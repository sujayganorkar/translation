"""
book_pipeline.py — PDF Book Translation Pipeline
==================================================

Commands:
  python book_pipeline.py status                — Progress overview
  python book_pipeline.py extract               — Rasterize original PDF pages as images
  python book_pipeline.py describe-pages        — VLM: structured page descriptions (Haiku)
  python book_pipeline.py merge                 — Sonnet: merge VLM + Trinity, resolve conflicts
  python book_pipeline.py build                 — Build English PDF from descriptions
  python book_pipeline.py qa [--sample N]       — VLM QA: compare original vs translation
  python book_pipeline.py fix                   — Remove QA-flagged pages for re-translation

Files:
  page_images/              ← rasterized original pages (extract)
  page_descriptions.json    ← structured VLM descriptions (describe-pages)
  page_descriptions_merged.json ← Sonnet-merged best-of-both (merge)
  translations.json         ← text translations fallback (from translate_all.py)
  <source>_EN.pdf           ← final output

Environment variables required:
  ANTHROPIC_API_KEY         — for Haiku (describe-pages) and Sonnet (merge, qa)
"""

import json
import os
import sys
import re
import html
import glob
import time
import base64
import argparse
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import fitz  # PyMuPDF

sys.stdout.reconfigure(encoding='utf-8')

# ── CONFIG ──────────────────────────────────────────────────────────
DESCRIPTIONS_FILE = "page_descriptions.json"
TRANSLATIONS_FILE = "translations.json"
IMAGE_DIR = "page_images"
QA_REPORT = "qa_report.json"
RETRANSLATE_QUEUE = "retranslate_queue.json"
MERGED_FILE = "page_descriptions_merged.json"

IMAGE_DPI = 150
JPEG_QUALITY = 75

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
DESCRIBE_MODEL = "claude-haiku-4-5-20251001"
MERGE_MODEL = "claude-sonnet-4-20250514"


def _get_output_pdf_name():
    """Derive output PDF name from source PDF: <source>_EN.pdf"""
    try:
        src = find_source_pdf()
        stem = Path(src).stem
        return f"{stem}_EN.pdf"
    except FileNotFoundError:
        return "output_EN.pdf"
# ────────────────────────────────────────────────────────────────────

DESCRIBE_PROMPT = """You are a technical document analyzer for a Chinese technical book.

Analyze this page image and return a JSON object describing ALL content, translated to English.

RULES:
1. Translate ALL Chinese text to English. Keep numbers, units, and standard codes (GB/T, ISO, JB/T) exactly as shown.
2. For tables: capture EVERY row and column. Do not summarize or skip rows. Include ALL numeric values exactly as printed.
3. For figures/diagrams: describe what is shown and list all visible labels, dimensions, and annotations.
4. For formulas: reproduce them in text form.
5. Sections must be in top-to-bottom reading order as they appear on the page.
6. If a table continues from a previous page (marked "continued" or "续"), still output the full visible table data.
7. If the page is empty or has only decorative content with no readable text, return: {"page_type": "empty", "page_header": "", "sections": []}
8. Output ONLY valid JSON. No markdown fences, no commentary, no text outside the JSON object.

JSON SCHEMA (follow exactly):
{
  "page_type": "text | table | diagram | toc | mixed | empty",
  "page_header": "running header at top of page, or empty string",
  "sections": [
    {"type": "heading", "level": 1, "text": "Chapter or section title"},
    {"type": "paragraph", "text": "Translated paragraph text"},
    {"type": "table", "caption": "Table title", "headers": ["col1", "col2"], "rows": [["val1", "val2"]]},
    {"type": "figure", "caption": "Figure title", "description": "What the figure shows"},
    {"type": "formula", "text": "F = p × A"},
    {"type": "note", "text": "Note or footnote text"},
    {"type": "list", "ordered": true, "items": ["item1", "item2"]},
    {"type": "toc_entries", "entries": [{"title": "Section name", "page": "123"}]}
  ]
}

level for headings: 1=chapter, 2=section (e.g. 3.2), 3=subsection (e.g. 3.2.1)"""


def find_source_pdf() -> str:
    exclude = {"english", "output", "test", "rebuild", "final", "handbook"}
    pdfs = [
        p for p in Path(".").glob("*.pdf")
        if not any(kw in p.name.lower() for kw in exclude)
    ]
    if not pdfs:
        raise FileNotFoundError("No source PDF found in current directory")
    return str(max(pdfs, key=lambda p: p.stat().st_size))


def find_img_path(page_num):
    path = os.path.join(IMAGE_DIR, f"page_{int(page_num):04d}.jpg")
    return path if os.path.exists(path) else None


def _repair_truncated_json(text):
    """Attempt to repair JSON truncated mid-output (e.g. from max_tokens cutoff).
    Closes open strings, arrays, and objects so we keep the data we got."""
    # Close any open string
    in_string = False
    escaped = False
    for ch in text:
        if escaped:
            escaped = False
            continue
        if ch == '\\':
            escaped = True
            continue
        if ch == '"':
            in_string = not in_string
    if in_string:
        text += '"'

    # Close open brackets/braces
    stack = []
    in_str = False
    esc = False
    for ch in text:
        if esc:
            esc = False
            continue
        if ch == '\\' and in_str:
            esc = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in ('{', '['):
            stack.append('}' if ch == '{' else ']')
        elif ch in ('}', ']') and stack:
            stack.pop()

    # Remove trailing comma before closing
    text = text.rstrip().rstrip(',')
    text += ''.join(reversed(stack))

    return json.loads(text)


