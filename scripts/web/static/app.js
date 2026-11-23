/* Laws 管理端：概览、内容库、采集任务、设置。数据来自仓库 db.sqlite3 与各 DLC。 */
"use strict";

const state = {
  user: null,
  view: "dashboard",
  theme: localStorage.getItem("laws_theme") || "system",
  accent: localStorage.getItem("laws_accent") || "red",
  stats: null,
  tasks: [],
  task: null,
  settings: null,
  users: [],
  taskDefs: { names: {}, fields: {} },
  taskKind: "download-local",
  catalog: [],
  catQuery: "",
  expandedPacks: { main: true },
  filterOptions: { levels: [], groups: [] },
  filters: {
    keyword: "", level: "", group: "", pack: "", category: "",
    date_from: "", date_to: "", sort: "publish_desc", page: 1, size: 20,
  },
  laws: { total: 0, items: [] },
  lawsLoading: false,
  taskStatusFilter: "all",
  law: null,
  exam: { overview: {}, items: [] },
  examLoading: false,
  examGroup: "all",
  examMatch: "all",
  examQuery: "",
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
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

/* ---------- 主题 ---------- */

function systemDark() {
  return matchMedia("(prefers-color-scheme: dark)").matches;
}

function applyTheme() {
  const dark = state.theme === "dark" || (state.theme === "system" && systemDark());
  document.documentElement.dataset.theme = dark ? "dark" : "light";
  document.documentElement.dataset.accent = state.accent;
  const themeSelect = $("#theme-select");
  const accentSelect = $("#accent-select");
  if (themeSelect) themeSelect.value = state.theme;
  if (accentSelect) accentSelect.value = state.accent;
}

function setTheme(value) {
  state.theme = value;
  localStorage.setItem("laws_theme", value);
  applyTheme();
}

function setAccent(value) {
  state.accent = value;
  localStorage.setItem("laws_accent", value);
  applyTheme();
}

try {
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    if (state.theme === "system") applyTheme();
  });
} catch (e) { /* 老浏览器忽略 */ }

/* ---------- 通用展示 ---------- */

function statusLabel(status) {
  return { pending: "排队中", running: "运行中", done: "完成", failed: "失败", canceled: "已取消" }[status] || status;
}

function taskRow(task, withResult = false) {
  const time = task.finished_at ? new Date(task.finished_at * 1000).toLocaleString()
    : task.started_at ? new Date(task.started_at * 1000).toLocaleString()
      : new Date(task.created_at * 1000).toLocaleString();
  let argv = "";
  try {
    const p = typeof task.params === "string" ? JSON.parse(task.params) : (task.params || {});
    if (Array.isArray(p.argv) && p.argv.length) argv = `<div class="argv">${esc(p.argv.join(" "))}</div>`;
  } catch (e) { /* 忽略无法解析的参数 */ }
  return `<tr data-action="open-task" data-id="${task.id}">
    <td>${task.id}</td>
    <td><div style="font-weight:600">${esc(task.name)}</div>${argv}</td>
    <td><span class="badge ${esc(task.status)}">${statusLabel(task.status)}</span></td>
    ${withResult ? `<td class="num">${task.returncode ?? ""}</td>` : ""}
    <td class="small muted">${time}</td>
    ${withResult ? `<td><a data-action="cancel-task" data-id="${task.id}">取消</a></td>` : ""}
  </tr>`;
}

function taskFilterChips() {
  const counts = {};
  for (const t of state.tasks) counts[t.status] = (counts[t.status] || 0) + 1;
  const defs = [["all", "全部"], ["running", "运行中"], ["pending", "排队中"],
    ["done", "完成"], ["failed", "失败"], ["canceled", "已取消"]];
  return defs.map(([key, label]) => {
    const n = key === "all" ? state.tasks.length : (counts[key] || 0);
    return `<button class="chip-filter ${state.taskStatusFilter === key ? "active" : ""}"
      data-action="task-filter" data-status="${key}">${label}<span class="cf-count">${n}</span></button>`;
  }).join("");
}

function visibleTasks() {
  if (state.taskStatusFilter === "all") return state.tasks;
  return state.tasks.filter((t) => t.status === state.taskStatusFilter);
}

function renderTaskList() {
  const body = $("#task-list-body");
  if (!body) return;
  body.innerHTML = visibleTasks().map((task) => taskRow(task, true)).join("")
    || '<tr><td colspan="6" class="empty">没有符合条件的任务</td></tr>';
  const chips = $("#task-chip-row");
  if (chips) chips.innerHTML = taskFilterChips();
}

function packLabels() {
  return Object.fromEntries(state.catalog.map((p) => [p.name, p.label]));
}

function packLabel(name) {
  return packLabels()[name] || (name === "main" ? "本体" : name);
}

function effectStatus(law) {
  const today = new Date().toISOString().slice(0, 10);
  const to = law.valid_to || "2099-12-31";
  if (to === "1970-01-01") return { text: "已失效（日期未知）", cls: "failed" };
  if (to !== "2099-12-31") return to <= today
    ? { text: "已失效", cls: "failed" } : { text: "现行有效", cls: "ok" };
  return { text: "现行有效", cls: "ok" };
}

function flagLabels(flags) {
  const labels = [];
  if (flags & 1) labels.push("修订历史");
  if (flags & 2) labels.push("含目录");
  if (flags & 4) labels.push("含信息块");
  return labels;
}

const REL_LABELS = { supersedes: "取代旧版", abolishes: "废止", amends: "修改", implements: "实施依据" };

/* ---------- 视图 ---------- */

const VIEW_TITLES = {
  dashboard: "概览",
  laws: "内容库",
  exam: "法考汇编",
  tasks: "采集任务",
  settings: "设置",
};

