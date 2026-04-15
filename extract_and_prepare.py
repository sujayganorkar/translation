"""
Extract and Prepare for Translation
Extracts all text, metadata, and image information from a Chinese PDF
Creates structured JSON suitable for translation and reassembly
"""

import json
import sys
from pathlib import Path
from pypdf import PdfReader
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')

class ManualExtractor:
    def __init__(self, pdf_path):
        self.pdf_path = pdf_path
        self.reader = PdfReader(pdf_path)
        self.total_pages = len(self.reader.pages)
        self.extracted_data = {
            "metadata": {
                "title": Path(pdf_path).stem,
                "total_pages": self.total_pages,
                "extracted_date": datetime.now().isoformat(),
                "source_pdf": str(pdf_path)
            },
            "pages": [],
            "statistics": {
                "total_characters": 0,
                "total_words": 0,
                "pages_with_images": 0,
                "empty_pages": 0
            }
        }

    def extract_all_pages(self):
        """Extract text from all pages with metadata"""
        print(f"\nExtracting from {self.total_pages} pages...")

        for page_num in range(self.total_pages):
            try:
                page = self.reader.pages[page_num]
                text = page.extract_text()

                # Check for images
                has_images = "/XObject" in page["/Resources"] if "/Resources" in page else False

                page_data = {
                    "page_number": page_num + 1,
                    "raw_text": text if text else "",
                    "character_count": len(text) if text else 0,
                    "word_count": len(text.split()) if text else 0,
                    "has_images": has_images,
                    "is_empty": len(text.strip()) == 0 if text else True,
                    "status": "pending_translation"
                }

                self.extracted_data["pages"].append(page_data)

                # Update statistics
                if text:
                    self.extracted_data["statistics"]["total_characters"] += len(text)
                    self.extracted_data["statistics"]["total_words"] += len(text.split())
                if has_images:
                    self.extracted_data["statistics"]["pages_with_images"] += 1
                if page_data["is_empty"]:
                    self.extracted_data["statistics"]["empty_pages"] += 1

                # Progress indicator
                if (page_num + 1) % 100 == 0:
                    print(f"  Processed {page_num + 1}/{self.total_pages} pages...")

            except Exception as e:
                print(f"  ERROR on page {page_num + 1}: {str(e)}")
                self.extracted_data["pages"].append({
                    "page_number": page_num + 1,
                    "raw_text": f"[ERROR: {str(e)}]",
                    "character_count": 0,
                    "word_count": 0,
                    "has_images": False,
                    "is_empty": False,
                    "status": "error"
                })

        print(f"\n✓ Extraction complete!")
        return self.extracted_data

    def save_to_json(self, output_path="manual_extracted.json"):
        """Save extracted data to JSON"""
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(self.extracted_data, f, ensure_ascii=False, indent=2)
        print(f"✓ Saved to: {output_path}")
        return output_path

    def create_translation_batches(self, batch_size=20):
        """Create batches for translation (e.g., 20 pages per batch)"""
        batches = []
        for i in range(0, len(self.extracted_data["pages"]), batch_size):
            batch = {
                "batch_number": len(batches) + 1,
                "pages": self.extracted_data["pages"][i:i+batch_size],
                "page_range": f"{self.extracted_data['pages'][i]['page_number']}-{self.extracted_data['pages'][min(i+batch_size-1, len(self.extracted_data['pages'])-1)]['page_number']}",
                "status": "pending"
            }
            batches.append(batch)

        batches_data = {
            "total_batches": len(batches),
            "batch_size": batch_size,
            "batches": batches
        }

        with open("translation_batches.json", "w", encoding="utf-8") as f:
            json.dump(batches_data, f, ensure_ascii=False, indent=2)

        print(f"✓ Created {len(batches)} translation batches of {batch_size} pages each")
        return batches_data

    def print_statistics(self):
        """Print extraction statistics"""
        stats = self.extracted_data["statistics"]
        print("\n" + "=" * 60)
        print("EXTRACTION STATISTICS")
        print("=" * 60)
        print(f"Total pages:           {self.total_pages}")
        print(f"Total characters:      {stats['total_characters']:,}")
        print(f"Total words:           {stats['total_words']:,}")
        print(f"Pages with images:     {stats['pages_with_images']}")
        print(f"Empty pages:           {stats['empty_pages']}")
        print(f"Content pages:         {self.total_pages - stats['empty_pages']}")

        if stats['total_characters'] > 0:
            avg_chars = stats['total_characters'] / (self.total_pages - stats['empty_pages'])
            print(f"\nAverage chars/page:    {avg_chars:,.0f}")
            print(f"Estimated tokens:      {stats['total_characters'] * 0.25:,.0f} (approx)")
        print("=" * 60)


def find_source_pdf():
    """Find the source PDF in the current directory (exclude output/test PDFs)."""
    exclude = {"english", "output", "test", "rebuild", "final", "handbook", "_en"}
    pdfs = [
        p for p in Path(".").glob("*.pdf")
        if not any(kw in p.name.lower() for kw in exclude)
    ]
    if not pdfs:
        return None
    return str(max(pdfs, key=lambda p: p.stat().st_size))


def main():
    pdf_path = find_source_pdf()

    if not pdf_path:
        print("ERROR: No source PDF found in current directory")
        return

    print("=" * 60)
    print("PDF TEXT EXTRACTION TOOL")
    print("=" * 60)
    print(f"Source: {pdf_path}")

    # Extract
    extractor = ManualExtractor(pdf_path)
    extractor.extract_all_pages()
    extractor.print_statistics()

    # Save
    extractor.save_to_json("manual_extracted.json")

    # Create batches
    print("\nCreating translation batches...")
    batches = extractor.create_translation_batches(batch_size=20)

    print("\n" + "=" * 60)
    print("NEXT STEPS:")
    print("=" * 60)
    print("1. Open 'manual_extracted.json' to review extracted content")
    print("2. Use 'translation_batches.json' for batch translation")
    print("3. For images/drawings:")
    print("   - Marked in JSON as 'has_images': true")
    print("   - Keep original PDF images, replace text only")
    print("4. Translation approach:")
    print("   - Translate each batch with Claude")
    print("   - Store translations back to JSON")
    print("   - Rebuild PDF with translated text + original images")
    print("=" * 60)


if __name__ == "__main__":
    main()