def _load_json(path):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ====================================================================
#  STATUS
# ====================================================================
def cmd_status():
    print("=" * 60)
    print("PIPELINE STATUS")
    print("=" * 60)

    try:
        pdf_path = find_source_pdf()
        src = fitz.open(pdf_path)
        total = len(src)
        src.close()
        print(f"\nOriginal PDF: {pdf_path} ({total} pages)")
    except FileNotFoundError:
        print("\nOriginal PDF: NOT FOUND")
        total = 0

    if os.path.exists(IMAGE_DIR):
        imgs = glob.glob(os.path.join(IMAGE_DIR, "page_*.jpg"))
        print(f"Page images: {len(imgs)} extracted")
    else:
        print("Page images: not extracted (run: python book_pipeline.py extract)")

    descs = _load_json(DESCRIPTIONS_FILE)
    if descs:
        print(f"Page descriptions: {len(descs)} pages")
        if total:
            print(f"  Progress: {len(descs)/total*100:.1f}% — {total - len(descs)} remaining")
    else:
        print("Page descriptions: not started (run: python book_pipeline.py describe-pages)")

    merged = _load_json(MERGED_FILE)
    if merged:
        flagged = sum(1 for v in merged.values() if isinstance(v, dict)
                      for s in v.get("sections", []) if "_flagged" in s)
        print(f"Merged descriptions: {len(merged)} pages ({flagged} flagged corrections)")
    else:
        print("Merged descriptions: not started (run: python book_pipeline.py merge)")

    trans = _load_json(TRANSLATIONS_FILE)
    if trans:
        print(f"Text translations (fallback): {len(trans)} pages")

    output_pdf = _get_output_pdf_name()
    if os.path.exists(output_pdf):
        size = os.path.getsize(output_pdf) / (1024 * 1024)
        print(f"Output PDF: {output_pdf} ({size:.1f} MB)")
    else:
        print(f"Output PDF: not yet built (will be: {output_pdf})")

    if os.path.exists(QA_REPORT):
        qa = _load_json(QA_REPORT)
        passed = sum(1 for r in qa.values() if r.get("pass"))
        failed = sum(1 for r in qa.values() if not r.get("pass"))
        print(f"QA: {len(qa)} checked — {passed} passed, {failed} failed")

    print("=" * 60)


# ====================================================================
#  EXTRACT
# ====================================================================
def cmd_extract():
    print("Extracting page images...")
    pdf_path = find_source_pdf()
    src = fitz.open(pdf_path)
    total = len(src)
    os.makedirs(IMAGE_DIR, exist_ok=True)

    existing = set()
    for f in glob.glob(os.path.join(IMAGE_DIR, "page_*.jpg")):
        num = int(os.path.splitext(os.path.basename(f))[0].split("_")[1])
        existing.add(num)

    todo = [p for p in range(1, total + 1) if p not in existing]
    print(f"  Total: {total}, done: {len(existing)}, remaining: {len(todo)}")

    scale = IMAGE_DPI / 72.0
    mat = fitz.Matrix(scale, scale)

    for i, p in enumerate(todo):
        if i % 50 == 0 and i > 0:
            print(f"  {i}/{len(todo)}...")
        pix = src[p - 1].get_pixmap(matrix=mat, alpha=False)
        pix.save(os.path.join(IMAGE_DIR, f"page_{p:04d}.jpg"), jpg_quality=JPEG_QUALITY)

    src.close()
    print(f"  Done. {len(glob.glob(os.path.join(IMAGE_DIR, 'page_*.jpg')))} images.")


# ====================================================================
#  DESCRIBE-PAGES — Async parallel VLM descriptions via Anthropic API
# ====================================================================
CONCURRENCY = 8  # conservative: ~8 concurrent × ~1K output tokens × ~3s = ~160K/min headroom

def cmd_describe_pages():
    import asyncio as _asyncio
    _asyncio.run(_describe_pages_async())