function shell(content) {
  const user = state.user;
  const nav = [
    ["dashboard", "概览"],
    ["laws", "内容库"],
    ["exam", "法考汇编"],
    ["tasks", "采集任务"],
    ["settings", "设置"],
  ];
  return `
    <div class="topbar">
      <div class="brand"><span class="brand-mark">⚖</span>Laws 管理端<small>LawRefBook/Laws</small></div>
      <nav class="nav">${nav.map(([key, label]) =>
        `<a data-action="nav" data-view="${key}" class="${state.view === key ? "active" : ""}">${label}</a>`).join("")}
      </nav>
      <div class="top-actions">
        <label class="theme-picker" title="主题"><span class="tp-icon">◐</span>
          <select id="theme-select" data-theme-select>
            <option value="system">跟随系统</option>
            <option value="light">浅色</option>
            <option value="dark">深色</option>
          </select>
        </label>
        <label class="theme-picker" title="强调色"><span class="tp-icon">◆</span>
          <select id="accent-select" data-accent-select>
            <option value="red">朱红</option>
            <option value="blue">蓝</option>
            <option value="green">绿</option>
            <option value="purple">紫</option>
          </select>
        </label>
      </div>
      <div class="userbox">
        <span class="avatar" title="${esc(user.username)}（${user.role}）">${esc((user.username || "?")[0].toUpperCase())}</span>
        <span class="uname">${esc(user.username)}<small>${user.role}</small></span>
        <a data-action="logout">退出</a>
      </div>
    </div>
    <div class="layout">
      <div class="page-head">
        <h2>${VIEW_TITLES[state.view] || ""}</h2>
        <span class="spacer"></span>
        <button class="btn sm ghost" data-action="refresh" title="刷新当前页数据">⟳ 刷新</button>
        <span class="faint small" id="page-extra"></span>
      </div>
      ${content}
    </div>
    <div id="drawer-root"></div>`;
}

function loginView() {
  return `
    <div class="login">
      <div class="login-brand"><span class="brand-mark">⚖</span></div>
      <h2>Laws 管理端</h2>
      <p>国家规章库采集与内容管理平台</p>
      <form data-form="login">
        <label class="field"><span>用户名</span><input name="username" required autofocus></label>
        <label class="field"><span>密码</span><input name="password" type="password" required></label>
        <button class="btn primary" type="submit">登录</button>
      </form>
      <p class="small faint">默认账号 admin / admin123，可在环境变量 LAWS_ADMIN_PASSWORD 修改初始密码。</p>
    </div>`;
}

function dashboardView() {
  const s = state.stats || {};
  const byLevel = Object.entries(s.by_level || {}).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...byLevel.map((x) => x[1]));
  const total = s.total ?? 0;
  const cards = [
    { icon: "📚", label: "法规总数", num: total, sub: `覆盖 ${byLevel.length} 个层级`, accent: "red" },
    { icon: "🗂️", label: "已找到文件", num: s.ok ?? 0, sub: `缺失 ${s.missing ?? 0}`, accent: "green" },
    { icon: "🗃️", label: "数据包", num: s.packs ?? 0, sub: `本体 + ${Math.max((s.packs ?? 1) - 1, 0)} 个 DLC`, accent: "blue" },
    { icon: "⏳", label: "待整理", num: s.pending_out ?? 0, sub: "__cache__/out 等待 organize", accent: "amber" },
    { icon: "🧪", label: "采集任务", num: state.tasks.length, sub: "累计任务数", accent: "purple" },
  ];
  const provinces = state.catalog
    .filter((p) => p.name !== "main" && p.col)
    .sort((a, b) => (a.row - b.row) || (a.col - b.col));
  return `
    <div class="quick-actions">
      <span class="qa-label">快捷操作</span>
      <button class="btn primary sm" data-action="go-tasks">＋ 新建采集任务</button>
      <button class="btn sm" data-action="quick-task" data-kind="organize">整理入仓</button>
      <button class="btn sm" data-action="quick-task" data-kind="db-update">更新数据库</button>
      <span class="spacer"></span>
      <span class="faint small">数据实时来自仓库 db.sqlite3 与各 DLC</span>
    </div>
    <div class="grid cards">
      ${cards.map((c) => `
        <div class="card">
          <div class="label"><span class="card-icon">${c.icon}</span>${c.label}</div>
          <div class="num">${c.num}</div>
          <div class="sub">${esc(c.sub)}</div>
        </div>`).join("")}
    </div>
    <div class="dash-grid">
      <div class="panel">
        <h3>按层级分布
          <span class="spacer"></span>
          <a class="small" data-action="go-laws">进入内容库 →</a>
        </h3>
        <div class="body"><div class="level-bars">
          ${byLevel.map(([level, count]) => `
            <div class="level-bar" data-action="quick-level" data-level="${esc(level)}" title="查看${esc(level)}（${count} 条）">
              <span class="name">${esc(level)}</span>
              <span class="track"><span class="fill" style="width:${(count / max * 100).toFixed(1)}%"></span></span>
              <span class="count">${count}<small>${total ? ((count / total) * 100).toFixed(1) : 0}%</small></span>
            </div>`).join("") || '<div class="empty">暂无数据</div>'}
        </div></div>
      </div>
      <div class="panel">
        <h3>地方规章覆盖
          <span class="spacer"></span>
          <a class="small" data-action="go-laws">全部 DLC →</a>
        </h3>
        <div class="body">
          ${provinces.length ? `<div class="map-grid">${provinces.map((p) => `
            <button type="button" class="prov ${p.empty ? "prov-empty" : ""}" style="grid-column:${p.col};grid-row:${p.row}"
              ${p.empty ? "" : `data-action="pick-pack-quick" data-pack="${esc(p.name)}"`}
              title="${esc(p.label)}：${p.empty ? "暂未采集" : `${p.total} 条（点击只看这个地区）`}">
              <span class="pname">${esc(p.province)}</span>
              <span class="pcount">${p.empty ? "待采集" : p.total}</span>
            </button>`).join("")}</div>
            <div class="small faint map-note">地图为方位示意，不代表实际面积与边界</div>`
          : '<div class="empty">还没有 DLC 数据</div>'}
          <div class="small faint" style="margin-top:12px">共 ${provinces.length} 个省级行政区，已收录 ${provinces.filter((p) => !p.empty).length} 个，最新公布日 ${esc(state.catalog[0]?.latest || "—")}</div>
        </div>
      </div>
    </div>
    <div class="panel">
      <h3>最近任务
        <span class="spacer"></span>
        <a class="small" data-action="go-tasks">全部任务 →</a>
      </h3>
      <div class="body flush"><table><thead><tr><th>#</th><th>任务</th><th>状态</th><th>时间</th></tr></thead>
      <tbody>${state.tasks.slice(0, 5).map(taskRow).join("") || '<tr><td colspan="4" class="empty">还没有任务</td></tr>'}
      </tbody></table></div>
    </div>`;
}

