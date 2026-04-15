# Chinese PDF → English Translation Pipeline

Translates Chinese technical books to English using a dual-source approach:
1. **Text extraction + free API translation** (fast, accurate text)
2. **VLM image analysis** (preserves document structure: tables, figures, formulas)
3. **AI-powered merge** (combines the best of both sources)

---

## Quick Start

1. Place your Chinese PDF in this directory
2. Set environment variables:
   ```bash
   export ANTHROPIC_API_KEY="sk-ant-..."
   export OPENROUTER_API_KEY="sk-or-..."  # optional, for free text translation
   ```
3. Run the pipeline:
   ```bash
   python book_pipeline.py extract          # Rasterize pages to images
   python book_pipeline.py describe-pages   # Haiku VLM → structured descriptions
   python book_pipeline.py build            # Build English PDF
   ```

---

## Pipeline Overview

```
Chinese PDF
    │
    ├──► book_pipeline.py extract        → page_images/*.jpg
    │
    ├──► extract_and_prepare.py          → manual_extracted.json (raw text)
    │        │
    │        ▼
    │    translate_all.py                 → translations.json (free model translation)
    │
    ├──► book_pipeline.py describe-pages → page_descriptions.json (VLM structured)
    │
    ├──► book_pipeline.py merge          → page_descriptions_merged.json (best of both)
    │
    └──► book_pipeline.py build          → <source>_EN.pdf
```

---

## Scripts

### `book_pipeline.py` — Main Pipeline
All-in-one tool with subcommands:

| Command | Description |
|---------|-------------|
| `status` | Progress overview |
| `extract` | Rasterize PDF pages to 150 DPI JPEG images |
| `describe-pages` | Send each page image to Haiku VLM for structured JSON description |
| `merge` | Sonnet merges VLM descriptions + text translations, resolves conflicts |
| `build` | Build English PDF from merged descriptions |
| `qa [--sample N]` | QA: compare original vs translation |
| `fix` | Remove QA-flagged pages for re-translation |

### `extract_and_prepare.py` — Text Extraction
Extracts raw text from PDF using PyPDF. Auto-detects the source PDF.

### `translate_all.py` — Free Model Translation
Translates extracted text 1 page at a time via OpenRouter (free model). Resumable.

---

## Environment Variables

| Variable | Required For | Description |
|----------|-------------|-------------|
| `ANTHROPIC_API_KEY` | `describe-pages`, `merge`, `qa` | Anthropic API key for Haiku/Sonnet |
| `OPENROUTER_API_KEY` | `translate_all.py` | OpenRouter API key for free translation model |

---

## Requirements

```bash
pip install pymupdf pypdf reportlab anthropic
```

Python 3.10+ required.
