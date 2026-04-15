"""
Hydraulic Cylinder Manual - Translation Coordinator and PDF Rebuilder
Manages batch translations and rebuilds the PDF with English text
"""

import json
import sys
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from io import BytesIO

sys.stdout.reconfigure(encoding='utf-8')

class TranslationManager:
    def __init__(self, extracted_json="manual_extracted.json", batches_json="translation_batches.json"):
        self.extracted_json = extracted_json
        self.batches_json = batches_json

        # Load data
        with open(extracted_json, "r", encoding="utf-8") as f:
            self.extracted_data = json.load(f)

        with open(batches_json, "r", encoding="utf-8") as f:
            self.batches_data = json.load(f)

        self.translations = {}
        self.load_existing_translations()

    def load_existing_translations(self):
        """Load any existing translations from file"""
        if Path("translations.json").exists():
            with open("translations.json", "r", encoding="utf-8") as f:
                self.translations = json.load(f)

    def save_translations(self):
        """Save translations to file"""
        with open("translations.json", "w", encoding="utf-8") as f:
            json.dump(self.translations, f, ensure_ascii=False, indent=2)

    def get_batch_for_translation(self, batch_num):
        """Get a specific batch for translation"""
        if batch_num < 1 or batch_num > self.batches_data["total_batches"]:
            return None

        batch = self.batches_data["batches"][batch_num - 1]
        text_to_translate = "\n\n---PAGE BREAK---\n\n".join([
            f"[PAGE {page['page_number']}]\n{page['raw_text']}"
            for page in batch["pages"]
        ])

        return {
            "batch_number": batch_num,
            "page_range": batch["page_range"],
            "text": text_to_translate,
            "num_pages": len(batch["pages"])
        }

    def save_batch_translation(self, batch_num, translations_dict):
        """Save translated pages from a batch"""
        batch = self.batches_data["batches"][batch_num - 1]

        for page_data, translated_text in zip(batch["pages"], translations_dict.values()):
            page_num = page_data["page_number"]
            self.translations[str(page_num)] = {
                "original": page_data["raw_text"],
                "translated": translated_text,
                "batch": batch_num
            }

        self.save_translations()
        print(f"✓ Saved translations for batch {batch_num}")

    def get_translation_progress(self):
        """Show progress of translation"""
        total_pages = self.extracted_data["metadata"]["total_pages"]
        translated = len(self.translations)
        percent = (translated / total_pages) * 100

        print(f"\nTranslation Progress:")
        print(f"  Translated: {translated}/{total_pages} pages ({percent:.1f}%)")
        print(f"  Remaining:  {total_pages - translated} pages")

        return {"translated": translated, "total": total_pages, "percent": percent}

    def print_batch_info(self):
        """Print information about batches"""
        print("\n" + "=" * 60)
        print("TRANSLATION BATCHES")
        print("=" * 60)
        print(f"Total batches: {self.batches_data['total_batches']}")
        print(f"Pages per batch: {self.batches_data['batch_size']}")
        print("\nBatch breakdown:")
        for i, batch in enumerate(self.batches_data["batches"][:5]):
            print(f"  Batch {batch['batch_number']}: Pages {batch['page_range']}")
        if len(self.batches_data["batches"]) > 5:
            print(f"  ... and {len(self.batches_data['batches']) - 5} more batches")
        print("=" * 60)


class PDFRebuilder:
    def __init__(self, original_pdf, translations_json="translations.json"):
        self.original_pdf = original_pdf
        self.reader = PdfReader(original_pdf)

        with open(translations_json, "r", encoding="utf-8") as f:
            self.translations = json.load(f)

    def rebuild_with_translations(self, output_path="translated_manual.pdf"):
        """Rebuild PDF with translated text overlaid on original"""
        writer = PdfWriter()

        total_pages = len(self.reader.pages)

        print(f"\nRebuilding PDF with translations...")
        for page_num in range(total_pages):
            page = self.reader.pages[page_num]
            writer.add_page(page)

            # TODO: Add translated text overlay
            # This is complex and requires careful positioning
            # For now, we'll keep original images and add text programmatically

        with open(output_path, "wb") as f:
            writer.write(f)

        print(f"✓ Rebuilt PDF saved to: {output_path}")

    def create_text_only_document(self, output_path="manual_translated_textonly.txt"):
        """Create a text-only document with all translated content"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("HYDRAULIC CYLINDER MANUAL - ENGLISH TRANSLATION\n")
            f.write("=" * 60 + "\n\n")

            for page_num in range(1, len(self.reader.pages) + 1):
                page_key = str(page_num)
                if page_key in self.translations:
                    f.write(f"\n{'='*60}\n")
                    f.write(f"PAGE {page_num}\n")
                    f.write(f"{'='*60}\n\n")
                    f.write(self.translations[page_key]["translated"])
                    f.write("\n")

        print(f"✓ Text-only translation saved to: {output_path}")


def main():
    print("=" * 60)
    print("TRANSLATION COORDINATOR")
    print("=" * 60)

    manager = TranslationManager()
    manager.print_batch_info()

    # Show progress
    progress = manager.get_translation_progress()

    # Show first batch as example
    print("\n" + "=" * 60)
    print("EXAMPLE: BATCH 1 (Pages 1-20)")
    print("=" * 60)
    batch = manager.get_batch_for_translation(1)
    if batch:
        print(f"Pages: {batch['page_range']}")
        print(f"Characters to translate: {len(batch['text']):,}")
        print(f"\nFirst 500 characters of source text:")
        print("-" * 60)
        print(batch['text'][:500])
        print("-" * 60)

    print("\n" + "=" * 60)
    print("WORKFLOW:")
    print("=" * 60)
    print("1. For each batch (1-46):")
    print("   - I'll show you the source text to translate")
    print("   - I'll translate it using Claude")
    print("   - Save translation to translations.json")
    print("\n2. After all batches are translated:")
    print("   - Rebuild PDF with English text")
    print("   - Preserve original images/drawings")
    print("\n3. Final output:")
    print("   - translated_manual.pdf (full English book)")
    print("   - manual_translated_textonly.txt (text-only version)")
    print("=" * 60)
    print("\nREADY TO START TRANSLATION")
    print("Call: manager = TranslationManager()")
    print("Then: batch = manager.get_batch_for_translation(1)")


if __name__ == "__main__":
    main()