/* ---------- 内容库 ---------- */

function catNodes(categories) {
  const root = new Map();
  for (const item of categories) {
    const parts = (item.folder || "").split("/").filter(Boolean);
    let level = root;
    let path = "";
    for (const part of parts) {
      path = path ? `${path}/${part}` : part;
      if (!level.has(part)) level.set(part, { name: part, folder: path, count: 0, children: new Map() });
      const node = level.get(part);
      node.count += item.n || 0;
      level = node.children;
    }
  }
  return [...root.values()];
}

function renderCatNodes(nodes, packName, depth = 0) {
  return nodes.map((node) => {
    const active = state.filters.pack === packName && state.filters.category === node.folder;
    const kids = [...node.children.values()];
    return `
      <li class="depth-${Math.min(depth, 2)} ${active ? "active" : ""}"
          data-action="pick-cat" data-pack="${esc(packName)}" data-cat="${esc(node.folder)}">
        <span>${esc(node.name)}</span><span class="count">${node.count}</span>
      </li>
      ${kids.length ? `<ul class="cat-list">${renderCatNodes(kids, packName, depth + 1)}</ul>` : ""}`;
  }).join("");
}

function catalogTree() {
  const q = (state.catQuery || "").trim().toLowerCase();
  return state.catalog.filter((pack) => !pack.empty).map((pack) => {
    const cats = pack.categories.filter((c) => !q || (c.folder || "").toLowerCase().includes(q));
    const expanded = state.expandedPacks[pack.name] !== false;
    const activePack = state.filters.pack === pack.name && !state.filters.category;
    return `<div class="pack-block">
      <div class="pack-row ${activePack ? "active" : ""}" data-action="pick-pack" data-pack="${esc(pack.name)}">
        <span class="tw">${expanded ? "▾" : "▸"}</span>
        <span>${esc(pack.label)}</span>
        <span class="count">${pack.total}</span>
      </div>
      ${expanded ? `<ul class="cat-list">${renderCatNodes(catNodes(cats), pack.name)}</ul>` : ""}
    </div>`;
  }).join("") || '<div class="empty">没有数据包</div>';
}

function activeChips() {
  const f = state.filters;
  const chips = [];
  if (f.pack) chips.push(`<span class="chip">数据包：${esc(packLabel(f.pack))}<a data-action="clear-filter" data-key="pack">×</a></span>`);
  if (f.category) chips.push(`<span class="chip">分类：${esc(f.category)}<a data-action="clear-filter" data-key="category">×</a></span>`);
  if (f.level) chips.push(`<span class="chip">层级：${esc(f.level)}<a data-action="clear-filter" data-key="level">×</a></span>`);
  if (f.group) chips.push(`<span class="chip">分组：${esc(f.group)}<a data-action="clear-filter" data-key="group">×</a></span>`);
  if (f.date_from) chips.push(`<span class="chip">自 ${esc(f.date_from)}<a data-action="clear-filter" data-key="date_from">×</a></span>`);
  if (f.date_to) chips.push(`<span class="chip">至 ${esc(f.date_to)}<a data-action="clear-filter" data-key="date_to">×</a></span>`);
  if (f.keyword) chips.push(`<span class="chip">关键词：${esc(f.keyword)}<a data-action="clear-filter" data-key="keyword">×</a></span>`);
  return chips.join("");
}

function scopeLabel() {
  const f = state.filters;
  const parts = [];
  if (f.pack) parts.push(packLabel(f.pack));
  if (f.category) parts.push(f.category);
  if (f.level) parts.push(f.level);
  if (f.group) parts.push(`分组 ${f.group}`);
  return parts.length ? `范围：${parts.join(" / ")}` : "范围：全部内容";
}

function sortMark(sort, prefix) {
  if (sort === `${prefix}_asc`) return '<span class="sort-arrow">↑</span>';
  if (sort === `${prefix}_desc`) return '<span class="sort-arrow">↓</span>';
  return '<span class="sort-arrow faint">↕</span>';
}

