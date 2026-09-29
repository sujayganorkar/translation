# chinese-pdf-translator

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/)
[![Anthropic](https://img.shields.io/badge/API-Anthropic-purple)](https://www.anthropic.com/)

A high-quality, resumable pipeline that translates Chinese technical PDFs to English using a dual-source approach — combining VLM image analysis with free text translation, then using Claude Sonnet to merge and reconcile the two sources.

---

## How It Works

```
Chinese PDF
    │
    ├──► extract          → page_images/*.jpg           (rasterize pages)
    │
    ├──► extract_and_prepare.py → manual_extracted.json (raw text via pypdf)
    │         │
    │         ▼
    │     translate_all.py → translations.json           (free OpenRouter model)
    │
    ├──► describe-pages   → page_descriptions.json       (Haiku VLM: structured JSON)
    │
    ├──► merge            → page_descriptions_merged.json (Sonnet: best-of-both)
    │
    └──► build            → <source>_EN.pdf              (final English PDF)
```

The pipeline is **resumable at every step** — safe to interrupt and restart.

---

## Features

- **Dual-source translation**: VLM (vision) captures document structure (tables, diagrams, formulas); text extraction captures accurate content
- **AI-powered merge**: Claude Sonnet reconciles conflicts, preferring the more accurate source per section
- **Handles technical content**: Tables with 100+ rows, engineering formulas, standards codes (GB/T, ISO, JB/T)
- **Async parallel VLM**: Processes ~8 pages concurrently with rate-limit backoff
- **QA pass**: Claude Haiku reviews original vs. translation and flags pages to re-do
- **JSON repair**: Recovers partial results from max-token cutoffs

---

## Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/sujayganorkar/translation.git
cd translation
pip install -r requirements.txt
```

Python 3.10+ is required.

### 2. Configure API keys

```bash
cp .env.example .env
# Edit .env and add your keys
```

| Variable | Required For | Get It At |
|----------|-------------|-----------|
| `ANTHROPIC_API_KEY` | `describe-pages`, `merge`, `qa` | [console.anthropic.com](https://console.anthropic.com) |
| `OPENROUTER_API_KEY` | `translate_all.py` | [openrouter.ai](https://openrouter.ai) |

The OpenRouter key is optional — it's only needed for the free text translation step. You can skip `translate_all.py` and run `describe-pages` + `merge` directly.

### 3. Place your PDF

Put your Chinese PDF in the project directory. The pipeline auto-detects the largest `.pdf` file (excluding files with `english`, `output`, `test`, `rebuild`, `final`, or `handbook` in the name).

---

## Usage

```bash
# Step 0: Check status at any point
python book_pipeline.py status

# Step 1: Rasterize PDF pages to images
python book_pipeline.py extract

# Step 2a: VLM structured descriptions (uses ANTHROPIC_API_KEY)
python book_pipeline.py describe-pages

# Step 2b (optional): Free text translation (uses OPENROUTER_API_KEY)
python extract_and_prepare.py   # extract raw text
python translate_all.py         # translate via OpenRouter

# Step 3: Merge VLM + text translation (uses ANTHROPIC_API_KEY)
python book_pipeline.py merge

# Step 4: Build the English PDF
python book_pipeline.py build

# Optional: QA review
python book_pipeline.py qa --sample 50   # spot-check 50 pages
python book_pipeline.py fix              # remove flagged pages for re-run
```

---

## Commands Reference

| Command | Script | Description |
|---------|--------|-------------|
| `status` | `book_pipeline.py` | Progress overview |
| `extract` | `book_pipeline.py` | Rasterize PDF pages to 150 DPI JPEG |
| `describe-pages` | `book_pipeline.py` | Send each page image to Claude Haiku for structured JSON |
| `merge` | `book_pipeline.py` | Use Claude Sonnet to merge VLM + text translation |
| `build` | `book_pipeline.py` | Render structured descriptions into an English PDF |
| `qa [--sample N]` | `book_pipeline.py` | QA: compare original vs translation via VLM |
| `fix` | `book_pipeline.py` | Remove QA-flagged pages for re-translation |
| _(standalone)_ | `extract_and_prepare.py` | Extract raw text from PDF via pypdf |
| _(standalone)_ | `translate_all.py` | Translate extracted text via OpenRouter (free model) |

---

## Output Files

| File | Description |
|------|-------------|
| `page_images/` | Rasterized page images (gitignored) |
| `page_descriptions.json` | VLM structured descriptions (gitignored) |
| `page_descriptions_merged.json` | Final merged descriptions (gitignored) |
| `translations.json` | Text-model translations fallback (gitignored) |
| `<source>_EN.pdf` | Final English PDF output |

All generated data files are gitignored. Only code and prompts are tracked.

---

## Estimated Costs

For a ~2,000 page technical book using Claude Haiku (describe-pages) + Claude Sonnet (merge):

| Step | Model | Approx Cost |
|------|-------|-------------|
| `describe-pages` | claude-haiku-4-5 | ~$5–10 |
| `merge` | claude-sonnet-4 | ~$15–25 |
| `qa` (optional) | claude-haiku-4-5 | ~$1–2 |

Text translation via OpenRouter is free (rate-limited).

---

## Legal Notice

This tool is designed to translate PDFs of technical documents **that you own or have the right to translate**. Do not use this tool to reproduce or distribute copyrighted material. The authors are not responsible for any copyright infringement by users of this software.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

---

## License

[MIT](LICENSE) © 2026 Sujay Ganorkar
