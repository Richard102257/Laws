/* 法律快查 · 静态查询页交互：主题、搜索、筛选、收藏、目录与正文工具。 */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var base = document.body.getAttribute("data-base") || "";

  /* ---------- 主题 ---------- */

  function systemDark() {
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches;
  }

  function applyTheme(theme) {
    var dark = theme === "dark" || (theme === "system" && systemDark());
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    var btn = $("[data-theme-toggle]");
    if (btn) btn.setAttribute("aria-label", dark ? "切换到浅色主题" : "切换到深色主题");
  }

  try {
    applyTheme(localStorage.getItem("laws_theme") || "system");
  } catch (e) { applyTheme("system"); }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-theme-toggle]");
    if (!btn) return;
    var current = document.documentElement.getAttribute("data-theme") === "dark";
    var next = current ? "light" : "dark";
    try { localStorage.setItem("laws_theme", next); } catch (e) {}
    applyTheme(next);
  });

  /* ---------- 搜索 ---------- */

  var searchInput = $("#search-input");
  var searchResults = $("#search-results");
  var indexPromise = null;

  function loadIndex() {
    if (!indexPromise) {
      indexPromise = fetch(base + "assets/search-index.json")
        .then(function (resp) { return resp.ok ? resp.json() : []; })
        .catch(function () { return []; });
    }
    return indexPromise;
  }

  function haystack(item) {
    return [item.n, item.s, item.c, item.g, item.l, item.k].filter(Boolean).join(" ").toLowerCase();
  }

  function score(item, q) {
    var name = (item.n || "").toLowerCase();
    if (name === q) return 1000;
    if (name.indexOf(q) === 0) return 700;
    if (name.indexOf(q) >= 0) return 400;
    var sub = (item.s || "").toLowerCase();
    if (sub.indexOf(q) >= 0) return 200;
    var cat = ((item.c || "") + (item.g || "")).toLowerCase();
    if (cat.indexOf(q) >= 0) return 80;
    return 10;
  }

  function renderResults(items, total) {
    if (!searchResults) return;
    var html = '<div class="search-meta">找到 ' + total + ' 条结果' +
      (items.length < total ? "，显示前 " + items.length + " 条" : "") + "</div>";
    html += '<div class="row-list">';
    items.forEach(function (item) {
      var status = item.st ? '<span class="badge badge--' + item.st + '">' + statusText(item.st) + "</span>" : "";
      html += '<a class="row" href="' + item.u + '">' +
        '<span class="dot-status ' + (item.st || "") + '"></span>' +
        '<span class="row-main"><span class="row-title">' + escapeHtml(item.n) + "</span>" +
        (item.c ? '<span class="row-sub">' + escapeHtml(item.c) + (item.p ? " · " + item.p : "") + "</span>" : "") +
        "</span>" + status + "</a>";
    });
    html += "</div>";
    searchResults.innerHTML = html;
    searchResults.classList.add("show");
  }

  function statusText(status) {
    return { current: "现行有效", pending: "尚未施行", revised: "已修订", abolished: "已废止", expired: "已失效" }[status] || status;
  }

  function escapeHtml(text) {
    return String(text == null ? "" : text)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  var searchTimer = null;
  function runSearch() {
    var q = (searchInput.value || "").trim().toLowerCase();
    if (!q) {
      searchResults.classList.remove("show");
      searchResults.innerHTML = "";
      return;
    }
    loadIndex().then(function (index) {
      var scored = [];
      for (var i = 0; i < index.length; i++) {
        var s = score(index[i], q);
        if (s > 0) scored.push([s, index[i]]);
      }
      scored.sort(function (a, b) { return b[0] - a[0] || (a[1].n || "").localeCompare(b[1].n || "", "zh"); });
      var items = scored.slice(0, 60).map(function (pair) { return pair[1]; });
      renderResults(items, scored.length);
    });
  }

  if (searchInput) {
    searchInput.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(runSearch, 120);
    });
    searchInput.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { searchInput.value = ""; searchResults.classList.remove("show"); }
    });
    var preset = new URLSearchParams(window.location.search).get("q");
    if (preset) {
      searchInput.value = preset;
      runSearch();
    }
  }

  /* ---------- 通用文本筛选 ---------- */

  $$("[data-filter]").forEach(function (input) {
    input.addEventListener("input", function () {
      var scope = input.closest("[data-filter-scope]") || document;
      var q = input.value.trim().toLowerCase();
      $$("[data-filter-item]", scope).forEach(function (item) {
        var hit = !q || item.textContent.toLowerCase().indexOf(q) >= 0;
        item.classList.toggle("hidden", !hit);
      });
      $$("[data-filter-group]", scope).forEach(function (group) {
        var anyVisible = $$("[data-filter-item]", group).some(function (item) { return !item.classList.contains("hidden"); });
        group.classList.toggle("hidden", !anyVisible);
      });
    });
  });

  /* ---------- 状态筛选（列表页） ---------- */

  $$("[data-status-filter]").forEach(function (chip) {
    chip.addEventListener("click", function () {
      var scope = chip.closest("[data-status-scope]") || document;
      $$("[data-status-filter]", scope).forEach(function (other) { other.classList.remove("active"); });
      chip.classList.add("active");
      var status = chip.getAttribute("data-status-filter");
      $$("[data-status]", scope).forEach(function (row) {
        var show = !status || row.getAttribute("data-status") === status;
        row.classList.toggle("hidden", !show);
      });
    });
  });

  /* ---------- 收藏 ---------- */

  var FAV_KEY = "laws_favs";

  function readFavs() {
    try { return JSON.parse(localStorage.getItem(FAV_KEY)) || {}; } catch (e) { return {}; }
  }

  function writeFavs(favs) {
    try { localStorage.setItem(FAV_KEY, JSON.stringify(favs)); } catch (e) {}
  }

  function favKey() { return location.pathname.replace(/^\/Laws(?=\/)/i, ""); }

  function updateFavButton() {
    var btn = $("[data-fav]");
    if (!btn) return;
    var favs = readFavs();
    var key = favKey();
    var on = Object.prototype.hasOwnProperty.call(favs, key);
    btn.setAttribute("aria-pressed", on ? "true" : "false");
    var label = $("[data-fav-label]", btn);
    if (label) label.textContent = on ? "已收藏" : "收藏";
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-fav]");
    if (!btn) return;
    var favs = readFavs();
    var key = favKey();
    if (Object.prototype.hasOwnProperty.call(favs, key)) delete favs[key];
    else favs[key] = { n: btn.getAttribute("data-name") || document.title, p: btn.getAttribute("data-pub") || "" };
    writeFavs(favs);
    updateFavButton();
    renderFavPanel();
  });

  function renderFavPanel() {
    var panel = $("#fav-panel");
    if (!panel) return;
    var favs = readFavs();
    var keys = Object.keys(favs);
    var list = $("#fav-list");
    if (!keys.length) {
      panel.classList.remove("show");
      if (list) list.innerHTML = "";
      return;
    }
    panel.classList.add("show");
    list.innerHTML = keys.map(function (key) {
      var item = favs[key];
      return '<div class="fav-item"><a href="' + base + key + '">' + escapeHtml(item.n) + "</a>" +
        '<button class="fav-remove" type="button" data-fav-remove="' + escapeHtml(key) + '" aria-label="移除收藏">×</button></div>';
    }).join("");
  }

  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-fav-remove]");
    if (!btn) return;
    var favs = readFavs();
    delete favs[btn.getAttribute("data-fav-remove")];
    writeFavs(favs);
    renderFavPanel();
  });

  renderFavPanel();
  updateFavButton();

  /* ---------- 详情页工具 ---------- */

  var copyBtn = $("[data-copy]");
  if (copyBtn) {
    copyBtn.addEventListener("click", function () {
      var done = function () {
        var label = $("[data-copy-label]", copyBtn);
        if (!label) return;
        var old = label.textContent;
        label.textContent = "已复制";
        setTimeout(function () { label.textContent = old; }, 1500);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(location.href).then(done, done);
      } else {
        var tmp = document.createElement("textarea");
        tmp.value = location.href;
        document.body.appendChild(tmp);
        tmp.select();
        try { document.execCommand("copy"); } catch (e) {}
        document.body.removeChild(tmp);
        done();
      }
    });
  }

  var lawBody = $("#law-body");
  var FONT_SIZES = ["15px", "16.5px", "18px", "20px"];
  var fontLevel = 1;
  try { fontLevel = Math.min(3, Math.max(0, parseInt(localStorage.getItem("laws_font"), 10) || 1)); } catch (e) {}
  function applyFont() {
    if (lawBody) lawBody.style.fontSize = FONT_SIZES[fontLevel];
    $$("[data-font-step]").forEach(function (btn) {
      btn.classList.toggle("active", Number(btn.getAttribute("data-font-step")) === fontLevel);
    });
  }
  applyFont();
  document.addEventListener("click", function (event) {
    var btn = event.target.closest("[data-font-step]");
    if (!btn) return;
    var step = Number(btn.getAttribute("data-font-step"));
    fontLevel = step > 0 ? Math.min(3, fontLevel + 1) : Math.max(0, fontLevel - 1);
    try { localStorage.setItem("laws_font", String(fontLevel)); } catch (e) {}
    applyFont();
  });

  var printBtn = $("[data-print]");
  if (printBtn) printBtn.addEventListener("click", function () { window.print(); });

  /* ---------- 条标跳转 ---------- */

  var CN_DIGITS = { "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9 };
  var CN_UNITS = { "十": 10, "百": 100, "千": 1000 };

  function parseArticleNumber(text) {
    text = text.trim().replace(/^第/, "").replace(/条$/, "");
    if (/^\d+$/.test(text)) return parseInt(text, 10);
    if (!text) return NaN;
    var total = 0, section = 0, ok = false;
    for (var i = 0; i < text.length; i++) {
      var ch = text[i];
      if (ch in CN_DIGITS) { section = CN_DIGITS[ch]; ok = true; }
      else if (ch in CN_UNITS) {
        section = (section || 1) * CN_UNITS[ch];
        total += section; section = 0; ok = true;
      } else return NaN;
    }
    return ok ? total + section : NaN;
  }

  var jumpForm = $("[data-jump-form]");
  if (jumpForm) {
    jumpForm.addEventListener("submit", function (event) {
      event.preventDefault();
      var input = $("input", jumpForm);
      var msg = $("[data-jump-msg]");
      var num = parseArticleNumber(input.value || "");
      var articles = $$(".law-body .art");
      if (!num || num < 1 || num > articles.length) {
        if (msg) { msg.textContent = "没有找到这一条（共 " + articles.length + " 条）"; msg.hidden = false; }
        return;
      }
      if (msg) msg.hidden = true;
      var target = articles[num - 1];
      target.scrollIntoView({ behavior: "smooth", block: "start" });
      target.style.transition = "background-color .6s";
      target.style.backgroundColor = "var(--primary-soft)";
      setTimeout(function () { target.style.backgroundColor = ""; }, 900);
    });
  }

  /* ---------- 目录高亮 ---------- */

  var tocLinks = $$(".toc a[data-toc-link]");
  if (tocLinks.length && "IntersectionObserver" in window && lawBody) {
    var linkById = {};
    tocLinks.forEach(function (link) { linkById[link.getAttribute("href").slice(1)] = link; });
    var visible = new Set();
    var observer = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (entry.isIntersecting) visible.add(entry.target.id);
        else visible.delete(entry.target.id);
      });
      var first = null;
      visible.forEach(function (id) {
        var el = document.getElementById(id);
        if (el && (!first || el.offsetTop < first.offsetTop)) first = el;
      });
      tocLinks.forEach(function (link) { link.classList.remove("active"); });
      if (first && linkById[first.id]) linkById[first.id].classList.add("active");
    }, { rootMargin: "-80px 0px -70% 0px", threshold: 0 });
    $$(".law-body h2[id], .law-body h3[id]").forEach(function (heading) { observer.observe(heading); });
  }

  /* ---------- 返回顶部 ---------- */

  var backTop = $("[data-back-top]");
  if (backTop) {
    window.addEventListener("scroll", function () {
      backTop.classList.toggle("show", window.scrollY > 600);
    }, { passive: true });
    backTop.addEventListener("click", function () { window.scrollTo({ top: 0, behavior: "smooth" }); });
  }
})();