async def _describe_pages_async():
    import asyncio
    print("Describing pages with VLM (Haiku) — async parallel...")
    assert os.path.exists(IMAGE_DIR), f"Missing {IMAGE_DIR}/ — run extract first"
    assert ANTHROPIC_API_KEY, "Set ANTHROPIC_API_KEY environment variable"

    import anthropic
    client = anthropic.AsyncAnthropic(api_key=ANTHROPIC_API_KEY)

    # Get total page count
    try:
        src = fitz.open(find_source_pdf())
        total = len(src)
        src.close()
    except Exception:
        total = len(glob.glob(os.path.join(IMAGE_DIR, "page_*.jpg")))

    descs = _load_json(DESCRIPTIONS_FILE)
    done = set(int(k) for k, v in descs.items() if v.get("page_type") != "error")
    error_pages = set(int(k) for k, v in descs.items() if v.get("page_type") == "error")
    todo = [p for p in range(1, total + 1) if p not in done]
    print(f"  Total: {total}, done: {len(done)}, errors to retry: {len(error_pages)}, remaining: {len(todo)}")

    if not todo:
        print("  All pages described.")
        return

    import threading
    lock = threading.Lock()
    total_input = 0
    total_output = 0
    success = 0
    failed = 0
    failed_pages = []
    completed = 0
    semaphore = asyncio.Semaphore(CONCURRENCY)
    # Global rate pacer: wait between launching requests to avoid thundering herd
    rate_delay = 0.3  # seconds between request starts — ~3.3 req/sec × ~3s flight = ~10 in-flight
    rate_lock = asyncio.Lock()
    t0 = time.time()

    async def process_page(page_num, idx):
        nonlocal total_input, total_output, success, failed, completed, rate_delay

        img_path = find_img_path(page_num)
        if not img_path:
            print(f"  Page {page_num}: no image, skipping")
            with lock:
                failed_pages.append(page_num)
                failed += 1
            return

        with open(img_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode()

        async with semaphore:
            # Pace requests globally to avoid bursts
            async with rate_lock:
                await asyncio.sleep(rate_delay)

            for attempt in range(3):
                try:
                    response = await client.messages.create(
                        model=DESCRIBE_MODEL,
                        max_tokens=16384,
                        temperature=0.1,
                        messages=[{"role": "user", "content": [
                            {"type": "text", "text": DESCRIBE_PROMPT},
                            {"type": "image", "source": {
                                "type": "base64", "media_type": "image/jpeg", "data": img_data}},
                        ]}],
                    )

                    text = response.content[0].text.strip()
                    if text.startswith("```"):
                        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

                    parsed = json.loads(text)

                    with lock:
                        descs[str(page_num)] = parsed
                        total_input += response.usage.input_tokens
                        total_output += response.usage.output_tokens
                        success += 1
                        completed += 1

                        if completed % 50 == 0:
                            elapsed = time.time() - t0
                            rate = completed / elapsed
                            remaining = (len(todo) - completed) / rate if rate > 0 else 0
                            print(f"  Progress: {completed}/{len(todo)} "
                                  f"({completed/len(todo)*100:.1f}%) — "
                                  f"{rate:.1f} pages/sec — ~{remaining/60:.0f}min remaining")
                            _save_json(DESCRIPTIONS_FILE, descs)
                    return

                except json.JSONDecodeError as e:
                    # Try to repair truncated JSON (common with large tables)
                    try:
                        parsed = _repair_truncated_json(text)
                        with lock:
                            descs[str(page_num)] = parsed
                            total_input += response.usage.input_tokens
                            total_output += response.usage.output_tokens
                            success += 1
                            completed += 1
                        print(f"  Page {page_num}: repaired truncated JSON")
                        return
                    except Exception:
                        pass
                    with lock:
                        descs[str(page_num)] = {"_raw": text[:2000], "_error": str(e),
                                                 "page_type": "error", "sections": []}
                        failed += 1
                        completed += 1
                        failed_pages.append(page_num)
                    return

                except anthropic.RateLimitError:
                    # Back off globally — increase delay for everyone
                    rate_delay = min(rate_delay * 1.5, 3.0)
                    wait = 10 + attempt * 15  # 10s, 25s, 40s
                    if attempt == 0:
                        print(f"  Rate limited (batch), pausing {wait}s (delay→{rate_delay:.1f}s)...")
                    await asyncio.sleep(wait)

                except Exception as e:
                    if attempt == 2:
                        print(f"  Page {page_num}: failed after 3 attempts: {e}")
                        with lock:
                            failed += 1
                            completed += 1
                            failed_pages.append(page_num)
                        return
                    await asyncio.sleep(2 ** attempt)

    # Launch all tasks
    tasks = [process_page(page_num, i) for i, page_num in enumerate(todo)]
    await asyncio.gather(*tasks)

    # Final save
    _save_json(DESCRIPTIONS_FILE, descs)
    await client.close()

    elapsed = time.time() - t0
    cost_in = total_input * 0.80 / 1_000_000
    cost_out = total_output * 4.00 / 1_000_000
    total_cost = cost_in + cost_out

    print(f"\n{'='*60}")
    print(f"DESCRIBE COMPLETE — {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"  Success: {success}, Failed: {failed}")
    print(f"  Tokens: {total_input:,} input + {total_output:,} output")
    print(f"  Cost: ${cost_in:.2f} input + ${cost_out:.2f} output = ${total_cost:.2f} total")
    print(f"  Speed: {success/elapsed:.1f} pages/sec")
    if failed_pages:
        print(f"  Failed pages ({len(failed_pages)}): {failed_pages[:20]}{'...' if len(failed_pages) > 20 else ''}")
    print(f"  Total descriptions: {len([k for k,v in descs.items() if v.get('page_type') != 'error'])}")
    print(f"{'='*60}")


# ====================================================================
#  BUILD — Render structured descriptions into PDF with ReportLab
# ====================================================================
def cmd_build():
    print("Building English PDF from page descriptions...")

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER, TA_LEFT
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, PageBreak,
        Image, HRFlowable, Table, TableStyle, KeepTogether,
    )
    from reportlab.lib import colors
    from reportlab.lib.utils import ImageReader

    # Prefer merged descriptions > raw descriptions > text translations
    merged = _load_json(MERGED_FILE)
    descs_raw = _load_json(DESCRIPTIONS_FILE)
    trans = _load_json(TRANSLATIONS_FILE)  # fallback
    # Merged takes priority, then raw VLM, combined into one dict
    descs = {}
    descs.update(descs_raw)
    descs.update(merged)  # merged overwrites raw

    try:
        src = fitz.open(find_source_pdf())
        total_pages = len(src)
        src.close()
    except Exception:
        total_pages = max(
            max((int(k) for k in descs.keys()), default=0),
            max((int(k) for k in trans.keys()), default=0),
        )

    desc_count = len([k for k, v in descs.items() if v.get("page_type") != "error"])
    print(f"  Descriptions: {desc_count}, Text fallbacks: {len(trans)}, Total pages: {total_pages}")

    PAGE_W, PAGE_H = A4
    MARGIN = 20 * mm
    CONTENT_W = PAGE_W - 2 * MARGIN

    output_pdf = _get_output_pdf_name()
    doc = SimpleDocTemplate(
        output_pdf, pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=18 * mm, bottomMargin=18 * mm,
    )

    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle('Body', parent=styles['Normal'],
        fontSize=10, leading=13, spaceAfter=5, alignment=TA_JUSTIFY))
    styles.add(ParagraphStyle('ChapterHead', parent=styles['Heading1'],
        fontSize=14, leading=18, spaceAfter=10, spaceBefore=20,
        textColor=colors.HexColor('#1a1a1a')))
    styles.add(ParagraphStyle('SectionHead', parent=styles['Heading2'],
        fontSize=12, leading=15, spaceAfter=8, spaceBefore=14))
    styles.add(ParagraphStyle('SubHead', parent=styles['Heading3'],
        fontSize=11, leading=14, spaceAfter=6, spaceBefore=10))
    styles.add(ParagraphStyle('PageRef', parent=styles['Normal'],
        fontSize=8, textColor=colors.grey, alignment=TA_LEFT,
        spaceBefore=8, spaceAfter=2))
    styles.add(ParagraphStyle('Caption', parent=styles['Normal'],
        fontSize=9, leading=12, spaceAfter=6, spaceBefore=6,
        textColor=colors.HexColor('#333333'), alignment=TA_CENTER))
    styles.add(ParagraphStyle('NoteStyle', parent=styles['Normal'],
        fontSize=9, leading=12, spaceAfter=4,
        textColor=colors.HexColor('#444444')))
    styles.add(ParagraphStyle('FormulaStyle', parent=styles['Normal'],
        fontSize=10, leading=14, spaceAfter=6, spaceBefore=4,
        alignment=TA_CENTER))
    styles.add(ParagraphStyle('TOCLine', parent=styles['Normal'],
        fontSize=9, leading=12, spaceAfter=2))
    styles.add(ParagraphStyle('FigureDesc', parent=styles['Normal'],
        fontSize=9, leading=12, spaceAfter=6, spaceBefore=4,
        textColor=colors.HexColor('#555555'),
        borderColor=colors.HexColor('#cccccc'), borderWidth=0.5,
        borderPadding=4))
    styles.add(ParagraphStyle('CellStyle', parent=styles['Normal'],
        fontSize=8, leading=10))
    styles.add(ParagraphStyle('HeaderCell', parent=styles['Normal'],
        fontSize=8, leading=10, textColor=colors.white))
    styles.add(ParagraphStyle('FallbackBody', parent=styles['Normal'],
        fontSize=10, leading=13, spaceAfter=5, alignment=TA_JUSTIFY))

    def esc(text):
        if not isinstance(text, str):
            text = str(text)
        return html.escape(text)

    def render_section(section, story):
        """Render one section dict into ReportLab flowables."""
        stype = section.get("type", "paragraph")

        if stype == "heading":
            level = section.get("level", 2)
            text = esc(section.get("text", ""))
            if level == 1:
                story.append(Paragraph(text, styles['ChapterHead']))
            elif level == 2:
                story.append(Paragraph(text, styles['SectionHead']))
            else:
                story.append(Paragraph(text, styles['SubHead']))

        elif stype == "paragraph":
            text = esc(section.get("text", "")).replace("\n", " ")
            if text.strip():
                story.append(Paragraph(text, styles['Body']))

        elif stype == "table":
            caption = section.get("caption", "")
            if caption:
                story.append(Paragraph(esc(caption), styles['Caption']))

            headers = section.get("headers", [])
            rows = section.get("rows", [])

            if headers or rows:
                # Build table data with Paragraph cells for wrapping
                table_data = []
                if headers:
                    header_row = [Paragraph(esc(h), styles['HeaderCell']) for h in headers]
                    table_data.append(header_row)

                n_cols = len(headers) if headers else (len(rows[0]) if rows else 1)
                for row in rows:
                    # Pad or trim row to match column count
                    cells = list(row) if isinstance(row, list) else [row]
                    while len(cells) < n_cols:
                        cells.append("")
                    cells = cells[:n_cols]
                    table_data.append([Paragraph(esc(c), styles['CellStyle']) for c in cells])

                if table_data:
                    # Calculate column widths — let ReportLab auto-size if many columns
                    if n_cols <= 6:
                        col_widths = [CONTENT_W / n_cols] * n_cols
                    else:
                        col_widths = None  # auto-size for wide tables
                    t = Table(table_data, colWidths=col_widths,
                              repeatRows=1 if headers else 0)

                    style_cmds = [
                        ('GRID', (0, 0), (-1, -1), 0.5, colors.grey),
                        ('VALIGN', (0, 0), (-1, -1), 'TOP'),
                        ('TOPPADDING', (0, 0), (-1, -1), 2),
                        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
                        ('LEFTPADDING', (0, 0), (-1, -1), 3),
                        ('RIGHTPADDING', (0, 0), (-1, -1), 3),
                    ]
                    if headers:
                        style_cmds.append(
                            ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#2c3e50')))

                    t.setStyle(TableStyle(style_cmds))
                    story.append(t)
                    story.append(Spacer(1, 4))

        elif stype == "figure":
            caption = section.get("caption", "")
            desc = section.get("description", "")
            parts = []
            if caption:
                parts.append(f"<b>{esc(caption)}</b>")
            if desc:
                parts.append(esc(desc))
            if parts:
                story.append(Paragraph("<br/>".join(parts), styles['FigureDesc']))

        elif stype == "formula":
            text = esc(section.get("text", ""))
            if text.strip():
                story.append(Paragraph(f"<b>{text}</b>", styles['FormulaStyle']))

        elif stype == "note":
            text = esc(section.get("text", "")).replace("\n", " ")
            if text.strip():
                story.append(Paragraph(f"<i>{text}</i>", styles['NoteStyle']))

        elif stype == "list":
            items = section.get("items", [])
            ordered = section.get("ordered", False)
            for j, item in enumerate(items):
                prefix = f"{j+1}. " if ordered else "• "
                story.append(Paragraph(prefix + esc(item), styles['Body']))

        elif stype == "toc_entries":
            entries = section.get("entries", [])
            for entry in entries:
                title = esc(entry.get("title", ""))
                page = entry.get("page", "")
                line = f"{title} {'.' * 3} {page}" if page else title
                story.append(Paragraph(line, styles['TOCLine']))

    def render_fallback(text, story):
        """Render plain translation text as fallback."""
        for para in re.split(r"\n\s*\n", text):
            para = para.strip()
            if para:
                story.append(Paragraph(esc(para).replace("\n", " "),
                                       styles['FallbackBody']))

    # ── Build story ──
    story = []

    # Title page
    try:
        book_title = Path(find_source_pdf()).stem
    except FileNotFoundError:
        book_title = "Translated Book"
    story.append(Spacer(1, 50 * mm))
    story.append(Paragraph("English Translation", styles['Title']))
    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(book_title, styles['SectionHead']))
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph(
        f"{desc_count} pages described, "
        f"{len(trans)} text translations", styles['Body']))
    story.append(PageBreak())

    # Page by page
    for page_num in range(1, total_pages + 1):
        pg = str(page_num)
        desc = descs.get(pg)
        has_desc = desc and desc.get("page_type") not in ("error", None)
        has_trans = pg in trans and trans[pg].strip()

        if not has_desc and not has_trans:
            continue

        if page_num % 100 == 0:
            print(f"  Page {page_num}/{total_pages}...")

        # Page marker
        header_text = ""
        if has_desc and desc.get("page_header"):
            header_text = f" — {desc['page_header']}"
        story.append(Paragraph(f"[p. {page_num}{esc(header_text)}]", styles['PageRef']))

        if has_desc:
            page_type = desc.get("page_type", "text")

            # Text-only pages: clean English text, no image
            # All other pages (table, mixed, diagram, toc): show original image + English below
            if page_type != "text":
                img_path = find_img_path(page_num)
                if img_path:
                    try:
                        reader = ImageReader(img_path)
                        iw, ih = reader.getSize()
                        ratio = min(CONTENT_W / iw, (PAGE_H * 0.55) / ih)
                        story.append(Image(img_path, width=iw*ratio, height=ih*ratio))
                        story.append(Spacer(1, 3))
                        story.append(HRFlowable(
                            width="100%", thickness=0.5,
                            color=colors.HexColor('#cccccc')))
                        story.append(Spacer(1, 3))
                    except Exception:
                        pass

            for section in desc.get("sections", []):
                try:
                    render_section(section, story)
                except Exception as e:
                    story.append(Paragraph(
                        f"[Render error: {esc(str(e))}]", styles['NoteStyle']))

        elif has_trans:
            render_fallback(trans[pg], story)

        story.append(Spacer(1, 2 * mm))

    print("  Rendering PDF...")
    doc.build(story)
    size = os.path.getsize(output_pdf) / (1024 * 1024)
    print(f"  Done! {output_pdf} ({size:.1f} MB)")


