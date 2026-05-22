#!/usr/bin/env python3
"""
Generate user-guide.pdf from user-guide.md using pandoc + Chrome headless.
Fixes page-break issues by wrapping sections and using proper CSS.
"""

import subprocess
import re
import sys
import os
import tempfile

MD_FILE = "user-guide.md"
PDF_FILE = "user-guide.pdf"


CSS = """
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

* {
  -webkit-print-color-adjust: exact !important;
  print-color-adjust: exact !important;
  box-sizing: border-box;
}

@page {
  size: A4;
  margin: 18mm 16mm 18mm 16mm;
  @bottom-right {
    content: counter(page);
    font-family: Inter, sans-serif;
    font-size: 9pt;
    color: #94A3B8;
  }
}

html, body {
  font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
  font-size: 9.5pt;
  line-height: 1.6;
  color: #1E293B;
  margin: 0;
  padding: 0;
  background: white;
}

/* ── COVER PAGE ─────────────────────────────── */
.cover-page {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  align-items: stretch;
  padding: 0;
  position: relative;
  background: #ffffff !important;
  overflow: hidden;
  direction: ltr;
}

/* Top accent bar — full-width gradient */
.cover-page::before {
  content: '';
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 5px;
  background: linear-gradient(90deg, #6366F1 0%, #8B5CF6 30%, #EC4899 60%, #F59E0B 80%, #10B981 100%);
}

/* Subtle decorative circle — bottom right */
.cover-page::after {
  content: '';
  position: absolute;
  bottom: -120px;
  right: -120px;
  width: 380px;
  height: 380px;
  border-radius: 50%;
  border: 40px solid rgba(99, 102, 241, 0.06);
  pointer-events: none;
}

/* Top bar: logo + badge stacked on the left */
.cover-topbar {
  width: 100%;
  padding: 32px 52px 0 52px;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  position: relative;
  z-index: 1;
  direction: ltr;
}

.cover-logo {
  height: 110px;
  width: 360px;
  /* background-image injected by generate_pdf.py after pandoc */
  background-size: contain;
  background-repeat: no-repeat;
  background-position: left center;
  /* Logo is white-on-black; invert makes it black-on-white */
  filter: invert(1);
  display: block;
}

.cover-badge {
  font-family: 'JetBrains Mono', monospace;
  font-size: 7pt;
  font-weight: 500;
  color: #6366F1;
  background: rgba(99,102,241,0.08);
  border: 1px solid rgba(99,102,241,0.25);
  padding: 4px 12px;
  border-radius: 20px;
  letter-spacing: 0.1em;
  text-transform: uppercase;
  white-space: nowrap;
  margin-top: 10px;
}

/* Main content area */
.cover-body {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: center;
  padding: 0 52px;
  position: relative;
  z-index: 1;
  direction: ltr;
}

.cover-eyebrow {
  font-family: 'JetBrains Mono', monospace;
  font-size: 7.5pt;
  font-weight: 400;
  color: #6366F1;
  letter-spacing: 0.22em;
  text-transform: uppercase;
  margin-bottom: 16px;
}

.cover-title {
  font-size: 38pt;
  font-weight: 700;
  color: #0F172A;
  line-height: 1.08;
  margin-bottom: 12px;
  letter-spacing: -1px;
}

.cover-sub {
  font-size: 13pt;
  font-weight: 400;
  color: #64748B;
  margin-bottom: 40px;
  letter-spacing: 0.01em;
}

.cover-divider {
  width: 52px;
  height: 3px;
  background: linear-gradient(90deg, #6366F1, #EC4899);
  border-radius: 2px;
  margin-bottom: 36px;
}

/* Meta info cards */
.cover-meta-grid {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
  direction: ltr;
}

.cover-meta-card {
  background: #F8FAFC;
  border: 1px solid #E2E8F0;
  border-radius: 8px;
  padding: 10px 18px;
  min-width: 110px;
}

.cover-meta-label {
  font-family: 'JetBrains Mono', monospace;
  font-size: 6.5pt;
  color: #94A3B8;
  letter-spacing: 0.15em;
  text-transform: uppercase;
  margin-bottom: 3px;
}

.cover-meta-value {
  font-size: 9pt;
  font-weight: 600;
  color: #1E293B;
}

/* Bottom bar */
.cover-bottombar {
  width: 100%;
  padding: 20px 52px;
  border-top: 1px solid #F1F5F9;
  display: flex;
  align-items: center;
  justify-content: space-between;
  position: relative;
  z-index: 1;
  direction: ltr;
}

.cover-bottombar-left {
  font-family: 'JetBrains Mono', monospace;
  font-size: 7pt;
  color: #94A3B8;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.cover-bottombar-right {
  display: flex;
  gap: 6px;
  align-items: center;
}

.cover-dot {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  display: inline-block;
}

/* ── TOC PAGE ───────────────────────────────── */
.toc-page {
  page-break-before: always;
  padding: 8px 0 0 0;
}

.toc-page h2 {
  font-size: 15pt;
  color: #1E293B;
  margin-bottom: 16px;
  padding-bottom: 8px;
  border-bottom: 2px solid #E2E8F0;
}

.toc-page ol, .toc-page ul {
  margin: 0;
  padding-left: 22px;
  line-height: 2;
}

.toc-page li {
  color: #334155;
  font-size: 9.5pt;
}

/* ── HEADINGS ───────────────────────────────── */
h1 {
  font-size: 19pt;
  font-weight: 700;
  color: #0F172A;
  margin: 28px 0 18px 0;
  padding-bottom: 10px;
  border-bottom: 2px solid #E2E8F0;
  page-break-after: avoid !important;
  break-after: avoid !important;
}

h2 {
  font-size: 14pt;
  font-weight: 700;
  color: #1E3A5F;
  background: #EFF6FF;
  border-left: 4px solid #3B82F6;
  padding: 8px 12px;
  margin: 24px 0 12px 0;
  border-radius: 0 4px 4px 0;
  page-break-after: avoid !important;
  break-after: avoid !important;
  page-break-inside: avoid !important;
  break-inside: avoid !important;
}

h3 {
  font-size: 11pt;
  font-weight: 600;
  color: #1E293B;
  margin: 18px 0 8px 0;
  padding-bottom: 4px;
  border-bottom: 1px solid #E2E8F0;
  page-break-after: avoid !important;
  break-after: avoid !important;
  page-break-inside: avoid !important;
  break-inside: avoid !important;
}

h4 {
  font-size: 10pt;
  font-weight: 600;
  color: #334155;
  margin: 14px 0 6px 0;
  page-break-after: avoid !important;
  break-after: avoid !important;
}

/* ── SECTION CONTAINERS (prevent heading orphans) */
.section-block {
  page-break-inside: avoid !important;
  break-inside: avoid !important;
}

/* ── PARAGRAPHS ─────────────────────────────── */
p {
  margin: 6px 0 10px 0;
  orphans: 3;
  widows: 3;
}

/* ── CODE BLOCKS ────────────────────────────── */
pre {
  background: #0F172A !important;
  color: #E2E8F0 !important;
  border-left: 4px solid #3B82F6;
  border-radius: 0 6px 6px 0;
  padding: 14px 16px;
  font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
  font-size: 8pt;
  line-height: 1.6;
  overflow-x: auto;
  white-space: pre-wrap;
  word-wrap: break-word;
  margin: 10px 0 14px 0;
  page-break-inside: avoid !important;
  break-inside: avoid !important;
}

code {
  background: #F1F5F9;
  color: #0F172A;
  font-family: 'JetBrains Mono', 'Fira Code', 'Courier New', monospace;
  font-size: 8pt;
  padding: 1px 5px;
  border-radius: 3px;
}

pre code {
  background: transparent !important;
  color: inherit !important;
  padding: 0;
  font-size: inherit;
}

/* ── TABLES ─────────────────────────────────── */
table {
  border-collapse: collapse;
  width: 100%;
  margin: 10px 0 16px 0;
  font-size: 8.5pt;
  page-break-inside: avoid !important;
  break-inside: avoid !important;
}

th {
  background: #1E293B !important;
  color: #F8FAFC !important;
  font-weight: 600;
  padding: 7px 10px;
  text-align: left;
  border: 1px solid #334155;
}

td {
  padding: 6px 10px;
  border: 1px solid #E2E8F0;
  vertical-align: top;
}

tr:nth-child(even) td {
  background: #F8FAFC;
}

/* ── BLOCKQUOTES ────────────────────────────── */
blockquote {
  background: #FFF7ED;
  border-left: 4px solid #F59E0B;
  margin: 10px 0;
  padding: 10px 16px;
  border-radius: 0 4px 4px 0;
  page-break-inside: avoid !important;
  break-inside: avoid !important;
}

blockquote p {
  margin: 0;
  color: #92400E;
  font-size: 9pt;
}

/* ── LISTS ──────────────────────────────────── */
ul, ol {
  margin: 6px 0 10px 0;
  padding-left: 24px;
}

li {
  margin: 3px 0;
  orphans: 2;
  widows: 2;
}

/* ── HORIZONTAL RULE ────────────────────────── */
hr {
  border: none;
  border-top: 1px solid #E2E8F0;
  margin: 16px 0;
}

/* ── BOLD / STRONG ──────────────────────────── */
strong {
  font-weight: 600;
  color: #0F172A;
}

/* ── PAGE BREAKS ────────────────────────────── */
.page-break {
  page-break-after: always;
  break-after: always;
  height: 0;
  margin: 0;
  padding: 0;
}

/* ── KEEP HEADING GLUED TO NEXT ELEMENT ─────── */
/* When heading is followed by any block, don't break between them */
h1 + *, h2 + *, h3 + *, h4 + * {
  page-break-before: avoid !important;
  break-before: avoid !important;
}

/* Bold-label paragraph followed by code block or table */
p + pre, p + table, p + blockquote, p + ul, p + ol {
  page-break-before: avoid !important;
  break-before: avoid !important;
}

/* ── KEEP-TOGETHER wrapper (for label+pre or label+table) */
.keep-together {
  page-break-inside: avoid !important;
  break-inside: avoid !important;
}
"""


