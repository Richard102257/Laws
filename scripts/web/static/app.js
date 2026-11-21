/* Laws 管理端：概览、法规库、采集任务、设置。数据来自仓库的 db.sqlite3 和 DLC。 */
"use strict";

const state = {
  user: null,
  view: "dashboard",
  stats: null,
  filters: { keyword: "", level: "", group: "", date_from: "", date_to: "", page: 1, size: 20 },
  filterOptions: { levels: [], groups: [] },
  laws: { total: 0, items: [] },
  tasks: [],
  task: null,
  settings: null,
  users: [],
  law: null,
};

const $ = (sel, root = document) => root.querySelector(sel);
const esc = (value) => String(value ?? "").replace(/[&<>"']/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) showLogin();
    throw new Error(data.detail || `请求失败（${response.status}）`);
  }
  return data;
}

function toast(message, isError = false) {
  const el = document.createElement("div");
  el.className = "toast" + (isError ? " error" : "");
  el.textContent = message;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3200);
}

/* ---------- 视图 ---------- */

function shell(content) {
  const user = state.user;
  const nav = [
    ["dashboard", "概览"],
    ["laws", "法规库"],
    ["tasks", "采集任务"],
    ["settings", "设置"],
  ];
  return `
    <div class="topbar">
      <div class="brand">Laws 管理端<small>LawRefBook/Laws</small></div>
      <nav class="nav">${nav.map(([key, label]) =>
        `<a data-action="nav" data-view="${key}" class="${state.view === key ? "active" : ""}">${label}</a>`).join("")}
      </nav>
      <div class="userbox">
        <span>${esc(user.username)}（${user.role}）</span>
        <a data-action="logout">退出</a>
      </div>
    </div>
    <div class="layout">${content}</div>
    <div id="drawer-root"></div>
    <div id="modal-root"></div>`;
}

function loginView() {
  return `
    <div class="login">
      <h2>Laws 管理端</h2>
      <p>默认账号 admin / admin123，可在环境变量 LAWS_ADMIN_PASSWORD 修改初始密码。</p>
      <form data-form="login">
        <label class="field"><span>用户名</span><input name="username" required></label>
        <label class="field"><span>密码</span><input name="password" type="password" required></label>
        <button class="btn primary" type="submit">登录</button>
      </form>
    </div>`;
}

function dashboardView() {
  const s = state.stats || {};
  const byLevel = Object.entries(s.by_level || {}).sort((a, b) => b[1] - a[1]);
  return `
    <div class="grid cards">
      <div class="card"><div class="label">法规总数</div><div class="num">${s.total ?? 0}</div></div>
      <div class="card"><div class="label">已找到文件</div><div class="num">${s.ok ?? 0}</div>
        <div class="sub">缺失 ${s.missing ?? 0}</div></div>
      <div class="card"><div class="label">数据包</div><div class="num">${s.packs ?? 0}</div>
        <div class="sub">本体 + ${Math.max((s.packs ?? 1) - 1, 0)} 个 DLC</div></div>
      <div class="card"><div class="label">待整理</div><div class="num">${s.pending_out ?? 0}</div>
        <div class="sub">__cache__/out 里等待 organize</div></div>
    </div>
    <div class="panel">
      <h3>按层级</h3>
      <div class="body"><table><tbody>
        ${byLevel.map(([level, count]) => `<tr><td>${esc(level)}</td><td class="num">${count}</td></tr>`).join("")}
      </tbody></table></div>
    </div>
    <div class="panel">
      <h3>最近任务</h3>
      <div class="body flush"><table><thead><tr><th>#</th><th>任务</th><th>状态</th><th>时间</th></tr></thead>
      <tbody>${state.tasks.slice(0, 5).map(taskRow).join("") || '<tr><td colspan="4" class="muted">还没有任务</td></tr>'}
      </tbody></table></div>
    </div>`;
}

function taskRow(task, withResult = false) {
  const time = task.finished_at ? new Date(task.finished_at * 1000).toLocaleString()
    : task.started_at ? new Date(task.started_at * 1000).toLocaleString()
      : new Date(task.created_at * 1000).toLocaleString();
  const result = withResult
    ? `<td>${task.returncode === null ? "" : task.returncode}</td>`
      + `<td><a data-action="cancel-task" data-id="${task.id}">取消</a></td>`
    : "";
  return `<tr data-action="open-task" data-id="${task.id}">
    <td>${task.id}</td><td>${esc(task.name)}</td>
    <td><span class="badge ${esc(task.status)}">${statusLabel(task.status)}</span></td>
    <td class="small muted">${time}</td>${result}</tr>`;
}