# ====================================================================
#  QA
# ====================================================================
def cmd_qa(sample_size=None):
    print("Running QA...")
    assert os.path.exists(DESCRIPTIONS_FILE) or os.path.exists(TRANSLATIONS_FILE)
    assert os.path.exists(IMAGE_DIR), f"Missing {IMAGE_DIR}/"

    assert ANTHROPIC_API_KEY, "Set ANTHROPIC_API_KEY environment variable"
    import anthropic

    descs = _load_json(DESCRIPTIONS_FILE)
    trans = _load_json(TRANSLATIONS_FILE)
    qa_results = _load_json(QA_REPORT)

    # QA pages that have descriptions
    all_pages = sorted(set(int(k) for k in descs.keys()) | set(int(k) for k in trans.keys()))

    if sample_size:
        step = max(1, len(all_pages) // sample_size)
        check_pages = all_pages[::step][:sample_size]
    else:
        check_pages = all_pages

    todo = [p for p in check_pages if str(p) not in qa_results]
    print(f"  To check: {len(check_pages)}, remaining: {len(todo)}")

    if not todo:
        _print_qa_summary(qa_results)
        return

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    system_prompt = (
        "You are a QA reviewer for a Chinese-to-English book translation.\n"
        "You receive an image of the original Chinese page and the English translation.\n"
        "Check: COMPLETENESS, COHERENCE, FIGURE/TABLE references, OBVIOUS ERRORS.\n"
        "Respond with ONLY valid JSON:\n"
        '{"pass": true/false, "score": 1-5, "issues": ["list"], "summary": "one sentence"}\n'
        "5=perfect, 4=minor, 3=noticeable gap, 2=significant, 1=major. pass=true if score>=4"
    )

    for i, page_num in enumerate(todo):
        print(f"  QA page {page_num} ({i+1}/{len(todo)})...", end=" ", flush=True)
        img_path = find_img_path(page_num)
        if not img_path:
            print("no image")
            continue

        # Get the best available translation for QA
        pg = str(page_num)
        if pg in descs and descs[pg].get("page_type") != "error":
            translation_text = json.dumps(descs[pg], indent=2)[:3000]
        elif pg in trans:
            translation_text = trans[pg][:3000]
        else:
            print("no translation")
            continue

        try:
            with open(img_path, "rb") as f:
                img_b64 = base64.standard_b64encode(f.read()).decode()

            response = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=400,
                api_key=ANTHROPIC_API_KEY,
                system=system_prompt,
                messages=[{"role": "user", "content": [
                    {"type": "text",
                     "text": f"Page {page_num}.\n\nTranslation:\n{translation_text}"},
                    {"type": "image", "source": {
                        "type": "base64", "media_type": "image/jpeg", "data": img_b64}},
                ]}]
            )
            text = response.content[0].text.strip().replace("```json", "").replace("```", "").strip()
            result = json.loads(text)
            qa_results[str(page_num)] = result
            print("PASS" if result.get("pass") else f"FAIL ({result.get('score')})")
        except Exception as e:
            print(f"error: {e}")
            time.sleep(2)
            continue

        _save_json(QA_REPORT, qa_results)
        time.sleep(0.5)

    _print_qa_summary(qa_results)


