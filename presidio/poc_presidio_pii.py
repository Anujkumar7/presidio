"""
Presidio PoC — PII Anonymization for Micro Improvement Plan PDFs
=================================================================
Masks: PERSON (name) and ORG (school/organisation)
Leaves: Location untouched

SETUP (run once):
    pip install pdfplumber pymupdf presidio-analyzer presidio-anonymizer

RUN:
    python poc_presidio_pii.py "Micro Improvement plan.pdf"
    # Produces: Micro Improvement plan_anonymized.pdf
"""

import sys
import os
import re
import logging
from typing import List, Set, Optional

logging.getLogger("pdfplumber").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)


# ── 1. PDF text extraction ────────────────────────────────────────────────────
def extract_text_from_pdf(pdf_path: str) -> List[str]:
    import pdfplumber
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


# ── 2. Structural name extraction from the cover page ────────────────────────
def extract_name_from_cover(page_text: str) -> Optional[str]:
    """
    The cover page always has the person's name as a standalone line —
    a short title-case word with no all-caps abbreviations (like 'HD').

    Structure:
        Improving ... in       ← title (has lowercase 'in')
        HD Kote Mysore         ← location (has 'HD', an all-caps abbreviation)
        Sindhu                 ← name  ← this is what we want
        HD Kote Mysore         ← location again

    Detection logic:
        - Line must fully match: one title-case word (uppercase first letter,
          rest lowercase), optionally followed by 1-2 more title-case words.
        - Reject lines with any run of 2+ uppercase letters (abbreviations).
    """
    lines = [l.strip() for l in page_text.strip().splitlines() if l.strip()]
    for line in lines:
        # Each word: uppercase letter followed by one or more lowercase letters
        if re.fullmatch(r'[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2}', line):
            # Reject if line contains abbreviations like "HD", "NIT", etc.
            if not re.search(r'[A-Z]{2,}', line):
                return line
    return None


# ── 3. Custom recognizer for school / org names ───────────────────────────────
from presidio_analyzer import PatternRecognizer, Pattern

def build_school_recognizer() -> PatternRecognizer:
    """
    spaCy sometimes tags school names as FAC (facility) instead of ORG,
    so Presidio misses them. This regex catches patterns like
    'Vienna High School', 'St. Mary's College', 'Delhi Academy', etc.

    Pattern: one or more strictly title-case words followed by a school type.
    Strictly title-case = first letter uppercase, rest lowercase.
    This prevents matching 'her school' (lowercase 'her') or 'HD School'
    (HD has no lowercase letters).
    """
    pattern = Pattern(
        name="school_org_name",
        regex=(
            r"\b[A-Z][a-z]+"          # First title-case word (e.g. "Vienna")
            r"(?: [A-Z][a-z]+)*"      # Zero or more additional title-case words (e.g. " High")
            r" (?:High School|Primary School|Secondary School|Middle School"
            r"|School|College|University|Institute|Academy)\b"
        ),
        score=0.88,
    )
    return PatternRecognizer(supported_entity="ORG", patterns=[pattern])


# ── 4. Build Presidio analyzer ────────────────────────────────────────────────
from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import NlpEngineProvider

def build_analyzer() -> AnalyzerEngine:
    provider = NlpEngineProvider(nlp_configuration={
        "nlp_engine_name": "spacy",
        "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
    })
    analyzer = AnalyzerEngine(nlp_engine=provider.create_engine())
    analyzer.registry.add_recognizer(build_school_recognizer())
    return analyzer


# ── 5. Anonymizer config ──────────────────────────────────────────────────────
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

# spaCy is only trusted for PERSON. ORG handled by deterministic regex below
# (spaCy mis-tags long noisy ORG spans like "noticed that her school").
ENTITIES_TO_DETECT = ["PERSON"]

# Deterministic ORG detector: strictly title-case words + school/org type.
# Matches "Vienna High School", "St Mary College" — never "her school".
SCHOOL_REGEX = re.compile(
    r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)* "
    r"(?:High School|Primary School|Secondary School|Middle School"
    r"|School|College|University|Institute|Academy)\b"
)

OPERATOR_CONFIG = {
    "PERSON":  OperatorConfig("replace", {"new_value": "<PERSON>"}),
    "ORG":     OperatorConfig("replace", {"new_value": "<ORGANIZATION>"}),
    "DEFAULT": OperatorConfig("replace", {"new_value": "<REDACTED>"}),
}


