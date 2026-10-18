#!/usr/bin/env python3
"""Convert a vault LOI markdown file to a print-quality PDF on Radikale Konzept letterhead.

Pipeline: markdown -> branded HTML -> headless Chrome via CDP Page.printToPDF.

WHY CDP AND NOT `--print-to-pdf`:
  Chrome's CLI `--print-to-pdf` cannot place content in the real page margin.
  A position:fixed footer rendered that way lands at the CONTENT-box bottom and
  silently covers the last lines of every page (verified: it clipped a table row).
  CDP's Page.printToPDF supports displayHeaderFooter + headerTemplate/
  footerTemplate, which render INSIDE the page margin and reserve layout space.
  Use CDP whenever a repeating footer or page numbers are required.

The house template is derived from the pack's .docx:
  logo  : assets/radikale-konzept/logo.png (native 1.42in x 0.81in placement)
  body  : Georgia 10.5pt, brand navy #1A3C5E
  header: logo | RADIKALE KONZEPT / SDN BHD (1070464-H) | address right-aligned
  footer: repeats on every page, in the true bottom page margin

Usage:  loi-md2pdf.py <input.md> [output.pdf] [--plain]
        --plain : generic document styling, no letterhead
"""
import base64
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
ASSETS = os.path.expanduser("~/.hermes/profiles/dealcrusher/assets/radikale-konzept")
TEMPLATE = os.path.join(ASSETS, "template.html")
LOGO = os.path.join(ASSETS, "logo.png")

FOOTER_HTML = (
    '<div style="font-family:Arial,Helvetica,sans-serif;font-size:7.8pt;color:#6B7280;'
    'width:100%;text-align:center;border-top:1px solid #E3E6EA;padding-top:2mm;">'
    'Radikale Konzept Sdn Bhd (1070464-H) &nbsp;&middot;&nbsp; '
    'No. 30-1, Jalan SS15/4C, 47500 Subang Jaya, Selangor, Malaysia. &nbsp;'
    'www.radikalekonzept.org'
    '</div>'
)
EMPTY_HEADER = '<div style="font-size:0;"></div>'

PLAIN_CSS = """
@page { size: A4; margin: 20mm 18mm 18mm 18mm; }
* { box-sizing: border-box; }
body { font-family: Georgia, serif; font-size: 10.5pt; line-height: 1.45; color: #1A3C5E; margin:0; }
h1 { font-size: 16pt; text-align:center; text-transform:uppercase; margin:0 0 6pt 0;
     border-bottom: 2px solid #1A3C5E; padding-bottom:6pt; page-break-after: avoid; }
h2 { font-size:12pt; margin:16pt 0 6pt 0; padding-bottom:2pt;
     border-bottom:1px solid #C9CFD6; page-break-after: avoid; }
h3 { font-size:11pt; font-style:italic; margin:12pt 0 4pt 0; page-break-after: avoid; }
p { margin:0 0 7pt 0; text-align:justify; }
hr { border:0; border-top:1px solid #C9CFD6; margin:12pt 0; }
ul, ol { margin:0 0 8pt 0; padding-left:18pt; } li { margin-bottom:3pt; }
blockquote { margin:8pt 0; padding:6pt 10pt; background:#F4F6F8;
             border-left:3px solid #1A3C5E; font-size:9.5pt; }
blockquote p { margin:0; text-align:left; }
table { width:100%; border-collapse:collapse; margin:6pt 0 10pt 0; font-size:8.8pt; }
th, td { border:1px solid #B9C0C8; padding:3pt 5pt; text-align:left; vertical-align:top; }
th { background:#EDF1F5; font-weight:bold; }
tr { page-break-inside: avoid; } thead { display: table-header-group; }
"""