def _print_qa_summary(qa_results):
    print(f"\n{'='*60}\nQA SUMMARY\n{'='*60}")
    total = len(qa_results)
    passed = sum(1 for r in qa_results.values() if r.get("pass"))
    failed = sum(1 for r in qa_results.values() if not r.get("pass"))
    scores = [r.get("score", 0) for r in qa_results.values()]
    avg = sum(scores) / len(scores) if scores else 0
    print(f"  Checked: {total}, Passed: {passed}, Failed: {failed}, Avg: {avg:.1f}/5")

    if failed:
        print("\n  Failed pages:")
        for pg, r in sorted(qa_results.items(), key=lambda x: int(x[0])):
            if not r.get("pass"):
                print(f"    p.{pg}: score={r.get('score')} — {'; '.join(r.get('issues',[]))[:100]}")
        queue = [int(p) for p, r in qa_results.items() if not r.get("pass")]
        _save_json(RETRANSLATE_QUEUE, sorted(queue))
        print(f"\n  Saved {len(queue)} pages to {RETRANSLATE_QUEUE}")


# ====================================================================
#  FIX
# ====================================================================
def cmd_fix():
    queue = _load_json(RETRANSLATE_QUEUE)
    if not isinstance(queue, list) or not queue:
        print("No pages to fix.")
        return

    print(f"Removing {len(queue)} pages from descriptions...")
    descs = _load_json(DESCRIPTIONS_FILE)
    removed = sum(1 for p in queue if descs.pop(str(p), None) is not None)
    _save_json(DESCRIPTIONS_FILE, descs)

    trans = _load_json(TRANSLATIONS_FILE)
    for p in queue:
        trans.pop(str(p), None)
    _save_json(TRANSLATIONS_FILE, trans)

    qa = _load_json(QA_REPORT)
    for p in queue:
        qa.pop(str(p), None)
    _save_json(QA_REPORT, qa)

    print(f"  Removed {removed} descriptions + translations + QA results")
    print(f"  Run: python book_pipeline.py describe-pages")
    print(f"  Run: python book_pipeline.py build")