function lawsView() {
  const f = state.filters;
  const sortOptions = [
    ["publish_desc", "公布日 ↓"],
    ["publish_asc", "公布日 ↑"],
    ["name_asc", "名称 A→Z"],
    ["name_desc", "名称 Z→A"],
  ];
  return `
    <div class="content-layout">
      <aside class="catalog">
        <div class="catalog-head"><strong>采集内容</strong>
          <div class="small faint">本体 + ${state.catalog.filter((p) => p.name !== "main" && !p.empty).length} 个 DLC</div></div>
        <div class="catalog-search"><input id="cat-search" placeholder="搜索分类…" value="${esc(state.catQuery)}"></div>
        <div class="catalog-tree" id="catalog-tree">${catalogTree()}</div>
      </aside>
      <div class="content-main">
        <div class="panel">
          <h3>筛选</h3>
          <div class="body">
            <div class="filter-bar">
              <input id="f-keyword" placeholder="名称 / 副标题 / 标签 / 分类" value="${esc(f.keyword)}">
              <select id="f-level"><option value="">全部层级</option>
                ${state.filterOptions.levels.map((x) => `<option ${x === f.level ? "selected" : ""}>${esc(x)}</option>`).join("")}
              </select>
              <select id="f-group"><option value="">全部分组</option>
                ${state.filterOptions.groups.map((x) => `<option ${x === f.group ? "selected" : ""}>${esc(x || "（未分组）")}</option>`).join("")}
              </select>
              <select id="f-sort" data-autosubmit>
                ${sortOptions.map(([v, label]) => `<option value="${v}" ${v === f.sort ? "selected" : ""}>${label}</option>`).join("")}
              </select>
              <input id="f-from" type="date" value="${esc(f.date_from)}" title="公布日从">
              <input id="f-to" type="date" value="${esc(f.date_to)}" title="公布日至">
              <button class="btn primary" data-action="law-search">查询</button>
              <button class="btn" data-action="law-reset">重置</button>
            </div>
            <div class="active-filters">${activeChips()}</div>
          </div>
        </div>
        <div class="panel">
          <div class="result-meta">
            <span>共 <strong>${state.laws.total}</strong> 条</span>
            <span class="faint small">${esc(scopeLabel())}</span>
            <span class="spacer"></span>
            <label class="theme-picker">每页
              <select class="page-size" data-autosubmit id="f-size">
                ${[20, 50, 100].map((n) => `<option value="${n}" ${n === f.size ? "selected" : ""}>${n}</option>`).join("")}
              </select>
            </label>
          </div>
          <div class="table-wrap">
            <table><thead><tr>
              <th class="sortable" data-action="law-sort" data-sort="${f.sort === "name_asc" ? "name_desc" : "name_asc"}">名称 ${sortMark(f.sort, "name")}</th>
              <th>层级</th><th>分类</th>
              <th class="sortable" data-action="law-sort" data-sort="${f.sort === "publish_desc" ? "publish_asc" : "publish_desc"}">公布日 ${sortMark(f.sort, "publish")}</th>
              <th>效力</th>
              <th class="num">版本</th><th>数据包</th><th>文件</th>
            </tr></thead>
            <tbody>${state.lawsLoading
              ? '<tr><td colspan="8" class="empty"><span class="spinner"></span>正在查询…</td></tr>'
              : state.laws.items.map(lawRow).join("") || '<tr><td colspan="8" class="empty">没有匹配的内容</td></tr>'}
            </tbody></table>
          </div>
          <div class="pager">
            <span class="muted small">第 ${f.page} 页</span>
            <button class="btn sm" data-action="law-page" data-page="${f.page - 1}" ${f.page <= 1 ? "disabled" : ""}>上一页</button>
            <button class="btn sm" data-action="law-page" data-page="${f.page + 1}"
              ${f.page * f.size >= state.laws.total ? "disabled" : ""}>下一页</button>
          </div>
        </div>
      </div>
    </div>`;
}

function lawRow(law) {
  const effect = effectStatus(law);
  const tags = (law.tags || "").split(/[\/;,，、]/).map((t) => t.trim()).filter(Boolean);
  return `<tr data-action="open-law" data-pack="${esc(law.pack)}" data-id="${esc(law.id)}">
    <td>
      <div style="font-weight:600">${esc(law.name)}</div>
      ${law.subtitle ? `<div class="small faint">${esc(law.subtitle)}</div>` : ""}
      ${tags.length ? `<div style="margin-top:4px">${tags.slice(0, 4).map((t) => `<span class="tag">${esc(t)}</span>`).join("")}</div>` : ""}
    </td>
    <td><span class="badge level">${esc(law.level)}</span></td>
    <td class="small">${esc(law.category_name || "")}${law.group ? `<div class="faint">${esc(law.group)}</div>` : ""}</td>
    <td>${esc(law.publish || "")}</td>
    <td><span class="badge ${effect.cls}">${effect.text}</span></td>
    <td>${law.ver || 1}</td>
    <td class="small">${esc(packLabel(law.pack))}</td>
    <td>${law.md_path ? '<span class="status-dot ok"></span>有' : '<span class="status-dot missing"></span>缺'}</td>
  </tr>`;
}

/* ---------- 法考汇编 ---------- */

const EXAM_MATCH_LABELS = { auto: "自动匹配", manual: "手工指定", unmatched: "未匹配" };

function examMatchMode(entry) {
  if (entry.match_mode === "manual") return "manual";
  return entry.law_id ? "auto" : "unmatched";
}

function examFilteredGroups() {
  const q = (state.examQuery || "").trim().toLowerCase();
  const match = state.examMatch;
  return state.exam.items.map((group) => ({
    ...group,
    entries: group.entries.filter((entry) => {
      if (match !== "all" && examMatchMode(entry) !== match) return false;
      if (q && !entry.name.toLowerCase().includes(q)
          && !(entry.law && (entry.law.name || "").toLowerCase().includes(q))) return false;
      return true;
    }),
  })).filter((group) => state.examGroup === "all" || String(group.id) === String(state.examGroup));
}

function examRow(group, entry) {
  const mode = examMatchMode(entry);
  const law = entry.law;
  const clickable = Boolean(entry.law_id && law);
  return `<tr ${clickable ? `data-action="open-law" data-pack="${esc(law.pack)}" data-id="${esc(entry.law_id)}"` : ""}>
    <td>
      <div style="font-weight:600">${esc(entry.name)}</div>
      <div class="small faint">${esc(group.name)}</div>
    </td>
    <td>${law ? esc(law.name) : '<span class="faint">—</span>'}</td>
    <td>${law ? `<span class="badge level">${esc(law.level)}</span>` : ""}</td>
    <td>${law ? esc(law.publish || "") : ""}</td>
    <td><span class="badge ${mode}">${EXAM_MATCH_LABELS[mode]}</span></td>
  </tr>`;
}

function examVisibleTotal() {
  return examFilteredGroups().reduce((n, g) => n + g.entries.length, 0);
}

function examTbodyHtml() {
  if (state.examLoading) {
    return '<tr><td colspan="5" class="empty"><span class="spinner"></span>正在加载…</td></tr>';
  }
  const groups = examFilteredGroups();
  return groups.flatMap((g) => g.entries.map((e) => examRow(g, e))).join("")
    || '<tr><td colspan="5" class="empty">没有匹配的内容</td></tr>';
}