def load_logo_b64() -> str:
    """Load the FLENDER GROUP logo as a base64 data URI."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    b64_file = os.path.join(script_dir, "logo_b64.txt")
    if os.path.exists(b64_file):
        with open(b64_file) as f:
            return "data:image/png;base64," + f.read().strip()
    # Fallback: scan for the screenshot file with non-standard spaces
    for fname in os.listdir(script_dir):
        if "Screenshot" in fname and fname.endswith(".png"):
            import base64
            with open(os.path.join(script_dir, fname), "rb") as f:
                return "data:image/png;base64," + base64.b64encode(f.read()).decode()
    return ""


def build_html(md_content: str) -> str:
    """Convert markdown to full HTML, post-processing for page breaks."""

    # Write markdown to temp file and convert via pandoc
    # NOTE: do NOT inject base64 logo into markdown — pandoc can't handle huge attributes
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write(md_content)
        tmp_md = f.name

    result = subprocess.run(
        ["pandoc", tmp_md, "--from=markdown+raw_html", "--to=html5", "--no-highlight"],
        capture_output=True, text=True, check=True
    )
    os.unlink(tmp_md)
    body_html = result.stdout

    # Remove any auto-generated H1 title pandoc adds
    body_html = re.sub(r'<h1[^>]*id="user-guide[^"]*"[^>]*>.*?</h1>', '', body_html, flags=re.DOTALL)

    # Inject logo as CSS background-image AFTER pandoc conversion
    # (avoids pandoc choking on 100KB base64 inside an HTML attribute)
    logo_src = load_logo_b64()
    if logo_src:
        logo_style = f'<style>.cover-logo {{ background-image: url("{logo_src}"); }}</style>'
        body_html = logo_style + body_html

    # Post-process: wrap each <p><strong>Label:</strong></p> + <pre> or <table> pair
    # in a .keep-together div to prevent orphan labels
    body_html = re.sub(
        r'(<p><strong>[^<]+</strong></p>\s*)(<(?:pre|table)\b)',
        r'<div class="keep-together">\1\2',
        body_html
    )
    body_html = re.sub(
        r'(<div class="keep-together">)(.*?)(</(?:pre|table)>)',
        r'\1\2\3</div>',
        body_html,
        flags=re.DOTALL
    )

    full_html = f"""<!DOCTYPE html>