# ====================================================================
#  MERGE — Sonnet-powered conflict resolution between VLM + Trinity
# ====================================================================

MERGE_PROMPT = r"""You are a technical translation reviewer for a Chinese technical book.

You receive:
1. The ORIGINAL Chinese page image (ground truth)
2. SOURCE A: A structured JSON description from a VLM (good structure, may have translation errors)
3. SOURCE B: A plain-text English translation (often more accurate content, but unstructured)

YOUR TASK: Produce a final merged JSON description that combines the best structure (from A) with the most accurate content (from A or B). Follow these rules strictly:

RULES:
1. Use Source A's JSON structure as the skeleton (section types, order, table layout).
2. For each section, compare A and B against the original image. Pick whichever text is more faithful. If both are wrong, correct based on what you see in the image — but ONLY for obvious, verifiable errors.
3. NUMERIC VALUES: Always verify numbers, dimensions, tolerances, and standard codes (GB/T, ISO, JB/T) against the image. If you change a number from BOTH sources, add a "_flagged" field to that section: {"_flagged": "changed value X to Y based on image"}.
4. TECHNICAL TERMS: Prefer the translation that matches standard English engineering terminology. Common corrections:
   - 活塞杆 = piston rod (NOT dual-rod)
   - 同轴密封件 = coaxial seal (NOT round seal ring)
   - 防尘圈 = dust wiper/dust seal (NOT anti-extrusion ring)
   - 支承环 = support ring (NOT 2-way ring)
   - 退刀槽 = relief groove (NOT withdrawal groove)
   - 收尾 = relief / runout (NOT collection)
   - 肩距 = shoulder distance
   - 热电偶 = thermocouple (NOT heating device)
   - 密封件 = seal / sealing element
5. FORMULAS: Verify variable names and operators against the image. Do not guess — if unclear, prefer Source B.
6. Do NOT add content that appears in neither source. Do NOT summarize or omit rows from tables.
7. Keep Source A's page_type and page_header (correct the header text if needed).
8. Output ONLY valid JSON matching the schema below. No markdown fences, no commentary.

JSON SCHEMA:
{
  "page_type": "text | table | diagram | toc | mixed | empty",
  "page_header": "...",
  "sections": [
    {"type": "heading", "level": 1|2|3, "text": "..."},
    {"type": "paragraph", "text": "..."},
    {"type": "table", "caption": "...", "headers": [...], "rows": [[...], ...]},
    {"type": "figure", "caption": "...", "description": "..."},
    {"type": "formula", "text": "..."},
    {"type": "note", "text": "..."},
    {"type": "list", "ordered": true|false, "items": [...]},
    {"type": "toc_entries", "entries": [{"title": "...", "page": "..."}]}
  ]
}

EXAMPLES — showing how to merge correctly:

--- EXAMPLE 1 (table-heavy page) ---

Source A (VLM) had these errors:
- Table 1-105, row P=3.5: g1 = "6.4" (WRONG — image shows 6.2)
- Header column "d4" should be "dg" (image shows subscript g, not 4)
- Figure 1-32 caption: "Internal Thread Groove Cutting Tool Type" (WRONG)
- Note says "thread pitch diameter" (WRONG — image says nominal diameter)
- Table 1-106 note inverted the recommendation logic

Source B (Trinity) was correct on all those points but had no structure.

Correct merged output (abbreviated — real output includes all rows):
{
  "page_type": "mixed",
  "page_header": "Chapter 1  Hydraulic Cylinder Design and Manufacturing Basics  89",
  "sections": [
    {"type": "table",
     "caption": "Table 1-105  Dimensions of External Thread Relief Grooves (Unit: mm)",
     "headers": ["Pitch P", "g2", "g1", "dg", "r"],
     "rows": [
       ["0.5", "1.5", "0.8", "d-0.8", "0.2"],
       ["3.5", "10.5", "6.2", "d-5", "1.6"],
       ["Reference value", "=3P", "--", "--", "--"]
     ]},
    {"type": "note", "text": "Note: 1. d is the nominal diameter symbol of the thread. 2. The tolerance of dg is h13 (d > 3 mm)."},
    {"type": "figure",
     "caption": "Figure 1-32  Types of Internal Thread Relief Grooves",
     "description": "Cross-section showing relief groove profile with dimensions D, Dg, R, G1 labeled, 45 and 120 degree angles."},
    {"type": "note", "text": "Note: Priority should be given to the general length of relief and shoulder distance; when chip removal requires a large space, the long shoulder distance can be selected; when the structure is limited, the short relief can be selected."},
    {"type": "heading", "level": 2, "text": "9. Pipe Thread Relief, Shoulder Distance, Relief Groove and Chamfer"}
  ]
}

Key decisions: Used Trinity's "6.2" over VLM's "6.4" (verified against image). Used "relief groove" over "withdrawal groove". Used Trinity's figure caption and note. Kept VLM's JSON structure.

--- EXAMPLE 2 (text-heavy page with formula) ---

Source A (VLM) had these errors:
- Leakage: said "less than 10mL" (WRONG — image shows exceeds)
- Formula: wrote "F_s = (F_i - F_t) / 4" with wrong variable names
- Variable definitions: swapped F_s and F_t meanings
- Temperature: said "thermal oil temperature maintained at 0.25C" (WRONG — thermocouple calibrated to +/-0.25C)
- Calibration item 1: said "heating device" (WRONG — image says thermocouple)

Source B (Trinity) was correct on all those points.

Correct merged output (abbreviated):
{
  "page_type": "mixed",
  "page_header": "Chapter 2  Design of Hydraulic Cylinders and Basic Components  189",
  "sections": [
    {"type": "heading", "level": 2, "text": "4. Measurement Methods and Instruments"},
    {"type": "heading", "level": 3, "text": "(1) Leakage"},
    {"type": "paragraph", "text": "Before each test, a measuring cylinder with a range of 10 mL and an accuracy of 0.1 mL should be prepared. If the test leakage exceeds 10 mL, a measuring cylinder with a larger range and an accuracy of 1 mL should be prepared."},
    {"type": "formula", "text": "Fs = (Ft - F1) / 4    (2-56)"},
    {"type": "paragraph", "text": "Where: Fs — average friction force of a single test sealing element during the forward and return stroke; F1 — sum of the inherent friction forces of the test device during the forward and return stroke; Ft — sum of the friction forces of the two test sealing elements and the test device during the forward and return stroke."},
    {"type": "heading", "level": 3, "text": "(5) Temperature Measurement"},
    {"type": "paragraph", "text": "The thermocouple should be installed according to the requirements shown in Figure 2-10 and be able to withstand the maximum circuit pressure. The thermocouple should be calibrated to +/-0.25C."},
    {"type": "list", "ordered": true, "items": [
      "Test temperature thermocouple.",
      "Test pressure gauge.",
      "Test pressure sensor.",
      "Test friction force sensor.",
      "Surface roughness measuring instrument."
    ]}
  ]
}

Key decisions: Used Trinity's "exceeds 10mL" over VLM's "less than 10mL". Used Trinity's formula variable names Fs, F1, Ft with correct definitions. Used "thermocouple" over "heating device". Kept VLM's heading structure."""


