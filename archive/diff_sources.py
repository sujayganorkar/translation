"""
diff_sources.py — Compare VLM descriptions vs Trinity translations
===================================================================

Finds pages where the two sources meaningfully disagree:
  - Different numbers/dimensions
  - Missing content (sections in one but not the other)
  - Significant text divergence

Output: diff_report.json with conflict details per page
"""

import json
import re
import sys
from difflib import SequenceMatcher

sys.stdout.reconfigure(encoding='utf-8')

DESCRIPTIONS_FILE = "page_descriptions.json"
TRANSLATIONS_FILE = "translations.json"
DIFF_REPORT_FILE = "diff_report.json"

# ── Extractors ───────────────────────────────────────────────────────

def extract_numbers(text: str) -> set:
    """Extract numeric values, excluding years and standard code numbers."""
    # Remove standard codes first so their numbers don't pollute
    cleaned = re.sub(r'(?:GB/?T|ISO|JB/?T|DIN|ANSI|HB|QC|SH|NB)[\s/]*[\d.\-—×]+', '', text)
    # Remove years (4-digit numbers 1900-2099 appearing alone)
    cleaned = re.sub(r'\b(19|20)\d{2}\b', '', cleaned)
    # Remove page numbers at start/end
    cleaned = re.sub(r'^\d{1,4}\s', '', cleaned)
    cleaned = re.sub(r'\s\d{1,4}$', '', cleaned)
    nums = re.findall(r'-?\d+\.?\d*', cleaned)
    return set(nums)


def extract_standards(text: str) -> set:
    """Extract standard codes like GB/T 1234, ISO 5678, JB/T 910."""
    codes = re.findall(r'(?:GB/?T|ISO|JB/?T|DIN|ANSI)\s*[\d.\-—]+', text)
    # Normalize: remove spaces, replace — with -
    return set(c.replace(' ', '').replace('—', '-') for c in codes)


def flatten_vlm_text(desc: dict) -> str:
    """Flatten VLM JSON description into plain text for comparison."""
    parts = []
    for section in desc.get("sections", []):
        stype = section.get("type", "")
        if stype == "heading":
            parts.append(section.get("text", ""))
        elif stype == "paragraph":
            parts.append(section.get("text", ""))
        elif stype == "table":
            caption = section.get("caption", "")
            if caption:
                parts.append(caption)
            for h in section.get("headers", []):
                parts.append(str(h))
            for row in section.get("rows", []):
                for cell in (row if isinstance(row, list) else [row]):
                    parts.append(str(cell))
        elif stype == "figure":
            parts.append(section.get("caption", ""))
            parts.append(section.get("description", ""))
        elif stype == "formula":
            parts.append(section.get("text", ""))
        elif stype == "note":
            parts.append(section.get("text", ""))
        elif stype == "list":
            for item in section.get("items", []):
                parts.append(str(item))
        elif stype == "toc_entries":
            for entry in section.get("entries", []):
                parts.append(entry.get("title", ""))
    return " ".join(parts)


def flatten_vlm_tables(desc: dict) -> list:
    """Extract all table numeric data from VLM description."""
    table_nums = []
    for section in desc.get("sections", []):
        if section.get("type") == "table":
            for row in section.get("rows", []):
                for cell in (row if isinstance(row, list) else [row]):
                    nums = extract_numbers(str(cell))
                    table_nums.extend(nums)
    return table_nums


# ── Comparison ───────────────────────────────────────────────────────