<html lang="en" dir="ltr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Social Media Content Tracker — User Guide</title>
<style>
{CSS}
</style>
</head>
<body>
{body_html}
</body>
</html>"""

    return full_html


def generate_pdf(html_path: str, pdf_path: str):
    """Use Chrome headless to render HTML to PDF."""
    chrome_candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "google-chrome",
        "chromium",
    ]

    chrome = None
    for c in chrome_candidates:
        try:
            subprocess.run([c, "--version"], capture_output=True, check=True)
            chrome = c
            break
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    if not chrome:
        print("ERROR: Chrome not found. Install Google Chrome.")
        sys.exit(1)

    abs_html = os.path.abspath(html_path)
    abs_pdf = os.path.abspath(pdf_path)

    subprocess.run([
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--print-background",
        f"--print-to-pdf={abs_pdf}",
        f"file://{abs_html}",
    ], check=True, capture_output=True)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    md_path = os.path.join(script_dir, MD_FILE)
    pdf_path = os.path.join(script_dir, PDF_FILE)

    print(f"Reading {md_path}...")
    with open(md_path, encoding="utf-8") as f:
        md_content = f.read()

    print("Converting to HTML...")
    html = build_html(md_content)

    html_path = os.path.join(script_dir, "user-guide.html")
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML written to {html_path}")

    print("Generating PDF via Chrome headless...")
    generate_pdf(html_path, pdf_path)

    size_kb = os.path.getsize(pdf_path) // 1024
    print(f"PDF saved: {pdf_path} ({size_kb} KB)")


if __name__ == "__main__":
    main()
