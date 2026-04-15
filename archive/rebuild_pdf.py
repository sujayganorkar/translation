"""
PDF Rebuilder - English Edition of Hydraulic Cylinder Manual

Architecture: new blank document, two page strategies:
  TEXT pages  (few/no drawings): clean English text on blank page
  VISUAL pages (tables/diagrams): high-DPI raster background + white-rect CJK + English overlay
  EMPTY/LIGHT pages: raster copy of original (no CJK to replace)
"""

import json
import os
import re
import sys
import fitz  # PyMuPDF
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')

# ── Config ──────────────────────────────────────────────────────────────────
RENDER_DPI       = 200                          # for visual pages
RENDER_SCALE     = RENDER_DPI / 72.0            # ≈ 2.78
FONT_FILE_BODY   = "C:/Windows/Fonts/arial.ttf"
FONT_FILE_NARROW = "C:/Windows/Fonts/ARIALN.TTF"
BODY_FONT_SIZE   = 10                           # clean text pages
MIN_FONT         = 5
RECT_EXPAND_DOWN = 20
MARGIN           = 54                           # 0.75 inch margins for text pages
SAVE_INTERVAL    = 50
OUTPUT_FILE      = "hydraulic_cylinder_manual_ENGLISH.pdf"
PROGRESS_FILE    = "rebuild_progress.json"
DRAWING_THRESHOLD = 10                          # pages with ≥ this many drawings = visual
# ────────────────────────────────────────────────────────────────────────────

CJK_RE      = re.compile(r"[\u4e00-\u9fff]")
SENT_END_ZH = re.compile(r"[。！？；\n]+")


# ── Helpers ─────────────────────────────────────────────────────────────────

def has_cjk(text: str) -> bool:
    return bool(CJK_RE.search(text))


# ── Extraction ──────────────────────────────────────────────────────────────