function updateExamRows() {
  const body = $("#exam-tbody");
  if (body) body.innerHTML = examTbodyHtml();
  const count = $("#exam-count");
  if (count) count.textContent = `${examVisibleTotal()} 条`;
}

function examView() {
  const o = state.exam.overview || {};
  const cards = [
    ["大分组", o.group_count ?? 0, `启用 ${o.enabled_group_count ?? 0} 组`],
    ["细分组（法规）", o.entry_count ?? 0, "对照觉晓法考汇编"],
    ["已匹配", o.matched_count ?? 0, `自动 ${o.auto_count ?? 0} · 手工 ${o.manual_count ?? 0}`],
    ["未匹配", o.unmatched_count ?? 0, "补数据后再同步"],
  ];
  return `
    <div class="panel">
      <h3>法考法律汇编
        <span class="spacer"></span>
        <button class="btn sm" data-action="exam-sync">⟳ 同步结构</button>
      </h3>
      <div class="body">
        <div class="exam-stat-grid">${cards.map(([k, v, sub]) => `
          <div class="exam-stat"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${esc(sub)}</div></div>`).join("")}
        </div>
        <div class="small faint" style="margin-top:10px">
          8 个大分组、267 个细分组（法规名），仅结构不含正文；名称按归一化规则匹配本地法规库。
        </div>
      </div>
    </div>
    <div class="exam-layout">
      <aside class="panel exam-groups">
        <h3>大分组</h3>
        <div class="body flush"><ul class="exam-group-list">
          <li class="${state.examGroup === "all" ? "active" : ""}" data-action="pick-exam-group" data-id="all">
            <span>全部</span><span class="count">${o.entry_count ?? 0}</span></li>
          ${state.exam.items.map((g) => `
            <li class="${String(state.examGroup) === String(g.id) ? "active" : ""}"
                data-action="pick-exam-group" data-id="${g.id}" title="${esc(g.name)}">
              <span>${esc(g.name)}</span><span class="count">${g.matched_count}/${g.entry_count}</span>
            </li>`).join("")}
        </ul></div>
      </aside>
      <div class="panel exam-main">
        <div class="result-meta">
          <input id="exam-q" placeholder="搜索法规名称…" value="${esc(state.examQuery)}">
          <select id="exam-match" data-autosubmit>
            <option value="all" ${state.examMatch === "all" ? "selected" : ""}>全部匹配状态</option>
            <option value="auto" ${state.examMatch === "auto" ? "selected" : ""}>已匹配（自动）</option>
            <option value="manual" ${state.examMatch === "manual" ? "selected" : ""}>手工指定</option>
            <option value="unmatched" ${state.examMatch === "unmatched" ? "selected" : ""}>未匹配</option>
          </select>
          <span class="spacer"></span>
          <span class="faint small" id="exam-count">${examVisibleTotal()} 条</span>
        </div>
        <div class="table-wrap"><table><thead><tr>
          <th>细分组（法规）</th><th>匹配到本地法规</th><th>层级</th><th>公布日</th><th>匹配</th>
        </tr></thead>
        <tbody id="exam-tbody">${examTbodyHtml()}</tbody></table></div>
      </div>
    </div>`;
}

async function loadExam() {
  state.examLoading = true;
  try {
    const [overview, tree] = await Promise.all([api("/api/exam/overview"), api("/api/exam/tree")]);
    state.exam.overview = overview;
    state.exam.items = tree.items;
  } finally {
    state.examLoading = false;
  }
}

async function startExamSync() {
  const result = await api("/api/tasks", {
    method: "POST", body: JSON.stringify({ kind: "exam-sync", limit: 0 }),
  });
  toast(`已创建同步任务 #${result.id}`);
  await loadTasks();
  for (let i = 0; i < 60; i += 1) {
    const task = await api(`/api/tasks/${result.id}`);
    if (task.status !== "pending" && task.status !== "running") {
      await loadExam();
      render();
      toast(task.status === "done" ? "法考汇编结构已同步" : `同步${statusLabel(task.status)}`,
        task.status !== "done");
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1000));
  }
  toast("同步仍在进行，可到「采集任务」查看", true);
}

/* ---------- 采集任务 / 设置 ---------- */

const TASK_FIELD_DEFS = {
  name: { label: "法规名称", type: "text" },
  url: { label: "页面链接（维基文库单页）", type: "url" },
  index: { label: "索引页名称（维基文库）", type: "text" },
  regions: { label: "地区（如 湖北 或 1-31，可多个）", type: "text", placeholder: "1-31" },
  department: { label: "部门（留空为全部）", type: "text" },
  provider: {
    label: "数据源", type: "select",
    options: [["flk", "国家法律法规数据库"], ["gov", "国家规章库"], ["wikisource", "维基文库"]],
  },
  folder: { label: "分类 / 类型", type: "text", placeholder: "行政法规" },
  title: { label: "标题（覆盖默认）", type: "text" },
  publish: { label: "公布日期", type: "date" },
  file_path: { label: "服务器文件路径", type: "text" },
  names: { label: "名单（每行一个名称）", type: "textarea", rows: 6 },
  delay: { label: "请求间隔（秒）", type: "number", value: 1, step: "0.1" },
  limit: { label: "限量（0 为不限）", type: "number", value: 0 },
  see_also: { label: "同时抓取「参见」页面（--see-also）", type: "checkbox" },
  apply: { label: "直接移入仓库（--apply）", type: "checkbox", checked: true },
};

