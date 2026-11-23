#!/usr/bin/env python3
"""Build design-demos/dir-3.html by injecting the real China map SVG."""
from __future__ import annotations

import html
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAP_SRC = Path("/tmp/china_map_snippet.svg")
OUT = ROOT / "design-demos" / "dir-3.html"

PALETTE = ["#edf3fa", "#d7e7f8", "#aed3f1", "#7db3e6", "#3a83cd"]
EMPTY_FILL = "#eef0f3"

svg = MAP_SRC.read_text(encoding="utf-8")
pairs = re.findall(r'data-name="([^"]+)"\s+data-count="(\d+)"', svg)
counts = {name: int(n) for name, n in pairs}

values = sorted({n for n in counts.values() if n > 0})
cuts: list[int] = []
if len(values) >= 5:
    for i in range(1, 5):
        cuts.append(values[min(len(values) - 1, i * len(values) // 5)])


def bucket(n: int) -> int:
    for i, cut in enumerate(cuts):
        if n <= cut:
            return i
    return len(cuts)


legend: list[tuple[str, str]] = [("暂无数据", EMPTY_FILL)]
lower = 0
for i, color in enumerate(PALETTE):
    group = [v for v in values if bucket(v) == i]
    if not group:
        continue
    upper = cuts[i] if i < len(cuts) else None
    label = f"{lower + 1}–{upper}" if upper is not None else f"≥{lower + 1}"
    legend.append((label, color))
    if upper is not None:
        lower = upper

svg = re.sub(r"<a\b[^>]*>", "", svg)
svg = svg.replace("</a>", "")
svg = svg.replace('class="cn-map"', 'class="cn-map" id="map"')

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>方向三 · 深宫数据殿 — 法律快查</title>
<!--
  方向三「深宫数据殿」：秒数轮盘 web #18 Gallery Dark 的骨架，但换主角与光
  借的机制：深底负空间、单列居中、一件主角展品独占视野、图下 EXIF 式小字注脚。
  不借：纯黑 #0A0A0A 的空洞、金色裱框；这次的「展品」是站内真实中国地图，
        并与检索台并置——地图进首屏，不再让第一屏全黑。
  form 来自内容：法规覆盖是一个地理问题，地图本身值得被装进展框并与搜索对望。
-->
<style>
  :root{
    --bg:#0b1220; --panel:#101a2c; --panel-2:#0d1626; --ink:#eef1f8;
    --muted:#a7b2c8; --faint:#8390a8; --line:rgba(255,255,255,.13);
    --line-soft:rgba(255,255,255,.07);
    --accent:#7fb0ea; --accent-2:#2c6cb3;
    --green:#57b98a; --blue:#7fb0ea;
    --sans:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",
      "Microsoft YaHei","Segoe UI",sans-serif;
    --serif:"Songti SC","STSong","Noto Serif SC","Source Han Serif SC","SimSun",serif;
  }
  *{box-sizing:border-box}
  html{scroll-behavior:smooth}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
    font-size:15.5px;line-height:1.7;-webkit-font-smoothing:antialiased}
  a{color:inherit;text-decoration:none}
  /* 顶栏 */
  .bar{position:sticky;top:0;z-index:5;background:rgba(11,18,32,.86);
    backdrop-filter:blur(10px);border-bottom:1px solid var(--line-soft)}
  .bar-in{max-width:1280px;margin:0 auto;padding:0 40px;height:64px;display:flex;
    align-items:center;gap:34px}
  .wordmark{font-size:13px;letter-spacing:.4em;color:var(--ink)}
  .wordmark b{font-weight:600}
  .nav{display:flex;gap:26px;font-size:13px;color:var(--muted);letter-spacing:.1em}
  .nav a:hover{color:var(--ink)}
  .spacer{flex:1}
  .theme{width:34px;height:34px;border:1px solid var(--line);border-radius:50%;
    display:grid;place-items:center;color:var(--muted);font-size:13px;cursor:pointer;
    background:transparent}
  main{max-width:1280px;margin:0 auto;padding:0 40px}
  /* 首屏：检索台 + 展品 */
  .hero{display:grid;grid-template-columns:minmax(0,1.02fr) minmax(0,.98fr);
    gap:56px;align-items:center;padding:64px 0 40px}
  .kicker{font-size:12px;letter-spacing:.32em;color:var(--accent);margin:0 0 20px}
  h1{font-family:var(--serif);font-size:52px;line-height:1.18;margin:0 0 20px;
    font-weight:700;letter-spacing:.01em}
  .lead{font-size:16.5px;color:var(--muted);max-width:520px;margin:0 0 32px}
  .search{display:flex;align-items:center;gap:14px;border:1px solid var(--line);
    background:var(--panel);height:58px;padding:0 8px 0 18px;border-radius:3px}
  .search:focus-within{border-color:var(--accent-2)}
  .search input{flex:1;min-width:0;border:0;outline:0;background:transparent;
    color:var(--ink);font-size:17px}
  .search input::placeholder{color:var(--faint)}
  .search .go{border:0;background:var(--accent-2);color:#fff;font-size:13.5px;
    letter-spacing:.18em;padding:12px 22px;border-radius:2px;cursor:pointer;white-space:nowrap}
  .search .go:hover{background:#255d9c}
  .hot{display:flex;flex-wrap:wrap;gap:8px 18px;margin-top:16px;font-size:12.5px;
    color:var(--faint);letter-spacing:.04em}
  .hot b{font-weight:400;letter-spacing:.2em;font-size:11.5px}
  .hot a:hover{color:var(--accent)}
  /* 展品 */
  .frame{border:1px solid var(--line);background:
    radial-gradient(120% 100% at 50% 0%,rgba(127,176,234,.14),rgba(11,18,32,0) 62%),
    var(--panel-2);
    padding:34px 28px 24px;position:relative}
  .frame::before{content:"展品 01";position:absolute;top:12px;left:16px;font-size:11.5px;
    letter-spacing:.3em;color:var(--faint)}
  .cn-map{display:block;width:100%;height:auto;max-width:640px;margin:14px auto 0}
  .caption{margin-top:22px;padding-top:18px;border-top:1px solid var(--line-soft);
    display:flex;justify-content:space-between;gap:20px;align-items:flex-start}
  .caption .t{font-family:var(--serif);font-size:21px;font-weight:700}
  .caption .d{font-size:12.5px;color:var(--muted);margin-top:5px}
  .exif{font-size:12px;color:var(--faint);text-align:right;line-height:1.9;
    letter-spacing:.08em;white-space:nowrap}
  .legend{list-style:none;display:flex;flex-wrap:wrap;gap:5px 14px;margin:14px 0 0;
    padding:0;font-size:12px;color:var(--muted);letter-spacing:.03em}
  .legend li{display:flex;align-items:center;gap:6px}
  .legend .sw{width:10px;height:10px;border:1px solid rgba(255,255,255,.22)}
  /* 数字带 */
  .figures{padding:44px 0 0;border-top:1px solid var(--line-soft);margin-top:26px}
  .figures h2,.index h2,.feed h2{font-size:11.5px;letter-spacing:.3em;color:var(--faint);
    font-weight:400;margin:0 0 26px}
  .fig-grid{display:grid;grid-template-columns:repeat(6,1fr)}
  .fig{padding:0 20px;border-left:1px solid var(--line-soft)}
  .fig:first-child{border-left:0;padding-left:0}
  .fig .n{font-family:var(--serif);font-size:42px;font-weight:700;line-height:1.05;
    font-variant-numeric:tabular-nums}
  .fig .l{font-size:12px;color:var(--muted);margin-top:8px;letter-spacing:.14em}
  /* 入口索引 */
  .index{padding:64px 0 0}
  .idx-row{display:grid;grid-template-columns:64px minmax(0,1fr) auto;gap:24px;
    align-items:baseline;padding:20px 0;border-top:1px solid var(--line-soft)}
  .idx-row:last-of-type{border-bottom:1px solid var(--line-soft)}
  .idx-row .no{font-family:var(--serif);font-size:14px;color:var(--accent);letter-spacing:.06em}
  .idx-row .t{font-family:var(--serif);font-size:23px;font-weight:700}
  .idx-row .d{font-size:13.5px;color:var(--muted);margin-top:5px}
  .idx-row .c{font-size:12px;color:var(--faint);white-space:nowrap;letter-spacing:.05em}
  .idx-row:hover .t{color:var(--accent)}
  /* 动态 */
  .feed{padding:64px 0 0}
  .frow{display:grid;grid-template-columns:112px minmax(0,1fr) auto;gap:20px;
    align-items:baseline;padding:13px 0;border-top:1px solid var(--line-soft);
    font-size:14px}
  .frow:last-of-type{border-bottom:1px solid var(--line-soft)}
  .frow .dt{font-size:12px;color:var(--faint);letter-spacing:.06em}
  .frow .ti{font-weight:600}
  .frow .su{font-size:12px;color:var(--faint);margin-top:3px}
  .frow:hover .ti{color:var(--accent)}
  .dot{display:inline-flex;align-items:center;gap:7px;font-size:12px;color:var(--muted);
    white-space:nowrap}
  .dot i{width:6px;height:6px;border-radius:50%;background:var(--green)}
  .dot.pen i{background:var(--blue)}
  footer{border-top:1px solid var(--line-soft);margin-top:76px;padding:28px 0 56px;
    display:flex;justify-content:space-between;gap:20px;font-size:12px;color:var(--faint);
    letter-spacing:.08em}
  @media(max-width:1080px){
    .hero{grid-template-columns:1fr;gap:34px;padding:40px 0 30px}
    h1{font-size:38px}
    .fig-grid{grid-template-columns:repeat(3,1fr);row-gap:26px}
    .fig:nth-child(4){border-left:0;padding-left:0}
    .nav{display:none}
  }
  @media(max-width:680px){
    .bar-in,main{padding-left:22px;padding-right:22px}
    .hero{padding:32px 0 24px}
    h1{font-size:30px}
    .lead{font-size:15px}
    .search{height:auto;flex-wrap:wrap;padding:10px;gap:10px}
    .search input{min-height:44px;flex:1 1 100%}
    .search .go{padding:12px 18px}
    .frame{padding:22px 14px 16px}
    .caption{flex-direction:column}
    .exif{text-align:left;white-space:normal}
    .fig-grid{grid-template-columns:1fr 1fr}
    .fig{border-left:0;padding-left:0}
    .idx-row,.frow{grid-template-columns:1fr;gap:4px}
    .idx-row .c,.frow .dot{white-space:normal;justify-self:start}
    footer{flex-direction:column;gap:8px}
  }
</style>
</head>
<body>
<header class="bar">
  <div class="bar-in">
    <span class="wordmark"><b>法律</b>快查</span>
    <nav class="nav"><a href="#">分类</a><a href="#gallery">地方法规</a><a href="#feed">动态</a></nav>
    <div class="spacer"></div>
    <button class="theme" type="button" aria-label="切换主题">◐</button>
  </div>
</header>

<main>
  <section class="hero">
    <div class="pitch">
      <p class="kicker">公开法律法规数据库 · 每日更新</p>
      <h1>一万八千部法条，<br>各就其位</h1>
      <p class="lead">收录法律、行政法规、司法解释、部门规章、地方法规与指导案例。搜一句关键词，或按分类、按地区把它取出来。</p>
      <form class="search" role="search" action="#">
        <input type="search" placeholder="输入法规名称、关键词，如「劳动合同」「刑法」…" aria-label="搜索法规">
        <button class="go" type="submit">检索</button>
      </form>
      <div class="hot"><b>热门</b>
        <a href="#">劳动合同</a><a href="#">婚姻</a><a href="#">继承</a><a href="#">交通事故</a><a href="#">劳动争议</a><a href="#">工伤</a><a href="#">合同纠纷</a><a href="#">刑法</a><a href="#">物业管理</a><a href="#">消费者权益</a>
      </div>
    </div>

    <figure class="frame" id="gallery">
      __MAP__
      <figcaption class="caption">
        <div>
          <div class="t">中国省级地区覆盖图</div>
          <div class="d">颜色深浅对应各地方法规与规章的收录数量。</div>
          <ul class="legend">__LEGEND__</ul>
        </div>
        <div class="exif">
          阿尔伯斯等积圆锥投影<br>
          31 个省级地区 · 12,562 部<br>
          截至 2026-11-23
        </div>
      </figcaption>
    </figure>
  </section>

  <section class="figures">
    <h2>收录规模</h2>
    <div class="fig-grid">
      <div class="fig"><div class="n">355</div><div class="l">法律</div></div>
      <div class="fig"><div class="n">729</div><div class="l">行政法规</div></div>
      <div class="fig"><div class="n">509</div><div class="l">司法解释</div></div>
      <div class="fig"><div class="n">2,658</div><div class="l">部门规章</div></div>
      <div class="fig"><div class="n">12,562</div><div class="l">地方法规</div></div>
      <div class="fig"><div class="n">859</div><div class="l">指导案例</div></div>
    </div>
  </section>

  <section class="index">
    <h2>入口</h2>
    <a class="idx-row" href="#"><span class="no">01</span><span><span class="t">常用法规</span><div class="d">民法典、刑法、劳动合同法等十二部最高频直达</div></span><span class="c">12 部</span></a>
    <a class="idx-row" href="#"><span class="no">02</span><span><span class="t">地方法规</span><div class="d">各省级行政区法规与规章，地图速查</div></span><span class="c">12,562 部 · 31 个省级地区</span></a>
    <a class="idx-row" href="#"><span class="no">03</span><span><span class="t">指导案例</span><div class="d">最高法、最高检与司法行政指导案例，含案名</div></span><span class="c">859 部</span></a>
    <a class="idx-row" href="#"><span class="no">04</span><span><span class="t">法考汇编</span><div class="d">按法考科目整理的法规清单</div></span><span class="c">8 组 · 267 条</span></a>
    <a class="idx-row" href="#"><span class="no">05</span><span><span class="t">司法解释</span><div class="d">最高法、最高检司法解释与批复</div></span><span class="c">509 部</span></a>
  </section>

  <section class="feed" id="feed">
    <h2>近期动态</h2>
    <div class="frow"><span class="dt">2026-09-15</span><span><span class="ti">船舶及其有关作业活动污染海洋环境防治管理规定</span><div class="su">交通运输部 · 部门规章</div></span><span class="dot"><i></i>现行有效</span></div>
    <div class="frow"><span class="dt">2026-09-15</span><span><span class="ti">植物新品种保护条例实施细则（农业部分）</span><div class="su">农业农村部 · 部门规章</div></span><span class="dot"><i></i>现行有效</span></div>
    <div class="frow"><span class="dt">2026-09-11</span><span><span class="ti">北京市无人驾驶航空器管理规定</span><div class="su">北京 · 地方性法规</div></span><span class="dot"><i></i>现行有效</span></div>
    <div class="frow"><span class="dt">2026-09-10</span><span><span class="ti">海南省实施《行政复议法》办法</span><div class="su">海南 · 地方性法规</div></span><span class="dot"><i></i>现行有效</span></div>
    <div class="frow"><span class="dt">2026-09-03</span><span><span class="ti">河南省气象灾害预警信号发布与传播办法</span><div class="su">河南 · 地方性法规</div></span><span class="dot"><i></i>现行有效</span></div>
    <div class="frow"><span class="dt">2026-08-30</span><span><span class="ti">山东省安全生产风险管控办法</span><div class="su">山东 · 地方性法规</div></span><span class="dot"><i></i>现行有效</span></div>
    <div class="frow"><span class="dt">2026-08-28</span><span><span class="ti">律师法</span><div class="su">行政法 · 法律</div></span><span class="dot"><i></i>现行有效</span></div>
    <div class="frow"><span class="dt">2026-08-28</span><span><span class="ti">国防动员法</span><div class="su">行政法 · 法律</div></span><span class="dot"><i></i>现行有效</span></div>
    <div class="frow"><span class="dt">2026-09-19</span><span><span class="ti">审计法实施条例</span><div class="su">行政法规</div></span><span class="dot pen"><i></i>尚未施行</span></div>
    <div class="frow"><span class="dt">2026-10-17</span><span><span class="ti">事业单位登记管理条例</span><div class="su">行政法规</div></span><span class="dot pen"><i></i>尚未施行</span></div>
    <div class="frow"><span class="dt">2026-10-12</span><span><span class="ti">统计严重失信企业信用管理办法</span><div class="su">国家统计局 · 部门规章</div></span><span class="dot pen"><i></i>尚未施行</span></div>
    <div class="frow"><span class="dt">2026-10-15</span><span><span class="ti">对外投资管理办法</span><div class="su">国家发展和改革委员会 · 部门规章</div></span><span class="dot pen"><i></i>尚未施行</span></div>
  </section>
</main>

<footer>
  <span>法律快查 · 数据来自公开法律法规数据库</span>
  <span>更新：2026-11-23　·　<a href="#">源码仓库</a></span>
</footer>
</body>
</html>
"""

legend_html = "".join(
    f'<li><span class="sw" style="background:{color}"></span>{html.escape(label)}</li>'
    for label, color in legend
)

page = TEMPLATE.replace("__MAP__", svg).replace("__LEGEND__", legend_html)
OUT.write_text(page, encoding="utf-8")
print(f"wrote {OUT} ({OUT.stat().st_size/1024:.1f} KB)")
