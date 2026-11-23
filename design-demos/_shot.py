#!/usr/bin/env python3
"""Render the three direction mockups at desktop size for review."""
from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = Path(__file__).resolve().parent
SHOTS = HERE / "shots"
SHOTS.mkdir(exist_ok=True)

VIEWS = [
    ("dir-1", 1440, 900),
    ("dir-2", 1440, 900),
    ("dir-3", 1440, 900),
]


def main() -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for name, width, height in VIEWS:
            page = browser.new_page(viewport={"width": width, "height": height},
                                    device_scale_factor=1)
            page.goto((HERE / f"{name}.html").resolve().as_uri())
            page.wait_for_timeout(500)
            page.screenshot(path=str(SHOTS / f"{name}-top.png"))
            page.screenshot(path=str(SHOTS / f"{name}-full.png"), full_page=True)
            page.close()
            print("shot", name)
        browser.close()


if __name__ == "__main__":
    main()