const TASK_DESCS = {
  download: "从国家法律法规数据库按名称下载。",
  "download-all": "全量下载法律、行政法规、司法解释。",
  "download-list": "按名单批量逐个下载（先写服务器临时文件再传给 CLI）。",
  "download-url": "下载指定维基文库页面的当前版本。",
  "download-dlc": "按地区采集地方性法规。",
  "download-local": "按地区采集地方政府规章。",
  "download-gz": "按国家规章库采集部门规章。",
  "download-guide": "采集最高人民法院指导性案例。",
  "download-jc": "采集最高人民检察院指导性案例。",
  "download-case": "采集其他司法行政工作指导案例。",
  "download-versions": "抓取某部法规在维基文库的全部历史版本。",
  "download-wikisource": "按索引页（如分类页）批量采集维基文库内容。",
  "parse-file": "解析服务器上的本地文件并写入 __cache__/out/。",
  "organize": "把 __cache__/out/ 里的结果整理进仓库。",
  "db-update": "遍历并重写所有 DLC 的 db.sqlite3。",
  "exam-sync": "导入法考法律汇编的大分组与细分组，并按名称重新自动匹配本地法规。",
};

function taskKindOptions(selected) {
  const names = state.taskDefs.names || {};
  const order = Object.keys(names);
  if (!order.length) {
    return ["download-local", "download-gz", "organize", "db-update"]
      .map((k) => `<option value="${k}">${esc(k)}</option>`).join("");
  }
  return order.map((k) => `<option value="${k}" ${k === selected ? "selected" : ""}>${esc(names[k] || k)}</option>`).join("");
}

function taskFieldHtml(key) {
  const def = TASK_FIELD_DEFS[key] || { label: key, type: "text" };
  const valueAttr = def.value !== undefined ? ` value="${esc(def.value)}"` : "";
  let control = "";
  if (def.type === "textarea") {
    control = `<textarea name="${key}" rows="${def.rows || 4}" placeholder="${esc(def.placeholder || "")}"></textarea>`;
  } else if (def.type === "select") {
    control = `<select name="${key}">${(def.options || []).map(([v, label]) =>
      `<option value="${v}">${esc(label)}</option>`).join("")}</select>`;
  } else if (def.type === "checkbox") {
    control = `<label class="row"><input type="checkbox" name="${key}" ${def.checked ? "checked" : ""}
      style="width:auto"> ${esc(def.label)}</label>`;
    return `<label class="field tpl-field" data-tfield="${key}"><span>&nbsp;</span>${control}</label>`;
  } else {
    control = `<input name="${key}" type="${def.type || "text"}"${valueAttr}
      step="${esc(def.step || "any")}" placeholder="${esc(def.placeholder || "")}">`;
  }
  return `<label class="field tpl-field" data-tfield="${key}"><span>${esc(def.label)}</span>${control}</label>`;
}

function syncTaskFields() {
  const kind = ($("#task-kind") || {}).value || "";
  const allowed = new Set((state.taskDefs.fields && state.taskDefs.fields[kind]) || []);
  $$(".tpl-field").forEach((el) => el.classList.toggle("hidden", !allowed.has(el.dataset.tfield)));
  const desc = $("#task-desc");
  if (desc) desc.textContent = TASK_DESCS[kind] || "";
}