# ── 6. Remove duplicate / overlapping detections ─────────────────────────────
def deduplicate(results):
    """Keep highest-score result when two detections overlap the same span."""
    results = sorted(results, key=lambda r: (r.start, -r.score))
    deduped, last_end = [], -1
    for r in results:
        if r.start >= last_end:
            deduped.append(r)
            last_end = r.end
    return deduped


# ── 7. Generate redacted PDF ──────────────────────────────────────────────────
def generate_redacted_pdf(input_path: str, output_path: str, pii_texts: Set[str]):
    """
    Searches for each PII string across EVERY page of the PDF and draws
    a black redaction box over it. This means a name detected on page 3
    is automatically redacted on the cover page (page 1) as well.
    """
    try:
        import fitz
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pymupdf", "-q"])
        import fitz

    doc = fitz.open(input_path)
    for page in doc:
        for pii in pii_texts:
            for rect in page.search_for(pii):
                page.add_redact_annot(rect, fill=(0, 0, 0))
        page.apply_redactions()
    doc.save(output_path)
    doc.close()


# ── 8. Main ───────────────────────────────────────────────────────────────────
def run_poc(pdf_path: str):
    print("\n" + "="*60)
    print("  Presidio PoC — Micro Improvement Plan PII Anonymization")
    print("  Masking: PERSON + ORG   |   Keeping: Location")
    print("="*60)

    print("\n[1] Loading models...")
    analyzer  = build_analyzer()
    anonymizer = AnonymizerEngine()
    print("    ✓ Ready")

    print("\n[2] Extracting text from PDF...")
    pages = extract_text_from_pdf(pdf_path)
    print(f"    ✓ {len(pages)} pages extracted")

    # This set collects ALL unique PII strings found across every page.
    # PyMuPDF will then search and redact these strings on every page,
    # so a name found on page 3 also gets redacted on page 1.
    all_pii_texts: Set[str] = set()

    for i, page_text in enumerate(pages, 1):
        if not page_text.strip():
            continue

        print(f"\n{'─'*60}")
        print(f"  PAGE {i}")
        print(f"{'─'*60}")
        print("\n[ORIGINAL TEXT]")
        print(page_text)

        # Cover page: structural extraction (spaCy has no sentence context here)
        if i == 1:
            cover_name = extract_name_from_cover(page_text)
            if cover_name:
                all_pii_texts.add(cover_name)
                print(f"\n[COVER PAGE — structural] Name identified: '{cover_name}'")
                print("  (Will be redacted in PDF even though NER has no context here)")

        # PERSON via spaCy NER (reliable for names)
        results = analyzer.analyze(
            text=page_text,
            language="en",
            entities=ENTITIES_TO_DETECT,
        )
        results = deduplicate(results)

        # ORG via deterministic regex (spaCy ORG spans are noisy/wrong here)
        org_spans = [(m.start(), m.end(), m.group()) for m in SCHOOL_REGEX.finditer(page_text)]

        if not results and not org_spans:
            print("\n[PII DETECTED] None found on this page.")
        else:
            print(f"\n[PII DETECTED] {len(results) + len(org_spans)} entities:")
            print(f"  {'Entity':<14} {'Text':<26} {'Score':>6}")
            print(f"  {'------':<14} {'----':<26} {'-----':>6}")
            for r in sorted(results, key=lambda x: x.start):
                snippet = page_text[r.start:r.end]
                all_pii_texts.add(snippet)
                print(f"  {'PERSON':<14} {repr(snippet):<26} {r.score:>6.2f}")
            for _, _, snippet in org_spans:
                all_pii_texts.add(snippet)
                print(f"  {'ORG':<14} {repr(snippet):<26} {0.95:>6.2f}")

    # Generate the redacted PDF
    print(f"\n{'─'*60}")
    print(f"[3] Generating anonymized PDF...")
    print(f"    Redacting across ALL pages: {all_pii_texts}")

    base, ext = os.path.splitext(pdf_path)
    output_path = base + "_anonymized" + ext

    if all_pii_texts:
        generate_redacted_pdf(pdf_path, output_path, all_pii_texts)
        print(f"    ✓ Saved → {output_path}")
    else:
        print("    No PII found.")

    print("\n" + "="*60)
    print("  Done")
    print("="*60)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        default_pdf = os.path.join(os.path.dirname(__file__), "Micro Improvement plan.pdf")
        if os.path.exists(default_pdf):
            run_poc(default_pdf)
        else:
            print("Usage: python poc_presidio_pii.py <path-to-pdf>")
            sys.exit(1)
    else:
        run_poc(sys.argv[1])