def compare_page(desc: dict, trans_text: str) -> dict:
    """Compare one page's VLM description against Trinity translation.

    Returns a dict with conflict info, or None if no significant conflicts.
    """
    vlm_text = flatten_vlm_text(desc)
    if not vlm_text.strip() or not trans_text.strip():
        return None

    conflicts = []
    severity = 0  # 0=clean, 1=minor, 2=moderate, 3=serious

    trivial = {'0', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10',
                '11', '12', '13', '14', '15', '16', '17', '18', '19', '20'}

    # 1. Table numeric data — most important for an engineering handbook
    vlm_table_nums = set(flatten_vlm_tables(desc)) - trivial
    trans_nums = extract_numbers(trans_text) - trivial

    if vlm_table_nums:
        # Numbers in VLM tables that Trinity doesn't have
        vlm_table_only = vlm_table_nums - trans_nums
        # Numbers in Trinity that aren't in VLM tables (less concerning)
        if vlm_table_only and len(vlm_table_only) > 2:
            conflicts.append({
                "type": "table_data_mismatch",
                "detail": "VLM table values not found in Trinity",
                "count": len(vlm_table_only),
                "examples": sorted(vlm_table_only)[:10],
            })
            severity = max(severity, 2 if len(vlm_table_only) > 5 else 1)

    # 2. Non-table numeric comparison (formulas, dimensions in text)
    vlm_text_nums = extract_numbers(vlm_text) - trivial - vlm_table_nums
    trans_text_nums = trans_nums - trivial

    num_diff = vlm_text_nums.symmetric_difference(trans_text_nums)
    # Only flag if there are decimal numbers or dimension-like values disagreeing
    decimal_diffs = [n for n in num_diff if '.' in n]
    if len(decimal_diffs) > 2:
        conflicts.append({
            "type": "numeric_text_mismatch",
            "decimal_diffs": sorted(decimal_diffs)[:10],
        })
        severity = max(severity, 2)

    # 3. Content length comparison (one source much shorter = missing content)
    vlm_norm = re.sub(r'\s+', ' ', vlm_text.lower().strip())
    trans_norm = re.sub(r'\s+', ' ', trans_text.lower().strip())

    len_ratio = len(vlm_norm) / max(len(trans_norm), 1)
    if len_ratio < 0.4:
        conflicts.append({
            "type": "vlm_much_shorter",
            "vlm_chars": len(vlm_norm),
            "trinity_chars": len(trans_norm),
            "ratio": round(len_ratio, 2),
        })
        severity = max(severity, 3)
    elif len_ratio > 2.5:
        conflicts.append({
            "type": "trinity_much_shorter",
            "vlm_chars": len(vlm_norm),
            "trinity_chars": len(trans_norm),
            "ratio": round(len_ratio, 2),
        })
        severity = max(severity, 2)

    # 4. Formula check — look for formula sections and compare against Trinity
    for section in desc.get("sections", []):
        if section.get("type") == "formula":
            formula = section.get("text", "")
            # Check if Trinity has a similar formula
            # Extract variable assignments like "F = " or "Fs = "
            vlm_vars = set(re.findall(r'([A-Za-z][A-Za-z0-9_]*)\s*=', formula))
            trans_vars = set(re.findall(r'([A-Za-z][A-Za-z0-9_]*)\s*=', trans_text))
            if vlm_vars and not vlm_vars.intersection(trans_vars):
                conflicts.append({
                    "type": "formula_variable_mismatch",
                    "vlm_formula": formula[:100],
                    "vlm_vars": sorted(vlm_vars),
                    "trinity_vars": sorted(trans_vars),
                })
                severity = max(severity, 3)

    if not conflicts:
        return None

    return {
        "severity": severity,
        "conflicts": conflicts,
    }


# ── Main ─────────────────────────────────────────────────────────────

def main():
    descs = {}
    with open(DESCRIPTIONS_FILE, "r", encoding="utf-8") as f:
        descs = json.load(f)

    trans = {}
    with open(TRANSLATIONS_FILE, "r", encoding="utf-8") as f:
        trans = json.load(f)

    print(f"VLM descriptions: {len(descs)}")
    print(f"Trinity translations: {len(trans)}")

    both_pages = sorted(
        [pg for pg in descs if pg in trans
         and descs[pg].get("page_type") not in ("error", "empty", None)
         and trans[pg].strip()],
        key=lambda x: int(x)
    )
    print(f"Pages with both sources: {len(both_pages)}")

    report = {}
    severity_counts = {0: 0, 1: 0, 2: 0, 3: 0}

    for pg in both_pages:
        result = compare_page(descs[pg], trans[pg])
        if result:
            report[pg] = result
            severity_counts[result["severity"]] += 1
        else:
            severity_counts[0] += 1

    # Save report
    with open(DIFF_REPORT_FILE, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    # Summary
    print(f"\n{'='*60}")
    print(f"DIFF REPORT")
    print(f"{'='*60}")
    print(f"  Clean (no conflicts):  {severity_counts[0]}")
    print(f"  Minor (sev 1):         {severity_counts[1]}")
    print(f"  Moderate (sev 2):      {severity_counts[2]}")
    print(f"  Serious (sev 3):       {severity_counts[3]}")
    print(f"  Total with conflicts:  {len(report)}")
    print(f"  Output: {DIFF_REPORT_FILE}")

    # Show some examples of serious conflicts
    serious = [(pg, r) for pg, r in sorted(report.items(), key=lambda x: int(x[0]))
               if r["severity"] >= 2]
    if serious:
        print(f"\n  Moderate/Serious conflicts ({len(serious)} pages):")
        for pg, r in serious[:15]:
            types = [c["type"] for c in r["conflicts"]]
            print(f"    Page {pg}: sev={r['severity']} — {', '.join(types)}")
        if len(serious) > 15:
            print(f"    ... and {len(serious) - 15} more")

    print(f"{'='*60}")


if __name__ == "__main__":
    main()