function tasksView() {
  const canEdit = ["admin", "editor"].includes(state.user.role);
  const kinds = Object.keys(state.taskDefs.names || {});
  const current = kinds.includes(state.taskKind) ? state.taskKind : (kinds[0] || "download-local");
  const fieldKeys = [
    "name", "url", "index", "regions", "department", "provider", "folder",
    "title", "publish", "file_path", "names", "delay", "limit", "see_also", "apply",
  ];
  return `
    <div class="panel">
      <h3>新建任务</h3>
      <div class="body">
        <form data-form="task">
          <div class="form-grid">
            <label class="field"><span>类型</span>
              <select name="kind" id="task-kind">${taskKindOptions(current)}</select>
            </label>
            <label class="field" style="grid-column:1/-1"><span>&nbsp;</span>
              <span class="small faint" id="task-desc">${esc(TASK_DESCS[current] || "")}</span></label>
            ${fieldKeys.map(taskFieldHtml).join("")}
          </div>
          <button class="btn primary" type="submit" ${canEdit ? "" : "disabled"}>开始</button>
          ${canEdit ? "" : '<p class="muted small">viewer 只能查看。</p>'}
        </form>
      </div>
    </div>
    <div class="panel">
      <h3>任务列表
        <span class="spacer"></span>
        <span class="small faint">同一时间只运行一个，可点任务查看实时日志</span>
      </h3>
      <div class="body flush">
        <div class="chip-row" id="task-chip-row">${taskFilterChips()}</div>
        <div class="table-wrap"><table><thead><tr><th>#</th><th>任务</th><th>状态</th><th>结果</th><th>时间</th><th></th></tr></thead>
        <tbody id="task-list-body">${visibleTasks().map((task) => taskRow(task, true)).join("") || '<tr><td colspan="6" class="empty">没有符合条件的任务</td></tr>'}
        </tbody></table></div>
      </div>
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

/* ---------- 渲染与数据加载 ---------- */

function render() {
  applyTheme();
  const app = $("#app");
  if (!state.user) {
    document.title = "登录 · Laws 管理端";
    app.innerHTML = loginView();
    return;
  }
  const views = { dashboard: dashboardView, laws: lawsView, exam: examView, tasks: tasksView, settings: settingsView };
  app.innerHTML = shell((views[state.view] || dashboardView)());
  document.title = `${VIEW_TITLES[state.view] || ""} · Laws 管理端`;
  if (state.view === "tasks") syncTaskFields();
}

async function loadDashboard() {
  const [stats, tasks] = await Promise.all([api("/api/stats"), api("/api/tasks")]);
  state.stats = stats;
  state.tasks = tasks.items;
  render();
}

async function loadCatalog() {
  const data = await api("/api/catalog");
  state.catalog = data.items;
}

async function loadLaws() {
  const f = state.filters;
  const query = new URLSearchParams({
    keyword: f.keyword, level: f.level, group: f.group, pack: f.pack, category: f.category,
    date_from: f.date_from, date_to: f.date_to, sort: f.sort, page: f.page, size: f.size,
  });
  state.lawsLoading = true;
  render();
  try {
    state.laws = await api(`/api/laws?${query}`);
  } finally {
    state.lawsLoading = false;
  }
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

async function showLogin() {
  state.user = null;
  render();
}

function readFilters() {
  state.filters.keyword = $("#f-keyword").value.trim();
  state.filters.level = $("#f-level").value;
  state.filters.group = $("#f-group").value;
  state.filters.date_from = $("#f-from").value;
  state.filters.date_to = $("#f-to").value;
}

async function searchLaws(page = 1) {
  readFilters();
  state.filters.page = page;
  await loadLaws();
  render();
}

/* ---------- 详情抽屉 ---------- */

let drawerSource = null;

function drawShell(title, body) {
  return `
    <div class="drawer-mask" data-action="close-drawer"></div>
    <div class="drawer open">
      <header><div class="spacer"><strong>${esc(title)}</strong></div>
        <a data-action="close-drawer">关闭 ✕</a></header>
      <div class="content">${body}</div>
    </div>`;
}

function closeDrawer() {
  if (drawerSource) drawerSource.close();
  drawerSource = null;
  $("#drawer-root").innerHTML = "";
}

function metaCell(k, v) {
  return `<div class="cell"><div class="k">${esc(k)}</div><div class="v">${v}</div></div>`;
}

function renderLawDrawer(law) {
  const effect = effectStatus(law);
  const flags = flagLabels(law.flags || 0);
  const relations = law.relations || { out: [], out_total: 0, in_counts: {} };
  const meta = [
    ["层级", `<span class="badge level">${esc(law.level)}</span>`],
    ["分类", esc(law.category_name || "—")],
    ["分组", esc(law.group || "—")],
    ["数据包", esc(packLabel(law.pack))],
    ["公布日", esc(law.publish || "—")],
    ["施行日", esc(law.valid_from || "—")],
    ["失效日", esc(law.valid_to === "2099-12-31" ? "现行有效" : (law.valid_to || "—"))],
    ["版本", law.ver || 1],
    ["文件名", esc(law.filename || "—")],
    ["状态", `<span class="badge ${effect.cls}">${effect.text}</span>`],
  ];
  if (flags.length) meta.push(["正文标记", flags.map((f) => `<span class="tag">${esc(f)}</span>`).join("")]);

  const inCounts = Object.entries(relations.in_counts || {}).map(([type, n]) =>
    `<span class="badge">${REL_LABELS[type] || type} × ${n}</span>`).join("");

  $("#drawer-root").innerHTML = drawShell(law.name, `
    <h2 class="detail-title">${esc(law.name)}</h2>
    ${law.subtitle ? `<p class="detail-sub">${esc(law.subtitle)}</p>` : ""}
    <div class="meta-grid">${meta.map(([k, v]) => metaCell(k, v)).join("")}</div>
    <div class="actions">
      <a class="btn primary" href="/api/laws/${encodeURIComponent(law.pack)}/${encodeURIComponent(law.id)}/download">下载 markdown</a>
      <button class="btn" data-action="copy-path" data-path="${esc(law.md_path || "")}">复制路径</button>
    </div>

    <div class="detail-section">
      <h4>关系（它指向的 ${relations.out_total} 条）</h4>
      <div class="rel-list">
        ${relations.out.map((r) => `
          <div class="rel-item" data-action="open-rel" data-pack="${esc(r.target.pack)}" data-id="${esc(r.target.id)}">
            <span class="rel-type">${REL_LABELS[r.type] || r.type}</span>
            <span class="rel-name">${esc(r.target.name)}</span>
            <span class="rel-date">${esc(r.target.publish || "")}${r.date ? ` · ${esc(r.date)}` : ""}</span>
          </div>`).join("") || '<div class="faint small">没有记录关系</div>'}
      </div>
      ${inCounts ? `<h4 style="margin-top:14px">被引用</h4><div class="in-counts">${inCounts}</div>` : ""}
    </div>

    <div class="detail-section">
      <h4>正文</h4>
      <pre class="md">${esc(law.content || "（文件缺失）")}</pre>
    </div>`);
}

async function openLaw(pack, id) {
  $("#drawer-root").innerHTML = drawShell("加载中…", '<div class="empty">正在读取详情…</div>');
  try {
    state.law = await api(`/api/laws/${encodeURIComponent(pack)}/${encodeURIComponent(id)}`);
    renderLawDrawer(state.law);
  } catch (error) {
    closeDrawer();
    toast(error.message, true);
  }
}

async function openRel(pack, id) {
  await openLaw(pack, id);
}

async function openTask(id) {
  const task = await api(`/api/tasks/${id}`);
  state.task = task;
  $("#drawer-root").innerHTML = drawShell(`任务 #${task.id} ${task.name}`, `
    <p><span class="badge ${esc(task.status)}">${statusLabel(task.status)}</span>
      <span class="muted small" style="margin-left:8px">${esc(task.kind || "")}</span></p>
    ${(task.params && typeof task.params === "string" ? (() => { try { const p = JSON.parse(task.params); return Array.isArray(p.argv) && p.argv.length ? `<div class="argv">${esc(p.argv.join(" "))}</div>` : ""; } catch (e) { return ""; } })() : "")}
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

/* ---------- 事件 ---------- */

document.addEventListener("click", async (event) => {
  const el = event.target.closest("[data-action]");
  if (!el) return;
  const action = el.dataset.action;
  try {
    if (action === "refresh") {
      if (state.view === "dashboard") {
        await loadDashboard();
      } else if (state.view === "laws") {
        await loadLaws();
        render();
      } else if (state.view === "exam") {
        await loadExam();
        render();
      } else if (state.view === "tasks") {
        await loadTasks();
        renderTaskList();
        toast("任务列表已刷新");
      } else if (state.view === "settings") {
        await loadSettings();
        render();
      }
    } else if (action === "nav") {
      state.view = el.dataset.view;
      if (state.view === "laws") {
        render();
        if (!state.catalog.length) await loadCatalog();
        await loadLaws();
      } else if (state.view === "tasks") {
        await loadTasks();
      } else if (state.view === "exam") {
        await loadExam();
      } else if (state.view === "settings") {
        await loadSettings();
      }
      render();
    } else if (action === "logout") {
      await api("/api/logout", { method: "POST" });
      state.user = null;
      render();
    } else if (action === "go-laws") {
      state.view = "laws";
      render();
      await loadLaws();
      render();
    } else if (action === "go-tasks") {
      state.view = "tasks";
      render();
      await loadTasks();
      render();
      $("#task-kind")?.scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (action === "quick-task") {
      state.taskKind = el.dataset.kind || "organize";
      state.view = "tasks";
      render();
      await loadTasks();
      render();
      $("#task-kind")?.scrollIntoView({ behavior: "smooth", block: "center" });
    } else if (action === "pick-pack-quick") {
      state.filters.pack = el.dataset.pack;
      state.filters.category = "";
      state.expandedPacks[el.dataset.pack] = true;
      state.view = "laws";
      render();
      await searchLaws(1);
    } else if (action === "law-sort") {
      state.filters.sort = el.dataset.sort;
      await searchLaws(1);
    } else if (action === "task-filter") {
      state.taskStatusFilter = el.dataset.status;
      renderTaskList();
    } else if (action === "quick-level") {
      state.view = "laws";
      state.filters = { ...state.filters, level: el.dataset.level, page: 1 };
      render();
      await loadLaws();
      render();
    } else if (action === "law-search") {
      await searchLaws(1);
    } else if (action === "law-reset") {
      state.filters = {
        keyword: "", level: "", group: "", pack: "", category: "",
        date_from: "", date_to: "", sort: "publish_desc", page: 1, size: 20,
      };
      await loadLaws();
      render();
    } else if (action === "law-page") {
      await searchLaws(Number(el.dataset.page));
    } else if (action === "pick-pack") {
      const pack = el.dataset.pack;
      if (state.filters.pack === pack && !state.filters.category) {
        state.expandedPacks[pack] = state.expandedPacks[pack] === false;
        render();
        return;
      }
      state.filters.pack = pack;
      state.filters.category = "";
      state.expandedPacks[pack] = true;
      await searchLaws(1);
    } else if (action === "pick-cat") {
      state.filters.pack = el.dataset.pack;
      state.filters.category = el.dataset.cat;
      state.expandedPacks[el.dataset.pack] = true;
      await searchLaws(1);
    } else if (action === "clear-filter") {
      const key = el.dataset.key;
      state.filters[key] = key === "pack" || key === "category" ? "" : "";
      if (key === "pack") state.filters.category = "";
      await searchLaws(1);
    } else if (action === "pick-exam-group") {
      state.examGroup = el.dataset.id;
      render();
    } else if (action === "exam-sync") {
      await startExamSync();
    } else if (action === "open-law") {
      await openLaw(el.dataset.pack, el.dataset.id);
    } else if (action === "open-rel") {
      await openRel(el.dataset.pack, el.dataset.id);
    } else if (action === "close-drawer") {
      closeDrawer();
    } else if (action === "copy-path") {
      await navigator.clipboard.writeText(el.dataset.path);
      toast("路径已复制");
    } else if (action === "open-task") {
      await openTask(Number(el.dataset.id));
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
  const target = event.target;
  if (target.matches("[data-theme-select]")) {
    setTheme(target.value);
    return;
  }
  if (target.matches("[data-accent-select]")) {
    setAccent(target.value);
    return;
  }
  if (state.view === "laws" && target.matches("[data-autosubmit]")) {
    if (target.id === "f-size") state.filters.size = Number(target.value);
    if (target.id === "f-sort") state.filters.sort = target.value;
    searchLaws(1);
    return;
  }
  if (state.view === "exam" && target.id === "exam-match") {
    state.examMatch = target.value;
    updateExamRows();
    return;
  }
  if (state.view === "tasks" && target.id === "task-kind") {
    state.taskKind = target.value;
    syncTaskFields();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.id === "cat-search") {
    state.catQuery = event.target.value;
    const tree = $("#catalog-tree");
    if (tree) tree.innerHTML = catalogTree();
  }
  if (event.target.id === "exam-q") {
    state.examQuery = event.target.value;
    updateExamRows();
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeDrawer();
    return;
  }
  if (event.key === "Enter" && event.target.id === "f-keyword") {
    event.preventDefault();
    searchLaws(1);
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
      const [options, taskDefs] = await Promise.all([api("/api/filters"), api("/api/task-defs")]);
      state.filterOptions = options;
      state.taskDefs = taskDefs;
      await loadCatalog();
      await loadDashboard();
    } else if (form.dataset.form === "task") {
      const raw = Object.fromEntries(new FormData(form).entries());
      const payload = { kind: raw.kind };
      for (const key of ["name", "url", "index", "regions", "department", "provider",
        "folder", "title", "publish", "file_path", "names"]) {
        if (raw[key] != null) payload[key] = String(raw[key]);
      }
      payload.limit = Number(raw.limit || 0);
      payload.delay = Number(raw.delay || 1.0);
      payload.see_also = raw.see_also === "on";
      payload.apply = raw.apply === "on";
      await api("/api/tasks", { method: "POST", body: JSON.stringify(payload) });
      toast("任务已创建");
      state.taskKind = raw.kind;
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

/* ---------- 启动 ---------- */

setInterval(async () => {
  if (!state.user) return;
  const active = state.tasks.some((t) => t.status === "running" || t.status === "pending");
  if (state.view !== "tasks") {
    if (active) await loadTasks();
    return;
  }
  await loadTasks();
  renderTaskList();
}, 3000);

(async function boot() {
  applyTheme();
  const loader = document.createElement("div");
  loader.className = "boot-loader";
  loader.innerHTML = '<span class="spinner"></span>';
  document.body.appendChild(loader);
  try {
    state.user = await api("/api/me");
    const [options, catalog, taskDefs] = await Promise.all([
      api("/api/filters"), api("/api/catalog"), api("/api/task-defs")]);
    state.filterOptions = options;
    state.catalog = catalog.items;
    state.taskDefs = taskDefs;
    await loadDashboard();
  } catch {
    showLogin();
  } finally {
    loader.remove();
  }
})();