function statusLabel(status) {
  return { pending: "排队中", running: "运行中", done: "完成", failed: "失败", canceled: "已取消" }[status] || status;
}

function lawsView() {
  const f = state.filters;
  return `
    <div class="panel">
      <h3>筛选</h3>
      <div class="body">
        <div class="filter-bar">
          <input id="f-keyword" placeholder="名称 / 副标题 / 标签" value="${esc(f.keyword)}">
          <select id="f-level"><option value="">全部层级</option>
            ${state.filterOptions.levels.map((x) => `<option ${x === f.level ? "selected" : ""}>${esc(x)}</option>`).join("")}
          </select>
          <select id="f-group"><option value="">全部分组</option>
            ${state.filterOptions.groups.map((x) => `<option ${x === f.group ? "selected" : ""}>${esc(x)}</option>`).join("")}
          </select>
          <input id="f-from" type="date" value="${esc(f.date_from)}">
          <input id="f-to" type="date" value="${esc(f.date_to)}">
          <button class="btn" data-action="law-search">查询</button>
          <button class="btn" data-action="law-reset">重置</button>
        </div>
      </div>
    </div>
    <div class="panel">
      <h3>共 ${state.laws.total} 部</h3>
      <div class="body flush">
        <table><thead><tr><th>名称</th><th>层级</th><th>公布日</th><th>分组</th><th>路径</th></tr></thead>
        <tbody>${state.laws.items.map((law) => `
          <tr data-action="open-law" data-pack="${esc(law.pack)}" data-id="${esc(law.id)}">
            <td>${esc(law.name)}${law.subtitle ? `<div class="small muted">${esc(law.subtitle)}</div>` : ""}</td>
            <td>${esc(law.level)}</td>
            <td>${esc(law.publish || "")}</td>
            <td>${esc(law.group || "")}</td>
            <td class="small muted">${esc(law.md_path || "文件缺失")}</td>
          </tr>`).join("") || '<tr><td colspan="5" class="muted">没有匹配的法规</td></tr>'}
        </tbody></table>
      </div>
      <div class="pager">
        <span class="muted small">第 ${f.page} 页</span>
        <button class="btn sm" data-action="law-page" data-page="${f.page - 1}" ${f.page <= 1 ? "disabled" : ""}>上一页</button>
        <button class="btn sm" data-action="law-page" data-page="${f.page + 1}"
          ${f.page * f.size >= state.laws.total ? "disabled" : ""}>下一页</button>
      </div>
    </div>`;
}

function tasksView() {
  const canEdit = ["admin", "editor"].includes(state.user.role);
  return `
    <div class="panel">
      <h3>新建任务</h3>
      <div class="body">
        <form data-form="task">
          <div class="form-grid">
            <label class="field"><span>类型</span>
              <select name="kind">
                <option value="download-local">地方政府规章采集（download-local）</option>
                <option value="download-gz">部门规章采集（download-gz）</option>
                <option value="organize">整理入仓（organize）</option>
                <option value="db-update">更新数据库（db update）</option>
              </select>
            </label>
            <label class="field tpl-local"><span>地区（湖北 / 1-31，可多个）</span>
              <input name="regions" value="1-31"></label>
            <label class="field tpl-gz hidden"><span>部门（留空为全部）</span>
              <input name="department"></label>
            <label class="field"><span>限量（0 为不限）</span><input name="limit" type="number" min="0" value="0"></label>
            <label class="field tpl-organize"><span>&nbsp;</span>
              <label class="row"><input type="checkbox" name="apply" checked style="width:auto"> 直接移入仓库（--apply）</label>
            </label>
          </div>
          <button class="btn primary" type="submit" ${canEdit ? "" : "disabled"}>开始</button>
          ${canEdit ? "" : '<p class="muted small">viewer 只能查看。</p>'}
        </form>
      </div>
    </div>
    <div class="panel">
      <h3>任务列表</h3>
      <div class="body flush"><table><thead><tr><th>#</th><th>任务</th><th>状态</th><th>结果</th><th>时间</th><th></th></tr></thead>
      <tbody>${state.tasks.map((task) => taskRow(task, true)).join("") || '<tr><td colspan="6" class="muted">还没有任务</td></tr>'}
      </tbody></table></div>
    </div>`;
}

