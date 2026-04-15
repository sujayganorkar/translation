"""
Automated translation pipeline using OpenRouter (free model)
Translates 1 page at a time - no PAGE SEPARATOR parsing needed
"""

import json
import os
import sys
import time
import threading
import urllib.request
import urllib.error
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

sys.stdout.reconfigure(encoding='utf-8')

API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
MODEL = "arcee-ai/trinity-large-preview:free"
URL = "https://openrouter.ai/api/v1/chat/completions"


HARD_TIMEOUT = 90  # seconds per attempt — thread is killed after this


def _call_api(payload, result_box):
    """Run in a thread; stores result or exception in result_box."""
    try:
        req = urllib.request.Request(URL, data=payload, headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://localhost",
            "X-Title": "PDF Translator"
        })
        with urllib.request.urlopen(req, timeout=HARD_TIMEOUT) as resp:
            result_box[0] = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        result_box[1] = e


def translate_page(text, retries=3):
    """Send a single page to OpenRouter for translation with hard wall-clock timeout."""
    payload = json.dumps({
        "model": MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a professional technical translator. "
                    "Translate the provided Chinese text to English accurately. "
                    "Preserve all formatting, section headers, numbers, and technical terms. "
                    "Do not add explanations or notes. Only output the translated text."
                )
            },
            {
                "role": "user",
                "content": f"Translate the following Chinese technical text to English:\n\n{text}"
            }
        ]
    }).encode("utf-8")

    for attempt in range(retries):
        result_box = [None, None]  # [response, exception]
        t = threading.Thread(target=_call_api, args=(payload, result_box), daemon=True)
        t.start()
        t.join(timeout=HARD_TIMEOUT)

        if t.is_alive():
            print(f"   Attempt {attempt+1} timed out after {HARD_TIMEOUT}s")
            if attempt < retries - 1:
                time.sleep(3)
            continue

        if result_box[1] is not None:
            err = result_box[1]
            if isinstance(err, urllib.error.HTTPError):
                body = err.read().decode("utf-8")
                print(f"   HTTP {err.code}: {body[:200]}")
                if err.code == 429:
                    print(f"   Rate limited, waiting 15s...")
                    time.sleep(15)
                elif attempt < retries - 1:
                    time.sleep(5)
            else:
                print(f"   Attempt {attempt+1} failed: {err}")
                if attempt < retries - 1:
                    time.sleep(5)
            continue

        res = result_box[0]
        if res:
            return res["choices"][0]["message"]["content"]

    return None


def load_progress():
    if Path("translations.json").exists():
        with open("translations.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_progress(translations):
    with open("translations.json", "w", encoding="utf-8") as f:
        json.dump(translations, f, ensure_ascii=False, indent=2)


def get_all_pages():
    with open("manual_extracted.json", "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["pages"]


def main():
    if not API_KEY:
        print("ERROR: Set OPENROUTER_API_KEY environment variable")
        sys.exit(1)

    print("=" * 60)
    print("TRANSLATION PIPELINE - 1 PAGE AT A TIME")
    print(f"Model: {MODEL}")
    print("=" * 60)

    all_pages = get_all_pages()
    total_pages = len(all_pages)

    translations = load_progress()
    already_done = len(translations)
    print(f"\nProgress: {already_done}/{total_pages} pages already translated")

    pages_todo = [p for p in all_pages if str(p["page_number"]) not in translations]
    print(f"Pages remaining: {len(pages_todo)}")

    for idx, page in enumerate(pages_todo):
        page_num = page["page_number"]
        text = page["raw_text"].strip()

        if not text:
            translations[str(page_num)] = "[Empty page]"
            save_progress(translations)
            print(f"Page {page_num}: empty, skipped")
            continue

        print(f"Page {page_num} ({idx+1}/{len(pages_todo)}) | {len(text):,} chars...", end=" ", flush=True)

        start = time.time()
        translated = translate_page(text)
        elapsed = time.time() - start

        if translated:
            translations[str(page_num)] = translated
            save_progress(translations)
            print(f"done in {elapsed:.1f}s | Total: {len(translations)}/{total_pages}")
        else:
            print(f"FAILED - skipping")

        time.sleep(0.5)

    print("\n" + "=" * 60)
    print(f"TRANSLATION COMPLETE: {len(translations)}/{total_pages} pages")
    print("Output: translations.json")
    print("=" * 60)


if __name__ == "__main__":
    main()
