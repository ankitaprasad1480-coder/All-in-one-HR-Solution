#!/usr/bin/env python3
"""Embed the logo and background into web/app.html.

    python web/build_web.py

Writes static/index.html (what server.py serves) and dist/preview.html (a clickable demo that needs no server:
accounts live in the browser and the data is made up)."""
import base64
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def uri(p, mime):
    return f"data:{mime};base64," + base64.b64encode(Path(p).read_bytes()).decode()


def main():
    src = (ROOT / "web" / "app.html").read_text(encoding="utf-8")
    html = src.replace("__LOGO__", uri(ROOT / "assets/logo.png", "image/png")).replace(
        "__BG__", uri(ROOT / "assets/office_bg.jpg", "image/jpeg"))
    (ROOT / "static").mkdir(exist_ok=True)
    (ROOT / "static" / "index.html").write_text(html, encoding="utf-8")
    banner = ('<div id="pvb">Preview mode: accounts live only in this browser and the dashboard shows made-up '
              'demo data. The real version runs on your server.</div>')
    mock = (ROOT / "web" / "preview_mock.js").read_text(encoding="utf-8")
    pv = html.replace("<body>", '<body class="pv">' + banner, 1).replace(
        "<script>\nlet R=[]", "<script>\n" + mock + "</script>\n<script>\nlet R=[]", 1)
    assert "window.MOCK_API" in pv and 'class="pv"' in pv
    (ROOT / "dist").mkdir(exist_ok=True)
    (ROOT / "dist" / "preview.html").write_text(pv, encoding="utf-8")
    print("Wrote static/index.html and dist/preview.html")


if __name__ == "__main__":
    main()