function settingsView() {
  const isAdmin = state.user.role === "admin";
  const s = state.settings || {};
  return `
    <div class="panel">
      <h3>采集设置</h3>
      <div class="body">
        <form data-form="settings">
          <div class="form-grid">
            <label class="field"><span>并发 (-j)</span><input name="jobs" value="${esc(s.jobs || "4")}"></label>
            <label class="field"><span>athenaAppKey（留空用代码里的默认值）</span>
              <input name="app_key" value="${esc(s.app_key || "")}"></label>
            <label class="field"><span>athenaAppName</span><input name="app_name" value="${esc(s.app_name || "")}"></label>
            <label class="field"><span>自动同步时间</span><input name="auto_sync_time" value="${esc(s.auto_sync_time || "03:30")}"></label>
            <label class="field"><span>&nbsp;</span>
              <label class="row"><input type="checkbox" name="auto_sync" ${s.auto_sync === "true" ? "checked" : ""} style="width:auto">
              每天定时增量采集地方政府规章</label>
            </label>
          </div>
          <button class="btn primary" type="submit" ${isAdmin ? "" : "disabled"}>保存</button>
        </form>
      </div>
    </div>
    <div class="panel">
      <h3>用户</h3>
      <div class="body">
        <form data-form="user" class="row">
          <input name="username" placeholder="用户名" style="max-width:160px">
          <input name="password" placeholder="密码" style="max-width:160px">
          <select name="role" style="max-width:120px"><option value="viewer">viewer</option>
            <option value="editor">editor</option><option value="admin">admin</option></select>
          <button class="btn blue" type="submit" ${isAdmin ? "" : "disabled"}>添加</button>
        </form>
      </div>
      <div class="body flush"><table><thead><tr><th>#</th><th>用户名</th><th>角色</th><th>操作</th></tr></thead>
      <tbody>${state.users.map((u) => `
        <tr><td>${u.id}</td><td>${esc(u.username)}</td><td>${esc(u.role)}</td>
        <td><a data-action="delete-user" data-id="${u.id}">删除</a></td></tr>`).join("")}
      </tbody></table></div>
    </div>`;
}

/* ---------- 渲染与事件 ---------- */

function render() {
  const app = $("#app");
  if (!state.user) {
    app.innerHTML = loginView();
    return;
  }
  const views = { dashboard: dashboardView, laws: lawsView, tasks: tasksView, settings: settingsView };
  app.innerHTML = shell((views[state.view] || dashboardView)());
  if (state.view === "laws") loadLaws();
  if (state.view === "tasks") loadTasks();
  if (state.view === "settings") loadSettings();
}

async function showLogin() {
  state.user = null;
  render();
}

async function loadDashboard() {
  const [stats, tasks] = await Promise.all([api("/api/stats"), api("/api/tasks")]);
  state.stats = stats;
  state.tasks = tasks.items;
  render();
}

async function loadLaws() {
  const f = state.filters;
  const query = new URLSearchParams({
    keyword: f.keyword, level: f.level, group: f.group,
    date_from: f.date_from, date_to: f.date_to, page: f.page, size: f.size,
  });
  state.laws = await api(`/api/laws?${query}`);
}

async function loadTasks() {
  const data = await api("/api/tasks");
  state.tasks = data.items;
}

async function loadSettings() {
  const [settings, users] = await Promise.all([api("/api/settings"), api("/api/users")]);
  state.settings = settings;
  state.users = users.items;
}

document.addEventListener("click", async (event) => {
  const el = event.target.closest("[data-action]");
  if (!el) return;
  const action = el.dataset.action;
  try {
    if (action === "nav") {
      state.view = el.dataset.view;
      render();
    } else if (action === "logout") {
      await api("/api/logout", { method: "POST" });
      state.user = null;
      render();
    } else if (action === "law-search" || action === "law-reset") {
      if (action === "law-reset") {
        state.filters = { keyword: "", level: "", group: "", date_from: "", date_to: "", page: 1, size: 20 };
        render();
        return;
      }
      state.filters.keyword = $("#f-keyword").value.trim();
      state.filters.level = $("#f-level").value;
      state.filters.group = $("#f-group").value;
      state.filters.date_from = $("#f-from").value;
      state.filters.date_to = $("#f-to").value;
      state.filters.page = 1;
      await loadLaws();
      render();
    } else if (action === "law-page") {
      state.filters.page = Number(el.dataset.page);
      await loadLaws();
      render();
    } else if (action === "open-law") {
      openLaw(el.dataset.pack, el.dataset.id);
    } else if (action === "close-drawer") {
      closeDrawer();
    } else if (action === "download-law") {
      window.location = `/api/laws/${encodeURIComponent(state.law.pack)}/${encodeURIComponent(state.law.id)}/download`;
    } else if (action === "open-task") {
      openTask(Number(el.dataset.id));
    } else if (action === "cancel-task") {
      const result = await api(`/api/tasks/${el.dataset.id}/cancel`, { method: "POST" });
      toast(result.ok ? "已取消" : result.detail || "无法取消");
      await loadTasks();
      render();
    } else if (action === "delete-user") {
      if (!confirm("确定删除这个用户？")) return;
      await api(`/api/users/${el.dataset.id}`, { method: "DELETE" });
      await loadSettings();
      render();
    }
  } catch (error) {
    toast(error.message, true);
  }
});

