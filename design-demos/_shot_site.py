#!/usr/bin/env python3
"""Screenshot the built site's key pages at desktop width, light + dark."""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/pagesim")
SHOTS = Path("/tmp/site_shots")
SHOTS.mkdir(exist_ok=True)
BASE = "http://127.0.0.1:8124"

PAGES = [
    ("home", "/index.html"),
    ("search", "/search/index.html?q=劳动合同"),
    ("catalog", "/catalog/index.html"),
    ("region", "/region/index.html"),
    ("law", "/law/7eeda5b12bc3/d881effa04404407922e8ce4a3d390cb.html"),
    ("exam", "/exam/index.html"),
]


def shoot(page, tag: str) -> None:
    errors = []
    page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
    for name, path in PAGES:
        page.goto(BASE + path, wait_until="networkidle")
        page.wait_for_timeout(350)
        page.screenshot(path=str(SHOTS / f"{name}-{tag}-top.png"))
        print("shot", name, tag)
    print("console errors:", errors[:10])


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        light = browser.new_page(viewport={"width": 1440, "height": 900},
                                device_scale_factor=1, color_scheme="light")
        shoot(light, "light")
        dark = browser.new_page(viewport={"width": 1440, "height": 900},
                               device_scale_factor=1, color_scheme="dark")
        shoot(dark, "dark")
        browser.close()


if __name__ == "__main__":
    main()
