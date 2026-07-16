"""
Batch PII masker for Micro Improvement Plan (MIP) and Guest_Discussion PDFs.
=============================================================================
Masks : PERSON (name) + ORG (organisation / school)
Keeps : LOCATION (village / district / state etc.)

These PDFs are machine-generated with a fixed layout, so detection is done
DETERMINISTICALLY (structural parse + regex) instead of statistical NER.
This is more reliable on a fixed template and also handles non-English
(Devanagari) names that an English spaCy model would miss.

Run:
    python mask_pii_batch.py                 # processes all MIP*/Guest_Discussion* in cwd
    python mask_pii_batch.py file1.pdf ...   # specific files
Produces: <name>_masked.pdf next to each input.
"""

import sys, os, re, glob, logging
from typing import List, Set, Tuple, Optional

logging.getLogger("pdfplumber").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

import pdfplumber
import fitz  # PyMuPDF

# ── Keyword vocab ─────────────────────────────────────────────────────────────
# Lines that describe a place (kept, never masked, and used to locate the name).
GEO = [
    "village", "district", "block", "panchayat", "thana", "jila", "state",
    "town", "post", "nagar", "kala", "pradesh", "tehsil", "ward", "colony",
    "bihar", "patna", "gaya", "sitamarhi", "mysore", "rampur", "sherghatti",
    "chakand", "sonabarsa", "parihar", "bela", "baya", "kote", "gaou",
    "hanuman", "sankhi", "riga", "parsa", "mysuru",
    # Devanagari geo markers
    "नगर", "पोस्ट", "थाना", "जला", "जिला", "सीतामढ", "सांखी", "रीगा",
    "हनुमान", "ब्लॉक", "गांव", "पंचायत", "गाँव",
]
# Topic / heading words → identify title lines (skipped when finding the name).
TOPIC = [
    "improving", "improve", "education", "educational", "through", "discussion",
    "infrastructure", "challenges", "solutions", "schools", "school", "financial",
    "constraints", "distance", "affecting", "mental", "health", "hygiene",
    "menstrual", "awareness", "attendance", "partnership", "transport", "shared",
    "barriers", "addressing", "rural", "areas", "girls", "girl", "story", "report",
    "community", "availability", "teacher", "teachers", "with", "and", "the",
    "in", "to", "of", "for",
]
# Words that introduce an organisation inside narrative text.
ROLE = ["resident", "worker", "volunteer", "coordinator", "member", "staff",
        "founder", "employee", "headmaster", "headmistress", "principal", "teacher"]
ORG_SUFFIX = ("High School|Primary School|Secondary School|Middle School|"
              "School|College|University|Institute|Academy|Foundation|Trust|NGO")

SCHOOL_REGEX = re.compile(r"\b[A-Z][a-z]+(?: [A-Z][a-z]+)* (?:" + ORG_SUFFIX + r")\b")
ROLE_ORG_REGEX = re.compile(
    r"\b(?:" + "|".join(ROLE) + r")\s+(?:of|at)\s+(?:the\s+)?"
    r"([A-Z][a-z]+(?: [A-Z][a-z]+)*)\b"
)

def _low(s: str) -> str:
    return s.lower()

def has_geo(line: str) -> bool:
    return any(k in _low(line) for k in GEO)

def is_topic(line: str) -> bool:
    return any(re.search(r"\b" + re.escape(k) + r"\b", _low(line)) for k in TOPIC)

def is_meta(line: str) -> bool:
    return bool(re.search(r"(date of discussion|members\s*:|total participants|^date\b)",
                          _low(line))) or line.startswith("★")

def looks_like_name(line: str) -> bool:
    """Title-case English (1-4 tokens) OR contains Devanagari, no digits."""
    if not line or any(ch.isdigit() for ch in line):
        return False
    if re.search(r"[ऀ-ॿ]", line):          # Devanagari
        return True
    toks = line.split()
    if not (1 <= len(toks) <= 4):
        return False
    return all(re.fullmatch(r"[A-Z][a-zA-Z'.]+", t) for t in toks)


# ── Structural extraction from the cover / header block ───────────────────────
def parse_header(page1_text: str) -> Tuple[Optional[str], Optional[str]]:
    """Return (name, org_from_header) parsed from the cover/header layout:
        <title lines...> <NAME> [<ORG>] <LOCATION> [<date/members>]
    """
    lines = [l.strip() for l in page1_text.splitlines() if l.strip()]
    # header = everything before the first '★' bullet section
    header = []
    for l in lines:
        if l.startswith("★"):
            break
        header.append(l)
    # drop meta (date/members) lines
    header = [l for l in header if not is_meta(l)]
    if not header:
        return None, None

    # LOCATION = last header line that looks geographic (fallback: last line)
    loc_idx = None
    for i in range(len(header) - 1, -1, -1):
        if has_geo(header[i]):
            loc_idx = i
            break
    if loc_idx is None:
        loc_idx = len(header) - 1

    # NAME = first line (top-down) that is not a title word and not geographic
    name, name_idx = None, None
    for i in range(loc_idx):
        l = header[i]
        if has_geo(l) or is_topic(l):
            continue
        if looks_like_name(l):
            name, name_idx = l, i
            break

    # ORG (header) = a non-geo, non-topic line between NAME and LOCATION
    org = None
    if name_idx is not None:
        for i in range(name_idx + 1, loc_idx):
            l = header[i]
            if not has_geo(l) and not is_topic(l) and not looks_like_name_is_title(l):
                org = l
                break
    return name, org