def build_html(md_text, title, plain=False):
    import markdown
    body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "sane_lists", "nl2br"],
    )
    if plain:
        return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>{title}</title><style>{PLAIN_CSS}</style></head><body>{body}</body></html>"""

    tpl = open(TEMPLATE, encoding="utf-8").read()
    return (tpl
            .replace("{{TITLE}}", title)
            .replace("{{LOGO}}", "file://" + LOGO)
            .replace("{{CONTENT}}", body))


def strip_frontmatter(md_text):
    return re.sub(r"^---\n.*?\n---\n", "", md_text, count=1, flags=re.S)


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def cdp_print(html_path, out_path, timeout=120):
    """Render HTML -> PDF via CDP Page.printToPDF, footer inside the page margin."""
    import websocket  # websocket-client

    port = free_port()
    profile_dir = tempfile.mkdtemp(prefix="chrome-prof-")
    chrome = subprocess.Popen(
        [CHROME, "--headless=new", "--disable-gpu", "--no-sandbox",
         "--no-first-run", "--no-default-browser-check",
         "--remote-allow-origins=*",
         f"--remote-debugging-port={port}",
         f"--user-data-dir={profile_dir}",
         "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ok = False
        for _ in range(160):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{port}/json/version", timeout=1):
                    ok = True
                break
            except Exception:
                time.sleep(0.25)
        if not ok:
            raise RuntimeError("Chrome DevTools endpoint did not come up")

        # open a tab at the target URL
        url = "file://" + html_path
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe=':/')}",
            method="PUT")
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                tab = json.load(r)
        except Exception:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/json/new?{urllib.parse.quote(url, safe=':/')}",
                    timeout=15) as r:
                tab = json.load(r)

        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=timeout)
        counter = {"n": 0}

        def send(method, params=None):
            counter["n"] += 1
            mid = counter["n"]
            msg = {"id": mid, "method": method}
            if params:
                msg["params"] = params
            ws.send(json.dumps(msg))
            while True:
                data = json.loads(ws.recv())
                if data.get("id") == mid:
                    if "error" in data:
                        raise RuntimeError(f"{method}: {data['error']}")
                    return data.get("result", {})

        send("Page.enable")
        send("Page.navigate", {"url": url})
        time.sleep(3.0)  # let webfonts/images settle

        res = send("Page.printToPDF", {
            "printBackground": True,
            "preferCSSPageSize": True,
            "displayHeaderFooter": True,
            "headerTemplate": EMPTY_HEADER,
            "footerTemplate": FOOTER_HTML,
            "marginTop": 0, "marginBottom": 0,
            "marginLeft": 0, "marginRight": 0,
        })
        pdf_b64 = res.get("data")
        if not pdf_b64:
            raise RuntimeError("printToPDF returned no data")
        with open(out_path, "wb") as f:
            f.write(base64.b64decode(pdf_b64))
        ws.close()
    finally:
        chrome.terminate()
        try:
            chrome.wait(timeout=15)
        except Exception:
            chrome.kill()
        shutil.rmtree(profile_dir, ignore_errors=True)


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    plain = "--plain" in sys.argv
    if not args:
        print("usage: loi-md2pdf.py <input.md> [output.pdf] [--plain]")
        return 2
    src = os.path.abspath(args[0])
    if not os.path.isfile(src):
        print(f"ERROR: not found: {src}")
        return 2
    out = os.path.abspath(args[1]) if len(args) > 1 else os.path.splitext(src)[0] + ".pdf"

    md_text = strip_frontmatter(open(src, encoding="utf-8").read())
    title = os.path.splitext(os.path.basename(out))[0]
    html = build_html(md_text, title, plain=plain)

    tmpdir = tempfile.mkdtemp(prefix="loipdf-")
    html_path = os.path.join(tmpdir, "doc.html")
    open(html_path, "w", encoding="utf-8").write(html)

    try:
        cdp_print(html_path, out)
    except Exception as e:
        print("RENDER FAILED:", e)
        return 1

    if not os.path.isfile(out):
        print("RENDER FAILED: no output file")
        return 1

    print(f"OK: {out}")
    print(f"   bytes: {os.path.getsize(out)} | letterhead: {'no (plain)' if plain else 'yes'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