def get_text_blocks(page):
    """Return ALL text blocks as list of (x0, y0, x1, y1, text, size)."""
    blocks = []
    for block in page.get_text("dict")["blocks"]:
        if block.get("type") != 0:
            continue
        all_text = ""
        sizes = []
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                all_text += span["text"]
                sizes.append(span["size"])
        all_text = all_text.strip()
        if not all_text:
            continue
        sizes.sort()
        blocks.append({
            "rect": fitz.Rect(block["bbox"]),
            "text": all_text,
            "size": sizes[len(sizes) // 2] if sizes else 10.0,
        })
    return blocks


def merge_blocks(blocks, max_gap_ratio=0.8):
    """Merge vertically adjacent blocks into groups."""
    if not blocks:
        return []
    sorted_b = sorted(blocks, key=lambda b: (b["rect"].y0, b["rect"].x0))
    groups = []
    cur_blocks = [sorted_b[0]]
    cur_rect = fitz.Rect(sorted_b[0]["rect"])

    for b in sorted_b[1:]:
        r = b["rect"]
        gap = r.y0 - cur_rect.y1
        avg_h = cur_rect.height / len(cur_blocks)
        x_overlap = min(r.x1, cur_rect.x1) - max(r.x0, cur_rect.x0)
        if gap < avg_h * max_gap_ratio and x_overlap > 20:
            cur_blocks.append(b)
            cur_rect = cur_rect | r
        else:
            groups.append(_finalize_group(cur_rect, cur_blocks))
            cur_blocks = [b]
            cur_rect = fitz.Rect(r)

    groups.append(_finalize_group(cur_rect, cur_blocks))
    return groups


def _finalize_group(rect, blocks):
    combined_text = "".join(b["text"] for b in blocks)
    sizes = [b["size"] for b in blocks]
    sizes.sort()
    return {
        "rect": rect,
        "text": combined_text,
        "size": sizes[len(sizes) // 2],
    }


# ── Classification ──────────────────────────────────────────────────────────

def classify_page(orig_page) -> str:
    """Returns: 'empty' | 'light' | 'text' | 'visual'"""
    blocks = get_text_blocks(orig_page)
    if not blocks:
        return "empty"
    cjk_blocks = [b for b in blocks if has_cjk(b["text"])]
    if not cjk_blocks:
        return "light"
    num_drawings = len(orig_page.get_drawings())
    if num_drawings >= DRAWING_THRESHOLD:
        return "visual"
    return "text"


# ── Translation alignment ──────────────────────────────────────────────────

def _zh_sentence_count(text: str) -> int:
    parts = SENT_END_ZH.split(text.strip())
    return max(1, sum(1 for p in parts if p.strip()))


def align_translation(english_text: str, groups: list) -> list:
    """Align English paragraphs to merged CJK groups by sentence proportion."""
    if not groups or not english_text.strip():
        return [""] * len(groups)
    if len(groups) == 1:
        return [english_text.strip()]

    en_paras = re.split(r"\n\s*\n", english_text.strip())
    en_paras = [p.strip() for p in en_paras if p.strip()]
    if not en_paras:
        return [english_text.strip()] + [""] * (len(groups) - 1)

    zh_counts = [_zh_sentence_count(g["text"]) for g in groups]
    total_zh = sum(zh_counts)
    total_en = len(en_paras)
    chunks = []
    para_idx = 0

    for i, count in enumerate(zh_counts):
        if i == len(zh_counts) - 1:
            chunks.append("\n\n".join(en_paras[para_idx:]))
        else:
            n = max(1, round((count / total_zh) * total_en))
            chunks.append("\n\n".join(en_paras[para_idx : para_idx + n]))
            para_idx = min(para_idx + n, total_en)

    return chunks


# ── Text insertion ──────────────────────────────────────────────────────────

def _try_insert(page, rect, text, fontsize, font_file):
    return page.insert_textbox(
        rect, text,
        fontsize=fontsize, fontfile=font_file,
        color=(0, 0, 0), align=0,
    )


def _find_best_fontsize(page, rect, text, font_file, max_size, min_size=MIN_FONT):
    """Binary search for largest font size that fits."""
    low, high = min_size, max_size
    best = -1
    while low <= high:
        mid = (low + high) // 2
        rc = _try_insert(page, rect, text, mid, font_file)
        if rc >= 0:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return best


def insert_text(page, rect: fitz.Rect, text: str, base_size: float, page_rect: fitz.Rect):
    """Insert text with binary-search font fitting + downward expansion."""
    if not text.strip():
        return
    inner = fitz.Rect(rect.x0 + 1, rect.y0 + 1, rect.x1 - 1, rect.y1 - 1)
    if inner.is_empty or inner.width < 4 or inner.height < 4:
        return

    font_file = FONT_FILE_NARROW if (rect.height <= 14) else FONT_FILE_BODY
    max_fs = max(int(base_size), MIN_FONT)

    best = _find_best_fontsize(page, inner, text, font_file, max_fs)
    if best >= MIN_FONT:
        _try_insert(page, inner, text, best, font_file)
        return

    expanded = fitz.Rect(inner.x0, inner.y0, inner.x1,
                         min(page_rect.y1 - 2, inner.y1 + RECT_EXPAND_DOWN))
    best = _find_best_fontsize(page, expanded, text, font_file, max_fs)
    if best >= MIN_FONT:
        _try_insert(page, expanded, text, best, font_file)
        return

    _try_insert(page, inner, text, MIN_FONT, font_file)


# ── Page builders ───────────────────────────────────────────────────────────

def build_text_page(new_doc, orig_page, translation: str):
    """Clean English text on a blank page. No rasterization."""
    w, h = orig_page.rect.width, orig_page.rect.height
    new_page = new_doc.new_page(width=w, height=h)

    if not translation or not translation.strip():
        return

    text_rect = fitz.Rect(MARGIN, MARGIN, w - MARGIN, h - MARGIN)
    best = _find_best_fontsize(new_page, text_rect, translation.strip(),
                                FONT_FILE_BODY, BODY_FONT_SIZE)
    if best >= MIN_FONT:
        _try_insert(new_page, text_rect, translation.strip(), best, FONT_FILE_BODY)
    else:
        _try_insert(new_page, text_rect, translation.strip(), MIN_FONT, FONT_FILE_BODY)


def build_visual_page(new_doc, orig_page, translation: str):
    """Raster background + white-rect CJK blocks + English overlay."""
    w, h = orig_page.rect.width, orig_page.rect.height
    new_page = new_doc.new_page(width=w, height=h)

    # 1. Rasterize original as background
    mat = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
    pix = orig_page.get_pixmap(matrix=mat, alpha=False)
    new_page.insert_image(orig_page.rect, pixmap=pix)

    if not translation or not translation.strip():
        return

    # 2. Get CJK text blocks, merge into groups
    all_blocks = get_text_blocks(orig_page)
    cjk_blocks = [b for b in all_blocks if has_cjk(b["text"])]
    if not cjk_blocks:
        return

    groups = merge_blocks(cjk_blocks)

    # 3. White-rect every CJK block (covers all text, gaps between spans too)
    for b in cjk_blocks:
        r = fitz.Rect(b["rect"].x0 - 1, b["rect"].y0 - 1,
                       b["rect"].x1 + 1, b["rect"].y1 + 1)
        new_page.draw_rect(r, color=(1, 1, 1), fill=(1, 1, 1), width=0)

    # 4. Re-insert non-CJK blocks that overlap with CJK groups
    #    (numeric data in tables that got covered by white rects)
    non_cjk_blocks = [b for b in all_blocks if not has_cjk(b["text"])]
    for b in non_cjk_blocks:
        # Check if this block overlaps any CJK block rect
        overlaps = any(b["rect"].intersects(c["rect"]) for c in cjk_blocks)
        if not overlaps:
            continue
        # Re-insert original text
        font_file = FONT_FILE_NARROW if b["rect"].height <= 14 else FONT_FILE_BODY
        fs = max(MIN_FONT, int(b["size"]))
        inner = fitz.Rect(b["rect"].x0 + 1, b["rect"].y0 + 1,
                           b["rect"].x1 - 1, b["rect"].y1 - 1)
        if not inner.is_empty and inner.width >= 4 and inner.height >= 4:
            _try_insert(new_page, inner, b["text"], fs, font_file)

    # 5. Insert English translation into merged group rects
    chunks = align_translation(translation, groups)
    for group, chunk in zip(groups, chunks):
        if chunk.strip():
            insert_text(new_page, group["rect"], chunk, group["size"], new_page.rect)


def build_copy_page(new_doc, orig_page):
    """Raster copy — for empty/light pages with no CJK to replace."""
    w, h = orig_page.rect.width, orig_page.rect.height
    new_page = new_doc.new_page(width=w, height=h)
    mat = fitz.Matrix(RENDER_SCALE, RENDER_SCALE)
    pix = orig_page.get_pixmap(matrix=mat, alpha=False)
    new_page.insert_image(orig_page.rect, pixmap=pix)


# ── Main rebuild dispatch ──────────────────────────────────────────────────

def rebuild_page(new_doc, orig_page, translation: str) -> str:
    """Build one page into new_doc. Returns page class for logging."""
    page_class = classify_page(orig_page)

    if page_class == "text":
        build_text_page(new_doc, orig_page, translation)
    elif page_class == "visual":
        build_visual_page(new_doc, orig_page, translation)
    else:
        # empty or light — just copy the original
        build_copy_page(new_doc, orig_page)

    return page_class


# ── Progress ────────────────────────────────────────────────────────────────

def load_progress() -> set:
    if Path(PROGRESS_FILE).exists():
        with open(PROGRESS_FILE, "r") as f:
            return set(json.load(f))
    return set()


def save_progress(done: set):
    with open(PROGRESS_FILE, "w") as f:
        json.dump(sorted(done), f)


def find_source_pdf() -> str:
    exclude = {"english", "output", "test", "rebuild", "final"}
    pdfs = [
        p for p in Path(".").glob("*.pdf")
        if not any(kw in p.name.lower() for kw in exclude)
    ]
    if not pdfs:
        raise FileNotFoundError("No source PDF found in current directory")
    return str(max(pdfs, key=lambda p: p.stat().st_size))


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    print("=" * 65)
    print("PDF REBUILDER — English Edition")
    print("=" * 65)

    pdf_path = find_source_pdf()
    print(f"Source PDF : {pdf_path}")
    print(f"Output     : {OUTPUT_FILE}")

    with open("translations.json", "r", encoding="utf-8") as f:
        translations = json.load(f)
    print(f"Translations: {len(translations)} pages loaded")

    done_pages = load_progress()

    src = fitz.open(pdf_path)
    total = len(src)

    # Resume: load existing output or start fresh
    out_path = Path(OUTPUT_FILE)
    if out_path.exists() and done_pages:
        new_doc = fitz.open(str(out_path))
        print(f"Resuming   : {len(done_pages)} pages already built ({len(new_doc)} in output)")
    else:
        new_doc = fitz.open()
        done_pages = set()
        print("Starting fresh build")

    print(f"\nBuilding {total} pages...\n")
    errors = []
    class_counts = {"text": 0, "visual": 0, "empty": 0, "light": 0}

    for page_idx in range(total):
        page_num = page_idx + 1

        if page_num in done_pages:
            continue

        orig_page = src[page_idx]
        translation = translations.get(str(page_num), "")

        try:
            pc = rebuild_page(new_doc, orig_page, translation)
            class_counts[pc] = class_counts.get(pc, 0) + 1
            done_pages.add(page_num)
        except Exception as e:
            errors.append((page_num, str(e)))
            print(f"  ERROR page {page_num}: {e}")
            # Insert blank placeholder
            new_doc.new_page(width=orig_page.rect.width, height=orig_page.rect.height)
            done_pages.add(page_num)

        if page_num % 10 == 0:
            pct = page_num / total * 100
            print(f"  Page {page_num}/{total} ({pct:.0f}%)")

        if page_num % SAVE_INTERVAL == 0:
            print(f"  [Checkpoint at page {page_num}...]")
            new_doc.save(OUTPUT_FILE, deflate=True)
            save_progress(done_pages)

    print("\nSaving final output...")
    new_doc.save(OUTPUT_FILE, garbage=4, deflate=True)
    save_progress(done_pages)
    new_doc.close()
    src.close()

    print("\n" + "=" * 65)
    print(f"DONE — {len(done_pages)}/{total} pages built")
    print(f"  text={class_counts['text']}  visual={class_counts['visual']}  "
          f"empty={class_counts['empty']}  light={class_counts['light']}")
    print(f"Output: {OUTPUT_FILE}")
    if errors:
        print(f"Errors on {len(errors)} pages: {[e[0] for e in errors]}")
    print("=" * 65)


if __name__ == "__main__":
    main()
