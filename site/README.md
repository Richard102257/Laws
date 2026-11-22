# 静态查询页（site/）

把仓库里的法规数据渲染成**纯静态**的查询网站，可直接发布到 GitHub Pages：
<https://richard102257.github.io/Laws/>

- 只用 Python 标准库，不需要 Hugo、Node 或任何第三方包
- 数据来源：仓库根目录的 `db.sqlite3` 与 `DLC/*/db.sqlite3`
- 输出：首页、分类树、每部法规的详情页、法考汇编页、客户端搜索索引、`sitemap.xml`

## 构建

```bash
# 本地预览（链接为根相对路径）
python3 site/build_site.py --out /tmp/laws-site

# GitHub 项目页（站内链接带 /Laws 前缀）
python3 site/build_site.py --out public --base /Laws
```

默认输出到 `site/public/`（已加入 `.gitignore`）。用任意静态服务器在产物目录启动即可预览，例如：

```bash
cd /tmp/laws-site && python3 -m http.server 8080
```

## 文件说明

| 文件 | 作用 |
| --- | --- |
| `build_site.py` | 生成器主程序：读库、算状态/版本谱系/相关法规、渲染全部页面 |
| `markdownlite.py` | 法规 Markdown → HTML（标题目录、条标锚点、表格、列表等） |
| `assets/style.css` | 浅色/深色主题、响应式与打印样式 |
| `assets/app.js` | 主题切换、关键词搜索、筛选、收藏、字号、打印、目录高亮 |

## URL 设计

法规名可能很长（个别超过 255 字节），且存在同名法规，所以详情页不用中文名做文件名：

- 详情页：`law/<sha1[:12]>/<法规UUID>.html`
- 分类页：`cat/<sha1[:12]>.html`

短 hash 只用于分组目录，UUID 保证跨数据包唯一；构建结果稳定，重复构建不会改变已有链接。

## 测试

```bash
.venv/bin/python -m pytest scripts/tests/test_site.py
```
