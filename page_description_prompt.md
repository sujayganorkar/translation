# VLM Page Description Prompt — Design Document

## Goal
Have a VLM analyze each page image and return structured JSON describing
all content, fully translated to English. The builder then uses this JSON
to render each page with appropriate ReportLab flowables (tables, headings,
paragraphs, etc.)

## JSON Schema

```json
{
  "page_type": "text | table | diagram | toc | mixed | empty",
  "page_header": "string — running header at top of page, if any",
  "sections": [
    // One or more of the following section types, in reading order:

    {"type": "heading", "level": 1, "text": "Chapter 3 Hydraulic Cylinder Design"},
    // level: 1=chapter, 2=section (e.g. 3.2), 3=subsection (e.g. 3.2.1)

    {"type": "paragraph", "text": "Full translated paragraph text here..."},

    {"type": "table",
     "caption": "Table 1-105 External Thread Undercut Groove Dimensions (mm)",
     "headers": ["Pitch", "p", "b₁", "b₂", "d", "r"],
     "rows": [
       ["0.5", "1.5", "0.8", "0.2", "d-0.8", "0.2"],
       ["0.75", "2.25", "1.2", "0.4", "d-1.2", "0.4"]
     ]},

    {"type": "figure",
     "caption": "Figure 1-32 Internal Thread Undercut Groove Forms",
     "description": "Cross-section technical drawing showing groove profile with dimensions d, p, r, b₁ labeled"},

    {"type": "formula",
     "text": "F = p × A = p × (π/4) × D²"},

    {"type": "note",
     "text": "Note: d is the nominal thread diameter symbol."},

    {"type": "list",
     "ordered": true,
     "items": ["First requirement...", "Second requirement..."]},

    {"type": "toc_entries",
     "entries": [
       {"title": "4.2.19 Liquid-filled Forming Hydraulic Press", "page": "563"},
       {"title": "4.2.20 Precision Servo Straightening Press", "page": "566"}
     ]}
  ]
}
```

## Section Type Reference

| type | when to use | required fields |
|------|------------|----------------|
| heading | Chapter/section/subsection titles | level, text |
| paragraph | Body text, descriptions | text |
| table | Any tabular data with rows/columns | headers, rows; caption optional |
| figure | Diagrams, drawings, schematics | description; caption optional |
| formula | Mathematical equations | text |
| note | Footnotes, remarks, "Note:" text | text |
| list | Bulleted or numbered lists | items, ordered |
| toc_entries | Table of contents entries | entries (array of {title, page}) |

## Prompt

```
You are a technical document analyzer for a Chinese hydraulic engineering handbook.

Analyze this page image and return a JSON object describing ALL content, translated to English.

RULES:
1. Translate ALL Chinese text to English. Keep numbers, units, and standard codes (GB/T, ISO) exactly as shown.
2. For tables: capture EVERY row and column. Do not summarize or skip rows. Include ALL numeric values exactly.
3. For figures/diagrams: describe what is shown and list all visible labels, dimensions, and annotations.
4. For formulas: reproduce them in text form.
5. Sections must be in top-to-bottom reading order as they appear on the page.
6. If the page is empty or contains only a cover image with no readable text, return: {"page_type": "empty", "sections": []}
7. Output ONLY valid JSON. No markdown fences, no commentary, no explanations outside the JSON.

JSON SCHEMA:
{
  "page_type": "text | table | diagram | toc | mixed | empty",
  "page_header": "running header text or empty string",
  "sections": [
    // Use these section types:
    // {"type": "heading", "level": 1|2|3, "text": "..."}
    // {"type": "paragraph", "text": "..."}
    // {"type": "table", "caption": "...", "headers": [...], "rows": [[...], ...]}
    // {"type": "figure", "caption": "...", "description": "..."}
    // {"type": "formula", "text": "..."}
    // {"type": "note", "text": "..."}
    // {"type": "list", "ordered": true|false, "items": ["...", ...]}
    // {"type": "toc_entries", "entries": [{"title": "...", "page": "..."}, ...]}
  ]
}
```

## Edge Cases to Handle

1. **Multi-part tables spanning pages**: "(Continued)" at top → still output as table type
2. **Mixed pages**: table + text + diagram → use "mixed" type, multiple sections
3. **Pages with only page numbers**: → "empty"
4. **Standard reference pages**: GB/T codes → treat as table with standard code + description
5. **Pages with drawings but no text labels**: → figure section with description only
6. **Footnotes with superscript markers**: → "note" section
7. **Multi-column layouts**: read left column first, then right
8. **Units in table headers**: include in header text, e.g. "Diameter D (mm)"
