# Presidio PoC — Handoff Document

## Context

I'm building a PoC to evaluate Microsoft Presidio (https://github.com/microsoft/presidio) for masking PII in my company's generated PDFs before sharing them. The PDFs are called "Micro Improvement Plans" — each one is generated for a specific person at a specific location. The goal is to redact the **person's name** and **organisation name** from the PDF, while leaving the **location** visible.

The PoC is nearly complete. The last script version was working but hadn't been re-run after the final round of fixes. The next step is to run it and verify the output PDF.

---

## Repo & Environment

- **Repo**: Microsoft Presidio, cloned to `~/Documents/presidio/`
- **Virtual env**: `~/Documents/presidio/presidio/presidio/presidio-env/`
  - Has `presidio_analyzer`, `presidio_anonymizer`, `spaCy en_core_web_lg` installed
- **Working directory**: `~/Documents/presidio/presidio/`
- **Activate venv**: `source presidio/presidio-env/bin/activate`

---

## Files

| File | Purpose |
|------|---------|
| `~/Documents/presidio/presidio/poc_presidio_pii.py` | Main PoC script (latest version) |
| `~/Documents/presidio/presidio/Micro Improvement plan.pdf` | Sample PDF used for testing |

---

## How to Run

```bash
cd ~/Documents/presidio/presidio
source presidio/presidio-env/bin/activate
pip install pdfplumber pymupdf   # if not already installed
python poc_presidio_pii.py "Micro Improvement plan.pdf"
```

Produces: `Micro Improvement plan_anonymized.pdf` in the same folder.

---

## Sample PDF Structure

The PDF is 3 pages:

- **Page 1 (Cover)**: Title ("Improving Teacher Availability in HD Kote Mysore"), then location "HD Kote Mysore", then name "Sindhu", then location again
- **Page 2**: Report content with no PII other than the location
- **Page 3**: Narrative paragraph — mentions "Sindhu" (name, 5 times) and "Vienna High School" (organisation)

**PII to mask**: `Sindhu` (PERSON), `Vienna High School` (ORG)
**PII to leave**: `HD Kote Mysore`, `HD Kote, Mysore` (LOCATION)

---

## What the Script Does

1. **Extracts text** from all PDF pages using `pdfplumber`
2. **Page 1 — structural extraction**: spaCy has no sentence context on the cover page, so NER fails there. The script structurally identifies the name (a standalone title-case line with no all-caps abbreviations like "HD") and adds it to the redaction pool.
3. **All pages — Presidio NER**: Runs spaCy `en_core_web_lg` to detect `PERSON` and `ORG` entities. A custom regex recognizer catches school names (`Vienna High School`) that spaCy sometimes tags as `FAC` instead of `ORG`.
4. **Cross-page redaction**: All detected PII strings are pooled into a set. PyMuPDF (`fitz`) then searches for each string across ALL pages and draws black boxes over every match — so "Sindhu" detected on page 3 also gets redacted on page 1.
5. **Outputs** `_anonymized.pdf` with the same layout/images/formatting, just with black boxes over the PII.

---

## Key Design Decisions

- **No standalone name recognizer**: An earlier attempt used a regex to catch standalone title-case words as names. It caused massive false positives ("teacher", "Report", "School" all tagged as PERSON). Removed entirely.
- **Structural cover page extraction**: Since the PDF is always generated in the same format, page 1 line structure reliably identifies the name without NER.
- **School recognizer regex**: `[A-Z][a-z]+(?: [A-Z][a-z]+)* (?:High School|School|College|...)` — strictly title-case only, so "her school" and "teacher" never match.
- **`deduplicate()`**: When spaCy and a custom recognizer both detect the same span, only the highest-confidence one is kept.

---

## Current Status

The script is written and saved. It **has not been re-run yet after the last round of fixes**. The previous run (with a broken version) was producing false positives like "teacher" and "Report" being tagged as PERSON — that was caused by a since-deleted regex recognizer. The current version should be clean.

### Expected output when run:
```
PAGE 1  → [COVER PAGE — structural] Name identified: 'Sindhu'
PAGE 2  → None found
PAGE 3  → PERSON 'Sindhu' ×5 (score 0.85), ORG 'Vienna High School' ×1 (score 0.88)
PDF     → Redacting: {'Sindhu', 'Vienna High School'} across all 3 pages
```

---

## Next Steps

1. **Run the script** and confirm the terminal output matches expected above
2. **Open the `_anonymized.pdf`** and visually verify:
   - Page 1: "Sindhu" has a black box over it, "HD Kote Mysore" is visible
   - Page 3: All 5 "Sindhu" occurrences redacted, "Vienna High School" redacted, locations intact
3. **If Vienna High School is still missed**: spaCy may be tagging it differently. Add a debug print: `spacy.load("en_core_web_lg")("Sindhu, a teacher at Vienna High School in HD Kote").ents` to see raw tags
4. **Once PoC is verified**: Present to team. The production integration decision is between:
   - Anonymizing data **before** PDF generation (cleanest, no post-processing)
   - Post-processing PDFs with this script (works on already-generated PDFs)

---

## Dependencies

```
presidio-analyzer==2.2.362
presidio-anonymizer==2.2.362
spacy==3.8.x  +  en_core_web_lg-3.8.0
pdfplumber
pymupdf  (imported as `fitz`)
```