def looks_like_name_is_title(line: str) -> bool:
    return is_topic(line)


# ── Narrative ORG extraction (MIP story pages) ────────────────────────────────
def extract_orgs_from_text(text: str) -> Set[str]:
    orgs = set()
    for m in SCHOOL_REGEX.finditer(text):
        if not has_geo(m.group()):
            orgs.add(m.group().strip())
    for m in ROLE_ORG_REGEX.finditer(text):
        cand = m.group(1).strip()
        if cand and not has_geo(cand) and not is_topic(cand):
            orgs.add(cand)
    return orgs


# ── Build the redaction pool for one PDF ──────────────────────────────────────
def build_pii_pool(pages: List[str]) -> Tuple[Set[str], dict]:
    pool: Set[str] = set()
    info = {"name": None, "orgs": set()}

    name, header_org = parse_header(pages[0]) if pages else (None, None)
    if name:
        info["name"] = name
        pool.add(name)
        # also redact individual name tokens (e.g. narrative uses first name only)
        for tok in name.split():
            if len(tok) > 2 and re.fullmatch(r"[A-Za-z'.]+", tok):
                pool.add(tok)
    if header_org:
        info["orgs"].add(header_org)
        pool.add(header_org)

    # ORG from all page text (narrative)
    for t in pages:
        for org in extract_orgs_from_text(t):
            info["orgs"].add(org)
            pool.add(org)

    # never mask anything that is purely a location token
    pool = {p for p in pool if p and not (has_geo(p) and " " not in p and len(p.split()) == 1 and is_topic(p))}
    return pool, info


# ── Redact a PDF ──────────────────────────────────────────────────────────────
def _norm(s: str) -> str:
    return re.sub(r"[\s\x00]", "", s)

def redact_pdf(src: str, dst: str, pool: Set[str]) -> int:
    doc = fitz.open(src)
    hits = 0
    for page in doc:
        matched = set()
        # 1) direct text search (works for Latin script)
        for s in sorted(pool, key=len, reverse=True):
            rects = page.search_for(s)
            if rects:
                matched.add(s)
            for rect in rects:
                page.add_redact_annot(rect, fill=(0, 0, 0))
                hits += 1
        # 2) fallback for non-Latin (Devanagari) names: text extraction mangles
        #    them so search_for fails. Redact the Devanagari NAME line
        #    structurally: it sits in the header (above the location / first
        #    '★'/'Members' line) and contains no geographic keyword.
        unmatched = [s for s in pool if s not in matched]
        need_deva = any(re.search(r"[ऀ-ॿ]", s) for s in unmatched)
        if need_deva:
            lines = []
            for blk in page.get_text("dict")["blocks"]:
                for line in blk.get("lines", []):
                    txt = "".join(sp["text"] for sp in line["spans"])
                    bbox = fitz.Rect(
                        min(sp["bbox"][0] for sp in line["spans"]),
                        min(sp["bbox"][1] for sp in line["spans"]),
                        max(sp["bbox"][2] for sp in line["spans"]),
                        max(sp["bbox"][3] for sp in line["spans"]))
                    lines.append((bbox.y0, txt, bbox))
            lines.sort(key=lambda x: x[0])
            # header cutoff = first line that is meta ('★' / Members / Date)
            cutoff = next((y for y, t, _ in lines if is_meta(t.strip())), 1e9)
            for y, txt, bbox in lines:
                if y < cutoff and re.search(r"[ऀ-ॿ]", txt) and not has_geo(txt):
                    page.add_redact_annot(bbox, fill=(0, 0, 0))
                    hits += 1
        page.apply_redactions()
    doc.save(dst, garbage=4, deflate=True)
    doc.close()
    return hits


def process(path: str) -> dict:
    with pdfplumber.open(path) as pdf:
        pages = [(p.extract_text() or "") for p in pdf.pages]
    pool, info = build_pii_pool(pages)
    base, ext = os.path.splitext(path)
    out_dir = os.environ.get("MASK_OUT_DIR")
    if out_dir:
        dst = os.path.join(out_dir, os.path.basename(base) + "_masked" + ext)
    else:
        dst = base + "_masked" + ext
    hits = redact_pdf(path, dst, pool)
    return {"file": os.path.basename(path), "name": info["name"],
            "orgs": sorted(info["orgs"]), "pool": sorted(pool),
            "boxes": hits, "out": dst}


def main(argv):
    files = argv[1:] if len(argv) > 1 else (
        sorted(glob.glob("MIP*.pdf")) + sorted(glob.glob("Guest_Discussion*.pdf")))
    files = [f for f in files if "_masked" not in f]
    print(f"Processing {len(files)} file(s)\n" + "=" * 64)
    for f in files:
        r = process(f)
        print(f"\n{r['file']}")
        print(f"  NAME : {r['name']}")
        print(f"  ORGS : {r['orgs'] or '—'}")
        print(f"  POOL : {r['pool']}")
        print(f"  boxes drawn: {r['boxes']}  →  {os.path.basename(r['out'])}")
    print("\n" + "=" * 64 + "\nDone.")


if __name__ == "__main__":
    main(sys.argv)
