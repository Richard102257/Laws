"""法规正文的轻量 Markdown 渲染，只用标准库。

法规正文用到的语法很少：标题、段落、有序/无序列表、引用、表格、分隔线、
加粗和链接。这里只实现这些，输出可直接放进模板的 HTML。
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass

_COMMENT = re.compile(r"<!--.*?-->", re.S)
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
_LIST_UL = re.compile(r"^[-*+]\s+(.*)$")
# 有序列表：只认「1.」后跟空白的形式。法律正文里大量出现「1.某种情形」这类
# 编号说明，没有空格，按普通段落处理更贴近原文。
_LIST_OL = re.compile(r"^\d{1,9}[.、]\s+(.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_HR = re.compile(r"^(?:-{3,}|\*{3,}|_{3,})\s*$")
_FENCE = re.compile(r"^```(.*)$")
_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?$")
_ARTICLE = re.compile(
    r"^(第[零一二三四五六七八九十百千两0-9]+条(?:之一|之二|之三)?)\s*[、.．:：]?\s*(.*)$",
    re.S,
)
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BOLD = re.compile(r"\*\*([^*]+)\*\*|__([^_]+)__")
_ITALIC = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)|(?<!_)_([^_\n]+)_(?!_)")
_URL = re.compile(r"(?<![\"'(\[])(https?://[^\s<>\[\]()\"']+)")
_CODE = re.compile(r"`([^`\n]+)`")


@dataclass
class Rendered:
    html: str
    toc: list[dict]
    articles: list[str]


def _escape(text: str, quote: bool = False) -> str:
    return html.escape(text, quote=quote)


def _slug(text: str, used: set[str]) -> str:
    value = re.sub(r"[^\w一-鿿]+", "-", text.strip().lower(), flags=re.UNICODE)
    value = value.strip("-") or "s"
    candidate = value
    index = 2
    while candidate in used:
        candidate = f"{value}-{index}"
        index += 1
    used.add(candidate)
    return candidate


def split_document(text: str) -> tuple[str, str, str]:
    """把法规 md 分成 (标题, 信息段, 正文)。

    正文以第一个一级标题为标题，到 `<!-- INFO END -->` 之间是信息段
    （通过、修正、公布机关等），其余是正文。没有信息标记时信息段为空。
    """
    marker = "<!-- INFO END -->"
    cut = text.find(marker)
    if cut >= 0:
        head, tail = text[:cut], text[cut + len(marker):]
    else:
        head, tail = text, ""

    head = _COMMENT.sub("", head).strip("\n")
    tail = _COMMENT.sub("", tail).strip("\n")

    lines = head.splitlines()
    title = ""
    rest = lines
    if lines and lines[0].lstrip().startswith("# "):
        title = lines[0].lstrip()[2:].strip()
        rest = lines[1:]

    if cut >= 0:
        info = "\n".join(rest).strip()
        body = tail.strip()
    else:
        # 没有信息标记：标题之后都算正文
        info = ""
        body = "\n".join(rest).strip()
    return title, info, body


def _inline(text: str) -> str:
    """行内渲染：先转义，再处理代码、链接、加粗、斜体、网址。"""
    out = _escape(text)

    parts = _CODE.split(out)
    for index in range(1, len(parts), 2):
        parts[index] = f"<code>{parts[index]}</code>"
    out = "".join(parts)

    def link(match: re.Match) -> str:
        label, href = match.group(1), match.group(2)
        return f'<a href="{_escape(href, quote=True)}">{label}</a>'

    out = _LINK.sub(link, out)

    def bold(match: re.Match) -> str:
        return f"<strong>{match.group(1) or match.group(2)}</strong>"

    out = _BOLD.sub(bold, out)

    def italic(match: re.Match) -> str:
        return f"<em>{match.group(1) or match.group(2)}</em>"

    out = _ITALIC.sub(italic, out)

    def url(match: re.Match) -> str:
        raw = match.group(1)
        trailing = ""
        while raw and raw[-1] in "。，、；”）).]":
            trailing = raw[-1] + trailing
            raw = raw[:-1]
        return (f'<a href="{_escape(raw, quote=True)}" rel="noopener">{raw}</a>'
                + _escape(trailing))

    out = _URL.sub(url, out)
    return out


def _split_row(line: str) -> list[str]:
    cells = line.strip().strip("|").split("|")
    return [cell.strip() for cell in cells]


def _is_block_start(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    return bool(
        _HEADING.match(stripped)
        or _LIST_UL.match(stripped)
        or _LIST_OL.match(stripped)
        or _QUOTE.match(stripped)
        or _HR.match(stripped)
        or _FENCE.match(stripped)
        or stripped.startswith("|")
    )


def _render_table(rows: list[list[str]]) -> str:
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    out = ['<table><thead><tr>']
    out += [f"<th>{_inline(cell)}</th>" for cell in header]
    out.append("</tr></thead><tbody>")
    for row in body:
        out.append("<tr>")
        out += [f"<td>{_inline(cell)}</td>" for cell in row]
        out.append("</tr>")
    out.append("</tbody></table>")
    return "".join(out)


def render(text: str) -> Rendered:
    """渲染 Markdown，返回 HTML、目录（二三级标题）和条标列表。"""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out: list[str] = []
    toc: list[dict] = []
    articles: list[str] = []
    used_ids: set[str] = set()
    index = 0
    total = len(lines)

    def emit_article(label: str, body: str) -> None:
        slug = _slug(label, used_ids)
        articles.append(label)
        out.append(
            f'<p class="art" id="{slug}"><a class="art-no" href="#{slug}">{_escape(label)}</a>'
            f" {_inline(body)}</p>"
        )

    while index < total:
        raw = lines[index]
        stripped = raw.strip()
        if not stripped:
            index += 1
            continue

        fence = _FENCE.match(stripped)
        if fence:
            lang = fence.group(1).strip()
            buffer: list[str] = []
            index += 1
            while index < total and not _FENCE.match(lines[index].strip()):
                buffer.append(lines[index])
                index += 1
            index += 1
            cls = f' class="language-{_escape(lang, quote=True)}"' if lang else ""
            out.append(f"<pre><code{cls}>{_escape(chr(10).join(buffer))}</code></pre>")
            continue

        heading = _HEADING.match(stripped)
        if heading:
            level = len(heading.group(1))
            text_value = heading.group(2).strip()
            slug = _slug(text_value, used_ids)
            out.append(f'<h{level} id="{slug}">{_inline(text_value)}</h{level}>')
            if 2 <= level <= 3:
                toc.append({"level": level, "id": slug, "text": text_value})
            index += 1
            continue

        if _HR.match(stripped):
            out.append("<hr>")
            index += 1
            continue

        if stripped.startswith("|"):
            if index + 1 < total and _TABLE_SEP.match(lines[index + 1].strip()):
                rows = [_split_row(stripped)]
                index += 2
                while index < total and lines[index].strip().startswith("|"):
                    rows.append(_split_row(lines[index]))
                    index += 1
                out.append(_render_table(rows))
                continue
            # 不是表格，按普通段落处理

        quote = _QUOTE.match(stripped)
        if quote:
            buffer = []
            while index < total and _QUOTE.match(lines[index].strip()):
                buffer.append(_QUOTE.match(lines[index].strip()).group(1))
                index += 1
            out.append(f"<blockquote>{_inline(' '.join(buffer))}</blockquote>")
            continue

        unordered = _LIST_UL.match(stripped)
        ordered = _LIST_OL.match(stripped)
        if unordered or ordered:
            tag = "ul" if unordered else "ol"
            items: list[str] = []
            while index < total:
                current = lines[index].strip()
                marker = _LIST_UL.match(current) or _LIST_OL.match(current)
                if marker:
                    items.append(marker.group(1))
                    index += 1
                    continue
                if not current:
                    # 列表后空行结束；连续空行后的同型列表仍接着
                    probe = index + 1
                    while probe < total and not lines[probe].strip():
                        probe += 1
                    if probe < total and (_LIST_UL.match(lines[probe].strip())
                                          or _LIST_OL.match(lines[probe].strip())):
                        index = probe
                        continue
                    break
                if _is_block_start(current):
                    break
                if items:
                    items[-1] += " " + current
                index += 1
            out.append(f"<{tag}>")
            out += [f"<li>{_inline(item)}</li>" for item in items]
            out.append(f"</{tag}>")
            continue

        # 普通段落：收集到下一个块级元素
        buffer = []
        while index < total:
            current = lines[index]
            stripped_current = current.strip()
            if not stripped_current:
                break
            if buffer and _is_block_start(stripped_current):
                break
            buffer.append(stripped_current)
            index += 1
        text_value = "\n".join(buffer)
        article = _ARTICLE.match(text_value)
        if article:
            emit_article(article.group(1), article.group(2).strip())
        else:
            out.append(f"<p>{_inline(text_value)}</p>")

    return Rendered("".join(out), toc, articles)