def cmd_merge():
    """Merge VLM descriptions with Trinity translations using Sonnet."""
    print("Merging VLM descriptions with Trinity translations (Sonnet)...")
    assert ANTHROPIC_API_KEY, "Set ANTHROPIC_API_KEY environment variable"

    import anthropic
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    descs = _load_json(DESCRIPTIONS_FILE)
    trans = _load_json(TRANSLATIONS_FILE)
    merged = _load_json(MERGED_FILE)

    # Find pages that have BOTH a valid description AND a translation
    both_pages = []
    desc_only = []
    trans_only = []
    for pg in sorted(set(descs.keys()) | set(trans.keys()), key=lambda x: int(x)):
        has_desc = pg in descs and descs[pg].get("page_type") not in ("error", None)
        has_trans = pg in trans and trans[pg].strip()
        if has_desc and has_trans:
            both_pages.append(pg)
        elif has_desc:
            desc_only.append(pg)
        elif has_trans:
            trans_only.append(pg)

    # Pages already merged
    done = set(merged.keys())
    todo = [pg for pg in both_pages if pg not in done]

    # Pages with only one source don't need merging — copy them through
    for pg in desc_only:
        if pg not in merged:
            merged[pg] = descs[pg]
    for pg in trans_only:
        if pg not in merged:
            # Wrap plain text in a minimal structure
            merged[pg] = {
                "page_type": "text",
                "page_header": "",
                "sections": [{"type": "paragraph", "text": p.strip()}
                             for p in re.split(r"\n\s*\n", trans[pg]) if p.strip()]
            }

    print(f"  Both sources: {len(both_pages)} pages")
    print(f"  VLM only: {len(desc_only)}, Trinity only: {len(trans_only)}")
    print(f"  Already merged: {len(done)}, remaining: {len(todo)}")

    if not todo:
        _save_json(MERGED_FILE, merged)
        print("  All pages merged.")
        return

    total_input = 0
    total_output = 0
    success = 0
    failed = 0
    failed_pages = []

    for i, pg in enumerate(todo):
        img_path = find_img_path(int(pg))
        if not img_path:
            # No image — just use VLM description as-is
            merged[pg] = descs[pg]
            success += 1
            continue

        print(f"  Page {pg} ({i+1}/{len(todo)})...", end=" ", flush=True)

        with open(img_path, "rb") as f:
            img_data = base64.standard_b64encode(f.read()).decode()

        source_a = json.dumps(descs[pg], indent=2, ensure_ascii=False)
        source_b = trans[pg]

        user_content = [
            {"type": "text", "text": MERGE_PROMPT},
            {"type": "text", "text": f"--- SOURCE A (VLM JSON) ---\n{source_a}"},
            {"type": "text", "text": f"--- SOURCE B (Trinity plain text) ---\n{source_b}"},
            {"type": "text", "text": "--- ORIGINAL PAGE IMAGE ---"},
            {"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": img_data}},
            {"type": "text", "text": "Now produce the merged JSON. Output ONLY the JSON object."},
        ]

        try:
            response = client.messages.create(
                model=MERGE_MODEL,
                max_tokens=8192,
                temperature=0.1,
                messages=[{"role": "user", "content": user_content}],
            )

            total_input += response.usage.input_tokens
            total_output += response.usage.output_tokens

            text = response.content[0].text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

            parsed = json.loads(text)
            merged[pg] = parsed
            success += 1

            # Check for flagged corrections
            flagged = []
            for s in parsed.get("sections", []):
                if "_flagged" in s:
                    flagged.append(s["_flagged"])

            status = f"ok — {parsed.get('page_type', '?')}"
            if flagged:
                status += f" [{len(flagged)} flagged]"
            print(status)

            if success % 10 == 0:
                _save_json(MERGED_FILE, merged)
                cost_so_far = (total_input * 3.0 + total_output * 15.0) / 1_000_000
                print(f"    [Saved — {success} merged, ${cost_so_far:.2f} so far]")

        except json.JSONDecodeError as e:
            print(f"JSON error: {e}")
            # Fall back to VLM description
            merged[pg] = descs[pg]
            merged[pg]["_merge_error"] = str(e)
            failed += 1
            failed_pages.append(pg)

        except Exception as e:
            print(f"error: {e}")
            failed += 1
            failed_pages.append(pg)
            time.sleep(2)

    # Final save
    _save_json(MERGED_FILE, merged)

    cost_in = total_input * 3.0 / 1_000_000
    cost_out = total_output * 15.0 / 1_000_000
    total_cost = cost_in + cost_out

    # Count flagged sections across all merged pages
    total_flagged = 0
    for pg_data in merged.values():
        for s in pg_data.get("sections", []) if isinstance(pg_data, dict) else []:
            if "_flagged" in s:
                total_flagged += 1

    print(f"\n{'='*60}")
    print(f"MERGE COMPLETE")
    print(f"  Success: {success}, Failed: {failed}")
    print(f"  Tokens: {total_input:,} input + {total_output:,} output")
    print(f"  Cost: ${cost_in:.2f} input + ${cost_out:.2f} output = ${total_cost:.2f} total")
    print(f"  Flagged corrections: {total_flagged}")
    if failed_pages:
        print(f"  Failed pages: {failed_pages}")
    print(f"  Output: {MERGED_FILE} ({len(merged)} pages)")
    print(f"{'='*60}")


# ====================================================================
#  CLI
# ====================================================================
def main():
    parser = argparse.ArgumentParser(description="Book Translation Pipeline")
    parser.add_argument("command",
        choices=["status", "extract", "describe-pages", "merge", "build", "qa", "fix"])
    parser.add_argument("--sample", type=int, default=None)
    args = parser.parse_args()

    cmds = {
        "status": cmd_status,
        "extract": cmd_extract,
        "describe-pages": cmd_describe_pages,
        "merge": cmd_merge,
        "build": cmd_build,
        "qa": lambda: cmd_qa(args.sample),
        "fix": cmd_fix,
    }
    cmds[args.command]()


if __name__ == "__main__":
    main()
