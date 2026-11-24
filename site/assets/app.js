/* 法律快查 · 静态查询页交互：主题、搜索、筛选、收藏、目录与正文工具。 */
(function () {
  "use strict";

  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var base = document.body.getAttribute("data-base") || "";

  /* 静态资源路径：base 是 GitHub 项目页前缀（如 /Laws），本地预览为空 */
  function assetUrl(filename) {
    return (base ? base.replace(/\/+$/, "") : "") + "/assets/" + filename;
  }

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
      indexPromise = fetch(assetUrl("search-index.json"))
        .then(function (resp) { return resp.ok ? resp.json() : []; })
        .catch(function () { return []; });
    }
    return indexPromise;
  }

  function scoreTitle(item, q) {
    var name = (item.n || "").toLowerCase();
    if (name === q) return 1000;
    if (name.indexOf(q) === 0) return 700;
    if (name.indexOf(q) >= 0) return 400;
    var sub = (item.s || "").toLowerCase();
    if (sub.indexOf(q) >= 0) return 200;
    var cat = ((item.c || "") + (item.g || "")).toLowerCase();
    if (cat.indexOf(q) >= 0) return 80;
    return 0;
  }

  /* ---------- 全文索引：构建期生成的分片倒排，按需取 ---------- */

  var FULLTEXT_SHARDS = 16; // 与 site/build_site.py 的 FULLTEXT_SHARDS 一致
  var fulltextManifest = null;
  var fulltextShards = {};

  function loadFulltextManifest() {
    if (!fulltextManifest) {
      fulltextManifest = fetch(assetUrl("search-full/manifest.json"))
        .then(function (resp) { return resp.ok ? resp.json() : null; })
        .catch(function () { return null; });
    }
    return fulltextManifest;
  }

  function loadShard(number) {
    if (!fulltextShards[number]) {
      var name = "f" + (number < 10 ? "0" + number : number) + ".json";
      fulltextShards[number] = fetch(assetUrl("search-full/" + name))
        .then(function (resp) { return resp.ok ? resp.json() : {}; })
        .catch(function () { return {}; });
    }
    return fulltextShards[number];
  }

  /* 切词规则必须与构建期 fulltext_terms 一致：中文二元词 + 小写英文词 */
  function queryTerms(text) {
    var out = [], seen = {}, re = /[一-鿿]{2,}|[A-Za-z0-9]{2,}/g, match;
    while ((match = re.exec(text)) !== null) {
      var run = match[0], parts;
      if (run.charAt(0) >= "一" && run.charAt(0) <= "鿿") {
        parts = [];
        for (var i = 0; i < run.length - 1; i++) parts.push(run.slice(i, i + 2));
      } else {
        parts = [run.toLowerCase()];
      }
      for (var j = 0; j < parts.length; j++) {
        if (!seen[parts[j]]) { seen[parts[j]] = 1; out.push(parts[j]); }
      }
    }
    return out;
  }

  /* base64(delta varint) → 升序法规行号数组 */
  function decodePostings(encoded) {
    var bin = atob(encoded), out = new Uint32Array(bin.length), n = 0;
    var value = 0, factor = 1, previous = 0;
    for (var i = 0; i < bin.length; i++) {
      var byte = bin.charCodeAt(i);
      value += (byte & 0x7f) * factor;
      if (byte & 0x80) {
        factor *= 128;
      } else {
        previous += value;
        out[n++] = previous;
        value = 0;
        factor = 1;
      }
    }
    return out.subarray(0, n);
  }

  /* 并集打分：命中词越多、词越罕见，分越高。返回按分数降序的行号。 */
  function fulltextSearch(terms) {
    return loadFulltextManifest().then(function (manifest) {
      if (!manifest || !manifest.laws) return [];
      var wanted = {}, numbers = [];
      for (var i = 0; i < terms.length; i++) {
        var number = terms[i].charCodeAt(0) % FULLTEXT_SHARDS;
        if (!wanted[number]) { wanted[number] = 1; numbers.push(number); }
      }
      return Promise.all(numbers.map(loadShard)).then(function (shards) {
        var table = {};
        shards.forEach(function (shard) {
          Object.keys(shard).forEach(function (term) { table[term] = shard[term]; });
        });
        var hits = {};
        for (var t = 0; t < terms.length; t++) {
          var encoded = table[terms[t]];
          if (encoded == null) continue;
          var ids = decodePostings(encoded);
          var idf = Math.log((manifest.laws + 1) / (ids.length + 1));
          for (var k = 0; k < ids.length; k++) {
            var pos = ids[k];
            if (!hits[pos]) hits[pos] = { pos: pos, matched: 0, idf: 0 };
            hits[pos].matched += 1;
            hits[pos].idf += idf;
          }
        }
        var rows = Object.keys(hits).map(function (key) { return hits[key]; });
        rows.sort(function (a, b) { return b.matched - a.matched || b.idf - a.idf; });
        return rows;
      });
    });
  }

  var PAGE_SIZE = 60;
  var searchState = { scored: [], shown: 0 };

  function resultRows(rows) {
    return rows.map(function (row) {
      var item = row.item;
      var status = item.st ? '<span class="badge badge--' + item.st + '">' + statusText(item.st) + "</span>" : "";
      var sub = row.note ? escapeHtml(row.note)
        : (item.c ? escapeHtml(item.c) + (item.p ? " · " + escapeHtml(item.p) : "") : "");
      return '<a class="row" href="' + item.u + '">' +
        '<span class="dot-status ' + (item.st || "") + '"></span>' +
        '<span class="row-main"><span class="row-title">' + escapeHtml(item.n) + "</span>" +
        (sub ? '<span class="row-sub">' + sub + "</span>" : "") +
        "</span>" + status + "</a>";
    }).join("");
  }

  function renderPage(extra) {
    if (!searchResults) return;
    var total = searchState.scored.length;
    var rows = searchState.scored.slice(0, searchState.shown);
    var html = '<div class="search-meta">找到 ' + total + " 条结果" +
      ((searchState.mode || currentMode()) === "full" ? "（全文）" : "（标题）") +
      (total && rows.length < total ? "，显示前 " + rows.length + " 条" : "") +
      (extra ? " · " + escapeHtml(extra) : "") + "</div>";
    html += '<div class="row-list">' + resultRows(rows) + "</div>";
    if (rows.length < total) {
      html += '<button class="search-more" type="button" id="search-more">加载更多（还有 ' +
        (total - rows.length) + " 条）</button>";
    }
    searchResults.innerHTML = html;
    searchResults.classList.add("show");
  }

  function compareRows(a, b) {
    return (b.meta + b.fullNorm) - (a.meta + b.fullNorm) ||
      (a.item.n || "").localeCompare(b.item.n || "", "zh");
  }

  function setResults(rows, extra, mode) {
    var maxFull = 0;
    rows.forEach(function (row) { if (row.full > maxFull) maxFull = row.full; });
    rows.forEach(function (row) { row.fullNorm = maxFull ? (row.full / maxFull) * 600 : 0; });
    rows.sort(compareRows);
    searchState = { scored: rows, shown: Math.min(PAGE_SIZE, rows.length), mode: mode || "" };
    renderPage(extra || "");
  }

  function renderTitle(index, q, extra) {
    var rows = [];
    for (var i = 0; i < index.length; i++) {
      var meta = scoreTitle(index[i], q);
      if (meta > 0) rows.push({ item: index[i], meta: meta, full: 0, note: "" });
    }
    setResults(rows, extra || "", "title");
  }

  function renderFull(index, hits, q, terms, extra) {
    var rows = [];
    for (var i = 0; i < hits.length; i++) {
      var item = index[hits[i].pos];
      if (!item) continue;
      var meta = scoreTitle(item, q);
      var partial = hits[i].matched < terms.length
        ? "（命中 " + hits[i].matched + "/" + terms.length + " 词）" : "";
      rows.push({
        item: item,
        meta: meta,
        full: hits[i].idf * (0.5 + 0.5 * hits[i].matched / terms.length),
        note: meta > 0 ? "" : "正文命中" + partial,
      });
    }
    setResults(rows, extra || "", "full");
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
  // 搜索在独立页面里进行；首页只放搜索框，回车即跳转，不在原地下拉结果
  var isSearchPage = /\/search(\/index\.html?)?$/.test(location.pathname);

  var modeButtons = $$(".mode-btn");
  var modeNote = $("#search-mode-note");

  function currentMode() {
    var forced = new URLSearchParams(window.location.search).get("mode");
    if (forced === "title" || forced === "full") return forced;
    try { return localStorage.getItem("laws_search_mode") || "full"; } catch (e) { return "full"; }
  }

  function applyModeUI(mode) {
    modeButtons.forEach(function (btn) {
      var active = btn.getAttribute("data-mode") === mode;
      btn.classList.toggle("active", active);
      btn.setAttribute("aria-pressed", active ? "true" : "false");
    });
    if (modeNote) {
      modeNote.textContent = mode === "full"
        ? "全文模式：检索全部法规正文，按相关度排序"
        : "标题模式：只查名称、副标题与分类";
    }
  }

  function runSearch() {
    if (!isSearchPage) return;
    var raw = (searchInput.value || "").trim();
    var q = raw.toLowerCase();
    if (!q) {
      searchState = { scored: [], shown: 0 };
      searchResults.classList.remove("show");
      searchResults.innerHTML = "";
      return;
    }
    var mode = currentMode();
    applyModeUI(mode);
    if (mode === "title") {
      loadIndex().then(function (index) { renderTitle(index, q); });
      return;
    }
    var terms = queryTerms(raw);
    if (!terms.length) {
      loadIndex().then(function (index) {
        renderTitle(index, q, "关键词太短，已按标题检索");
      });
      return;
    }
    searchResults.classList.add("show");
    searchResults.innerHTML = '<div class="search-meta">正在检索正文…</div>';
    Promise.all([loadIndex(), fulltextSearch(terms)]).then(function (pair) {
      renderFull(pair[0], pair[1], q, terms);
    });
  }

  modeButtons.forEach(function (btn) {
    btn.addEventListener("click", function () {
      try { localStorage.setItem("laws_search_mode", btn.getAttribute("data-mode")); } catch (e) {}
      runSearch();
    });
  });

  if (searchResults) {
    searchResults.addEventListener("click", function (event) {
      var btn = event.target.closest("#search-more");
      if (!btn) return;
      searchState.shown = Math.min(searchState.shown + PAGE_SIZE, searchState.scored.length);
      renderPage();
    });
  }

  if (searchInput && isSearchPage) {
    searchInput.addEventListener("input", function () {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(runSearch, 120);
    });
    searchInput.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        searchInput.value = "";
        searchState = { scored: [], shown: 0 };
        searchResults.classList.remove("show");
      }
    });
    var preset = new URLSearchParams(window.location.search).get("q");
    if (preset) {
      searchInput.value = preset;
      runSearch();
    }
  }
  applyModeUI(currentMode());
  // 默认就是全文模式：先把 manifest（几十字节）取回来，输入时少一次往返
  if (currentMode() === "full") loadFulltextManifest();

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

  /* 条标索引：按条号或正文关键词过滤（只影响本区块） */
  $$("[data-article-filter]").forEach(function (input) {
    input.addEventListener("input", function () {
      var q = input.value.trim().toLowerCase();
      var items = $$(".art-index-list li");
      items.forEach(function (item) {
        var hay = (item.getAttribute("data-keywords") || item.textContent).toLowerCase();
        item.classList.toggle("hidden", !!q && hay.indexOf(q) < 0);
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

  /* 条标索引点击：锚点跳转后高亮对应条目 */
  document.addEventListener("click", function (event) {
    var link = event.target.closest("[data-article-link]");
    if (!link) return;
    var id = link.getAttribute("href").slice(1);
    var target = document.getElementById(id);
    if (!target) return;
    event.preventDefault();
    target.scrollIntoView({ behavior: "smooth", block: "start" });
    target.style.transition = "background-color .6s";
    target.style.backgroundColor = "var(--primary-soft)";
    setTimeout(function () { target.style.backgroundColor = ""; }, 900);
    if (history.replaceState) history.replaceState(null, "", "#" + id);
  });

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