document.addEventListener("change", (event) => {
  if (event.target.name === "kind" && state.view === "tasks") {
    const kind = event.target.value;
    document.querySelectorAll(".tpl-local").forEach((x) => x.classList.toggle("hidden", kind !== "download-local"));
    document.querySelectorAll(".tpl-gz").forEach((x) => x.classList.toggle("hidden", kind !== "download-gz"));
    document.querySelectorAll(".tpl-organize").forEach((x) => x.classList.toggle("hidden", kind !== "organize"));
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target.closest("[data-form]");
  if (!form) return;
  event.preventDefault();
  const data = Object.fromEntries(new FormData(form).entries());
  try {
    if (form.dataset.form === "login") {
      state.user = await api("/api/login", { method: "POST", body: JSON.stringify(data) });
      await loadDashboard();
    } else if (form.dataset.form === "task") {
      const payload = {
        kind: data.kind,
        regions: data.regions || "",
        department: data.department || "",
        limit: Number(data.limit || 0),
        apply: data.apply === "on",
      };
      await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
      toast("任务已创建");
      await loadTasks();
      render();
    } else if (form.dataset.form === "settings") {
      const settings = {
        jobs: data.jobs, app_key: data.app_key, app_name: data.app_name,
        auto_sync: data.auto_sync === "on" ? "true" : "false", auto_sync_time: data.auto_sync_time,
      };
      for (const [key, value] of Object.entries(settings)) {
        await api("/api/settings", { method: "PUT", body: JSON.stringify({ key, value }) });
      }
      toast("设置已保存");
      await loadSettings();
      render();
    } else if (form.dataset.form === "user") {
      await api("/api/users", { method: "POST", body: JSON.stringify(data) });
      toast("用户已添加");
      await loadSettings();
      render();
    }
  } catch (error) {
    toast(error.message, true);
  }
});

/* ---------- 抽屉与日志 ---------- */

let drawerSource = null;

function drawShell(title, body) {
  return `
    <div class="drawer open">
      <header><div class="spacer"><strong>${esc(title)}</strong></div>
        <a data-action="close-drawer">关闭</a></header>
      <div class="content">${body}</div>
    </div>`;
}

function closeDrawer() {
  if (drawerSource) drawerSource.close();
  drawerSource = null;
  $("#drawer-root").innerHTML = "";
}

async function openLaw(pack, id) {
  state.law = await api(`/api/laws/${encodeURIComponent(pack)}/${encodeURIComponent(id)}`);
  $("#drawer-root").innerHTML = drawShell(state.law.name, `
    <p class="muted small">${esc(state.law.level)} · ${esc(state.law.publish || "")} ·
      ${esc(state.law.md_path || "文件缺失")}</p>
    <p><a data-action="download-law">下载 markdown</a></p>
    <pre class="md">${esc(state.law.content || "（文件缺失）")}</pre>`);
}

async function openTask(id) {
  const task = await api(`/api/tasks/${id}`);
  state.task = task;
  $("#drawer-root").innerHTML = drawShell(`任务 #${task.id} ${task.name}`, `
    <p><span class="badge ${esc(task.status)}">${statusLabel(task.status)}</span>
      <span class="muted small" style="margin-left:8px">${esc(JSON.stringify(task.params))}</span></p>
    <div class="log" id="task-log"></div>`);
  const logEl = $("#task-log");
  let last = 0;
  const append = (message) => {
    logEl.textContent += message + "\n";
    logEl.scrollTop = logEl.scrollHeight;
  };
  if (drawerSource) drawerSource.close();
  drawerSource = new EventSource(`/api/tasks/${id}/stream`);
  drawerSource.onmessage = (event) => append(JSON.parse(event.data).message);
  drawerSource.addEventListener("end", () => {
    append(`—— ${statusLabel((state.task || {}).status)} ——`);
    drawerSource.close();
    drawerSource = null;
  });
}

/* ---------- 启动 ---------- */

(async function boot() {
  try {
    state.user = await api("/api/me");
    const options = await api("/api/filters");
    state.filterOptions = options;
    await loadDashboard();
  } catch {
    showLogin();
  }
})();