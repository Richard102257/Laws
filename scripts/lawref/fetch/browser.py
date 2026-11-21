"""无头浏览器兜底：有的地方站正文靠 JS 渲染，或带 JS 挑战页，静态下载只能拿到空壳。

默认不依赖 playwright；需要时：

    pip install playwright && playwright install chromium

没装、或设了 LAWS_NO_BROWSER=1，render_html 返回 None，调用方按普通失败处理。
"""
import os

# 每次失败都启一次浏览器比较慢，先等页面网络基本空闲再取 DOM
WAIT = 2.0
TIMEOUT = 30.0


def installed() -> bool:
    """有没有装 playwright（浏览器二进制另说，真跑起来才知道）。"""
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False
    return True


async def render_html(url: str, headers: dict | None = None, wait: float = WAIT,
                      timeout: float = TIMEOUT) -> str | None:
    """用 Chromium 打开 url，返回渲染后的 HTML；失败返回 None。"""
    if os.environ.get("LAWS_NO_BROWSER", "").lower() in ("1", "true", "yes"):
        return None
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return None

    headers = headers or {}
    extra = {k: v for k, v in headers.items() if k.lower() != "user-agent"}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])
            try:
                context = await browser.new_context(user_agent=headers.get("User-Agent"),
                                                    extra_http_headers=extra)
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=int(timeout * 1000))
                await page.wait_for_timeout(int(wait * 1000))
                html = await page.content()
                return html or None
            finally:
                await browser.close()
    except Exception:
        return None
