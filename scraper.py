#!/usr/bin/env python3

import io
import re
import time
import unicodedata
from typing import Dict, Any, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, render_template_string, request
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams

app = Flask(__name__)

# ---------------------------------------------------------------------------
# HTTP session that looks like a real desktop Chrome browser.
#
# The cPanel/cpGuard rule "User-Agent associated with scripting/generic HTTP
# client" closes the TLS connection (SSLZeroReturnError / `unexpected eof while
# reading`) for any request that uses the default `python-requests/x.y` UA, an
# empty UA, or other obviously non-browser identifiers. A plain Chrome UA is
# enough to be allowed through, but we send the full set of headers a real
# Chrome would send so the request is indistinguishable from a normal visitor.
# ---------------------------------------------------------------------------

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_BROWSER_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,image/apng,*/*;q=0.8,"
        "application/signed-exchange;v=b3;q=0.7"
    ),
    "Accept-Language": "en-US,en;q=0.9,ro;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-User": "?1",
    "Sec-Fetch-Dest": "document",
    "sec-ch-ua": (
        '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
    ),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
    "Connection": "keep-alive",
}


def _build_session() -> requests.Session:
    """Return a requests Session pre-configured to look like Chrome and retry."""
    s = requests.Session()
    s.headers.update(_BROWSER_HEADERS)
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        backoff_factor=1.5,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET", "HEAD"]),
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=4, pool_maxsize=4)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


# Single shared session — keeps cookies (e.g. cpGuard challenge cookies) and
# warmed-up TLS connections across all requests in the process.
SESSION = _build_session()

# Superscript to digit mapping
_SUP_RANGE = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUP_TO_DIG = str.maketrans(_SUP_RANGE, "0123456789")

# Pattern for affiliation start
AFFIL_START = re.compile(r"^\s*(\d+|[" + _SUP_RANGE + r"]+)\s")

def _normalize_author_name(author_name: str) -> str:
    """Normalize author name for URL slug creation with proper diacritics handling"""
    name = author_name.lower().strip()
    
    # Normalize Unicode characters (NFD = decomposed form)
    # This separates characters like "á" into "a" + combining accent
    name = unicodedata.normalize('NFD', name)
    
    # Remove combining characters (accents, diacritics)
    # This keeps the base character and removes the accent
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    
    # Replace spaces and dots with hyphens
    name = re.sub(r'[\s.]+', '-', name)
    
    # Remove any remaining non-alphanumeric characters except hyphens
    name = re.sub(r'[^a-z0-9\-]', '', name)
    
    # Clean up multiple hyphens and leading/trailing hyphens
    name = re.sub(r'-+', '-', name).strip('-')
    
    return name

def _check_author_exists(author_name: str) -> bool:
    """Check if author exists on the website with proper diacritics normalization.

    Uses the shared browser-like SESSION so cpGuard does not block the request
    based on a `python-requests` User-Agent.
    """
    slug = _normalize_author_name(author_name)
    url = f"https://revistamedicinamilitara.ro/article-author/{slug}/"
    try:
        # Some hosts behind cpGuard react badly to HEAD; fall back to a tiny GET.
        response = SESSION.head(url, allow_redirects=True, timeout=15)
        if response.status_code in (405, 403):
            response = SESSION.get(url, allow_redirects=True, timeout=15, stream=True)
            response.close()
        return response.status_code != 404
    except Exception as e:
        print(f"⚠️  author existence check failed for '{author_name}': {e}")
        return False

def _split_authors(authors_full: str) -> list[dict[str, str]]:
    if not authors_full:
        return []
    sup_digits = _SUP_RANGE
    # FIXED: Added Romanian diacritics ăâîșțĂÂÎȘȚ to the character class
    pat = re.compile(
        r"\s*([A-Z][A-Za-zÀ-ÖØ-öø-ÿăâîșțĂÂÎȘȚ.\-'\s]+?)\s*"
        r"([0-9" + sup_digits + r"]+(?:\s*,\s*[0-9" + sup_digits + r"]+)*)"
    )
    out = []
    for m in pat.finditer(authors_full):
        name   = m.group(1).strip()
        orders = ", ".join(
            m.group(2).translate(_SUP_TO_DIG).replace(" ", "").split(",")
        )
        exists = _check_author_exists(name)
        out.append({"name": name, "orders": orders, "exists": exists})
    
    # Fallback: if no authors found (no affiliation numbers), split by comma
    if not out and "," in authors_full:
        print("🔧 No affiliation numbers found, splitting authors by comma")
        # Split by comma and clean up names
        names = [n.strip() for n in authors_full.split(",")]
        for name in names:
            if name and len(name) > 2:  # Skip empty or too short
                exists = _check_author_exists(name)
                out.append({"name": name, "orders": "", "exists": exists})
    
    return out

def _extract_first_page_text(pdf_file) -> str:
    """Extract text from first page only"""
    try:
        laparams = LAParams()
        text = extract_text(pdf_file, page_numbers=[0], laparams=laparams)
        return text
    except:
        # Fallback to full document if first page extraction fails
        return extract_text(pdf_file)

def _detect_format(text: str, url: str = "") -> str:
    """Detect PDF format: 2026 (Citation field) or 2025 (DOI pattern). Defaults to 2026."""
    if (re.search(r"Received:\s*\d+\s+\w+\s+202[56]", text, re.I) and
        re.search(r"Revised:\s*\d+\s+\w+\s+202[56]", text, re.I) and
        re.search(r"Accepted:\s*\d+\s+\w+\s+202[56]", text, re.I) and
        re.search(r"Citation:\s*\w+", text, re.I)):
        print("🔧 Detected 2026 format")
        return "2026"

    if re.search(r"https://doi\.org/10\.55453/rjmm\.2025\.", text, re.I):
        print("🔧 Detected 2025 format")
        return "2025"

    return "2026"

def _extract_doi_universal(text: str) -> str:
    """Extract DOI URL from PDF text."""
    doi_match = re.search(r"(https?://doi\.org/\S+)", text, re.I)
    return doi_match.group(1).strip() if doi_match else ""

def _split_content_universal(text: str) -> str:
    """Return text after the DOI line (used to locate title/authors)."""
    parts = re.split(r"https?://doi\.org/\S+\s*", text, maxsplit=1, flags=re.I)
    if len(parts) > 1:
        return parts[-1]
    return text

def _parse_dates_flexible(text: str) -> Dict[str, str]:
    """Parse Received/Revised/Accepted dates from 2025/2026 format PDFs."""
    dates = {"received": "", "revised": "", "accepted": ""}

    date_patterns = [
        r"Received:\s*([^\n\r]+).*?(?:Revised:\s*([^\n\r]+).*?)?Accepted:\s*([^\n\r]+)",
        r"Received:\s*([^R\n]+?)(?:\s+Revised:\s*([^A\n]+?))?\s+Accepted:\s*([^\n\r]+)",
    ]

    for pattern in date_patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            dates["received"] = match.group(1).strip()
            if match.group(2):
                dates["revised"] = match.group(2).strip()
            dates["accepted"] = match.group(3).strip()
            print(f"🔧 Found dates: received='{dates['received']}', revised='{dates['revised']}', accepted='{dates['accepted']}'")
            return dates

    for label, key in [("Received", "received"), ("Revised", "revised"), ("Accepted", "accepted")]:
        m = re.search(rf"{label}:\s*([^\n\r]+)", text, re.I)
        if m:
            dates[key] = m.group(1).strip()

    return dates

def _extract_academic_editor(text: str) -> str:
    """Extract academic editor - ENHANCED FOR 2025"""
    # 2025 inline format: "Academic Editor: Octavian Vasiliu"
    editor_patterns = [
        r"Academic Editor:\s*([^\n\r]+?)(?:\s+Received:|$)",
        r"Academic Editor[:\s]*([^\n\r]+)",
        r"Editor[:\s]*([^\n\r]+)"
    ]
    
    for pattern in editor_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            editor = match.group(1).strip()
            print(f"🔧 Found academic editor: '{editor}'")
            return editor
    
    return ""

def _title_authors_universal(text: str, format_type: str, override: Optional[str] = None) -> Tuple[str, str]:
    """Universal title and authors parsing for all formats"""
    def cleaned(s):
        import unicodedata
        # Normalize unicode characters (NFKD decomposes characters)
        s = unicodedata.normalize('NFKD', s)
        # Remove combining characters (accents, diacritics)
        s = ''.join(c for c in s if unicodedata.category(c) != 'Mn')
        # Clean whitespace
        return " ".join(s.replace("\u00A0", " ").split())

    if override:
        pat = re.escape(" ".join(override.split()))
        pat = re.sub(r"\s+", r"\\s+", pat)
        m = re.search(pat, text, re.I)
        if m:
            rest = text[m.end():]
            authors_lines = []
            for ln in rest.splitlines():
                if (AFFIL_START.match(ln) or "Correspondence" in ln or "Corresponding author" in ln
                        or re.match(r"^\s*(Abstract|Keywords?)\b", ln, re.I)):
                    break
                if ln.strip():
                    authors_lines.append(ln.strip())
            return override.strip(), cleaned(" ".join(authors_lines))

    # 2025 format: predictable structure — article type, DOI, title, authors
    if format_type == "2025":
        print("🔧 Using 2025 parsing logic")
        lines = text.splitlines()
        title = ""
        authors = ""
        for i in range(3, min(7, len(lines))):
            line = lines[i].strip()
            if line and not line.startswith("http") and not re.match(r"^\s*\w+\s*,", line):
                title = cleaned(line)
                print(f"🔧 2025 title at line {i}: '{title}'")
                break
        for i in range(5, min(9, len(lines))):
            line = lines[i].strip()
            if line and "," in line and (re.search(r"\d", line) or re.search("[" + _SUP_RANGE + "]", line)):
                authors = cleaned(line)
                print(f"🔧 2025 authors at line {i}: '{authors}'")
                break
        return title, authors

    # 2026 format: split after DOI, then extract title/authors
    after = _split_content_universal(text)
    lines = after.splitlines()
    i = 0

    while i < len(lines) and not lines[i].strip():
        i += 1

    def looks_author(ln):
        return "," in ln and (re.search(r"\d", ln) or re.search("[" + _SUP_RANGE + "]", ln))

    title_lines = []
    while i < len(lines):
        ln = lines[i].strip()
        if not ln or looks_author(ln):
            break
        title_lines.append(ln)
        i += 1

    while i < len(lines) and not lines[i].strip():
        i += 1

    authors = []
    while i < len(lines):
        ln = lines[i]
        if (AFFIL_START.match(ln) or "Correspondence" in ln
                or re.match(r"^\s*(Abstract|Keywords?)\b", ln, re.I)):
            break
        if ln.strip():
            authors.append(ln.strip())
        i += 1

    return cleaned(" ".join(title_lines)), cleaned(" ".join(authors))

def _correspondence_universal(text: str) -> Tuple[str, str]:
    """Extract correspondence email and full text. Format: 'Correspondence: Name, e-mail: ...'"""
    email = ""
    full_text = ""

    m = re.search(r"Correspondence:\s*([^\n\r]+)", text, re.I)
    if m:
        full_text = m.group(1).strip()
        email_m = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", full_text)
        if email_m:
            email = email_m.group(1)
        print(f"🔧 Correspondence found: '{full_text}' -> email: '{email}'")
        return email, full_text

    email_m = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)
    if email_m:
        email = email_m.group(1)

    return email, full_text

def _affiliations_universal(text: str, format_type: str) -> List[Tuple[str, str]]:
    """Extract affiliations from any format"""
    lines = text.splitlines()
    affs = []
    
    if format_type == "2025":
        print("🔧 Using specialized 2025 affiliations parsing")
        
        # For 2025: affiliations are typically at lines 8-11, before correspondence
        correspondence_idx = -1
        abstract_idx = -1
        
        for i, line in enumerate(lines):
            if line.strip().startswith("Correspondence:"):
                correspondence_idx = i
            elif line.strip().startswith("Abstract:"):
                abstract_idx = i
                break
        
        # Search for affiliations before correspondence
        start_idx = 7  # Start after typical authors line
        end_idx = correspondence_idx if correspondence_idx > 0 else (abstract_idx if abstract_idx > 0 else len(lines))
        
        print(f"🔧 2025 Looking for affiliations between lines {start_idx} and {end_idx}")
        
        sup_range = "⁰¹²³⁴⁵⁶⁷⁸⁹"
        
        for i in range(start_idx, min(end_idx, len(lines))):
            line = lines[i].strip()
            
            # Check if line starts with number or superscript followed by institution name
            match = re.match(r"^\s*(\d+|[" + sup_range + r"]+)\s+(.*)$", line)
            if match:
                num = match.group(1).translate(_SUP_TO_DIG)
                content = match.group(2).strip()
                
                # Check if it looks like an institution
                if _looks_like_institution(content):
                    # For 2025, affiliations are typically complete on one line
                    # Remove page numbers at the end
                    content = re.sub(r'\s+\d+\s*$', '', content)
                    
                    if content and len(content) > 10:
                        affs.append((num, content))
                        print(f"🔧 2025 Added affiliation {num}: {content[:50]}...")
    
    else:
        # 2026 format: affiliations appear before the Correspondence line.
        #
        # An affiliation can be either:
        #   (a) on a single line:    "1  Department of X, University Y, ...; user@x.ro (UX)"
        #   (b) split across lines, when the line wraps in the PDF and breaks the
        #       affiliation right in the middle (typically before another author's
        #       e-mail). Example from issue 2026/05:
        #           1  Doctoral School, ... (AFG),
        #           horia.blejan@drd.umfcd.ro (HB)
        #   (c) the rare case where only the number sits alone on its own line and
        #       the institution starts on the next line.
        #
        # Continuation rule: any non-empty line that does NOT start with a new
        # affiliation number AND is not the Correspondence/Abstract/Keywords/page
        # marker is treated as a continuation of the previous affiliation and is
        # appended to it (separated by a single space). Blank lines are ignored.
        corr_idx = -1
        for i, line in enumerate(lines):
            if "Correspondence" in line.strip():
                corr_idx = i
                break

        if corr_idx == -1:
            return affs

        affil_lines = lines[:corr_idx]
        num_re = re.compile(r"^\s*(\d+|[" + _SUP_RANGE + r"]+)(?:\s+(.+))?\s*$")
        # Lines that should never be merged into an affiliation (defensive guard).
        stop_re = re.compile(
            r"^\s*(?:Abstract\b|Keywords?\b|Correspondence\b|Citation\b|Received\b|"
            r"Revised\b|Accepted\b|Academic\s+Editor\b|https?://|\d{1,4}\s*$)",
            re.I,
        )

        i = 0
        while i < len(affil_lines):
            line = affil_lines[i].strip()
            m = num_re.match(line)
            if not m:
                i += 1
                continue

            num = m.group(1).translate(_SUP_TO_DIG)
            parts: List[str] = []
            if m.group(2):
                parts.append(m.group(2).strip())

            # Walk forward and absorb continuation lines until we hit either
            # another affiliation number, a blank-line separator that is followed
            # by a non-affiliation block, or a stop marker.
            j = i + 1
            while j < len(affil_lines):
                nxt = affil_lines[j].strip()
                if not nxt:
                    j += 1
                    continue
                if num_re.match(nxt):
                    break
                if stop_re.match(nxt):
                    break
                # Looks like a wrapped continuation — keep it.
                parts.append(nxt)
                j += 1

            content = " ".join(parts).strip()
            # Collapse trailing punctuation/space artefacts left from line-wrap.
            content = re.sub(r"\s+", " ", content).rstrip(" ,;")

            if content and _looks_like_institution(content):
                affs.append((num, content))

            i = j  # continue right after the absorbed block

    # Sort affiliations by number (numeric)
    affs.sort(key=lambda x: int(x[0]))
    return affs

def _looks_like_institution(text: str) -> bool:
    """Check if text looks like an institution name"""
    if not text or len(text) < 10:
        return False
    
    # Common institution keywords
    institution_keywords = [
        "university", "hospital", "college", "institute", "school", "department",
        "faculty", "center", "centre", "clinic", "medical", "emergency",
        "bucuresti", "bucharest", "romania", "timisoara", "cluj", "iasi",
        "universit", "spital", "clinica", "facultat", "haifa", "israel"
    ]
    
    text_lower = text.lower()
    
    # Must contain at least one institution keyword
    if not any(keyword in text_lower for keyword in institution_keywords):
        return False
    
    # Should not contain time references or content patterns
    time_patterns = [
        r"\d+\s+(week|month|day|year|hour)s?",
        r"postoperatively",
        r"preoperatively",
        r"mg/body",
        r"pmid:",
        r"doi:",
        r"figure \d+",
        r"table \d+",
        r"play a crucial role",
        r"onset and metastasis",
        r"\[\d+\]",  # References like [6]
        r"has become an important",
        r"as a direct consequence"
    ]
    
    for pattern in time_patterns:
        if re.search(pattern, text_lower):
            return False
    
    return True

def _looks_like_institution_continuation(text: str) -> bool:
    """Check if text looks like a continuation of institution name"""
    if not text:
        return False
    
    text_lower = text.lower()
    
    # Should not contain these patterns
    bad_patterns = [
        r"\d+\s+(week|month|day|year|hour)s?",
        r"postoperatively",
        r"preoperatively", 
        r"mg/body",
        r"pmid:",
        r"doi:",
        r"figure \d+",
        r"table \d+",
        r"^\d+\.",  # Reference number
        r"collection\s+was\s+performed",
        r"histological\s+sections"
    ]
    
    for pattern in bad_patterns:
        if re.search(pattern, text_lower):
            return False
    
    # Should be reasonable length
    if len(text) > 200:
        return False
    
    return True

# Article type label printed at the top-left of every RJMM article page,
# e.g. "REVIEW", "ORIGINAL ARTICLE", "CASE REPORT", "CASE SERIES", "EDITORIAL".
# Each entry maps a regex (matched against a single trimmed line) to the
# WordPress-compatible label we want to return. Order matters: more specific
# labels are listed first so e.g. "CASE SERIES" beats "CASE REPORT".
_HEADER_TYPE_RULES: List[Tuple[str, str]] = [
    (r"^(?:original\s+research|original\s+article|research\s+article)$", "Original article"),
    (r"^(?:systematic\s+review|literature\s+review|narrative\s+review|mini[-\s]?review|review\s+article|review)$", "Review"),
    (r"^(?:case\s+series|case\s+report|case\s+study|clinical\s+case)$", "Case Report"),
    (r"^education\s+and\s+imaging$",                                    "Education and Imaging"),
    (r"^short\s+communication|^brief\s+communication|^rapid\s+communication$", "Short Communication"),
    (r"^technical\s+note$",                                             "Technical Note"),
    (r"^clinical\s+practice$",                                          "Clinical Practice"),
    (r"^editorial$|^editor.?s?\s+note$",                                "Editorial"),
    (r"^letter\s+to\s+(?:the\s+)?editor$|^letter$",                     "Letter"),
    (r"^commentary$|^perspective$|^viewpoint$",                         "Commentary"),
]

# Generic body-text fallback patterns. These are stricter than the old set:
# bare words like "correspondence" or "letter" are NOT used here, because
# every RJMM article contains "Correspondence: <e-mail>" and that used to
# misclassify everything as "Letter".
_BODY_TYPE_RULES: List[Tuple[str, str]] = [
    (r"\bsystematic\s+review\b|\bnarrative\s+review\b|\bliterature\s+review\b|\breview\s+article\b|\bmini[-\s]?review\b", "Review"),
    (r"\bcase\s+series\b|\bcase\s+report\b|\bclinical\s+case\b",        "Case Report"),
    (r"\boriginal\s+article\b|\boriginal\s+research\b|\bresearch\s+article\b", "Original article"),
    (r"\beducation\s+and\s+imaging\b",                                  "Education and Imaging"),
    (r"\bshort\s+communication\b|\bbrief\s+communication\b|\brapid\s+communication\b", "Short Communication"),
    (r"\btechnical\s+note\b",                                           "Technical Note"),
    (r"\bclinical\s+practice\b",                                        "Clinical Practice"),
    (r"\beditorial\b",                                                  "Editorial"),
    (r"\bletter\s+to\s+(?:the\s+)?editor\b",                            "Letter"),
    (r"\bcommentary\b|\bperspective\b|\bviewpoint\b",                   "Commentary"),
]

def _detect_article_type(text: str) -> str:
    """Detect the article type printed at the top of an RJMM PDF page.

    Strategy (in order):
      1. Scan the first ~25 non-empty lines for a *line that is exactly* one of
         the known section labels (e.g. "REVIEW", "CASE SERIES"). This is the
         label printed in the top-left of the article in every RJMM template,
         and is by far the most reliable signal.
      2. If nothing is found, scan the **first page text** with stricter
         body-text patterns. Notably we do NOT trigger "Letter" on the bare
         word "correspondence" or "letter", because those occur in the
         "Correspondence:" e-mail line of every article.
      3. Default to "Original article" (WordPress compatible).
    """
    if not text:
        return "Original article"

    # --- 1) Header lookup (high confidence) ---------------------------------
    header_lines = [
        ln.strip() for ln in text.splitlines()[:60] if ln.strip()
    ][:25]
    for ln in header_lines:
        ln_low = ln.lower().strip("  .:-—–")
        for pattern, label in _HEADER_TYPE_RULES:
            if re.match(pattern, ln_low):
                return label

    # --- 2) Body-text fallback (strict) -------------------------------------
    text_low = text.lower()
    for pattern, label in _BODY_TYPE_RULES:
        if re.search(pattern, text_low):
            return label

    # --- 3) Default ---------------------------------------------------------
    return "Original article"

def _parse_issue(issue_str: str) -> Dict[str, str]:
    """Parse issue string like 'No.5 / 2025, Vol. CXXVIII, September'"""
    if not issue_str:
        return {"issue": "", "year": ""}
    
    # Extract year (4 digits)
    year_match = re.search(r"\b(\d{4})\b", issue_str)
    year = year_match.group(1) if year_match else ""
    
    return {"issue": issue_str.strip(), "year": year}

def _extract_abstract_improved(text: str) -> str:
    """Extract abstract with improved patterns"""
    
    # Method 1: Find Abstract: line and extract until Keywords:
    lines = text.splitlines()
    abstract_start_idx = -1
    keywords_start_idx = -1
    
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if line_stripped.startswith("Abstract:"):
            abstract_start_idx = i
        elif line_stripped.startswith("Keywords:"):
            keywords_start_idx = i
            break
    
    if abstract_start_idx >= 0:
        # Extract abstract content
        abstract_lines = []
        
        # Start from the Abstract: line itself
        first_line = lines[abstract_start_idx].strip()
        if first_line.startswith("Abstract:"):
            # Remove "Abstract:" prefix and add the rest
            first_content = first_line[9:].strip()  # Remove "Abstract:"
            if first_content:
                abstract_lines.append(first_content)
        
        # Continue with subsequent lines until Keywords: or end
        end_idx = keywords_start_idx if keywords_start_idx > 0 else len(lines)
        
        for i in range(abstract_start_idx + 1, end_idx):
            line = lines[i].strip()
            if not line:
                continue  # Skip empty lines
            if line.startswith("Keywords:"):
                break
            abstract_lines.append(line)
        
        if abstract_lines:
            abstract_text = " ".join(abstract_lines)
            # Clean up the abstract
            abstract_text = re.sub(r'\s+', ' ', abstract_text).strip()
            
            # Validate the abstract
            if len(abstract_text) > 50 and len(abstract_text) < 5000:
                # Remove common false matches
                if not any(bad in abstract_text.lower() for bad in ['pmid:', 'doi:', 'figure', 'table']):
                    print(f"🔧 Found abstract with line-by-line method: {len(abstract_text)} chars")
                    return abstract_text
    
    # Method 2: Regex-based extraction as fallback
    abstract_pattern = r"Abstract:\s*([^K]+?)(?=Keywords?:|$)"
    abstract_match = re.search(abstract_pattern, text, re.I | re.S)
    if abstract_match:
        abstract_text = abstract_match.group(1).strip()
        # Clean up the abstract
        abstract_text = re.sub(r'\s+', ' ', abstract_text)
        if len(abstract_text) > 50:  # Must be substantial
            print(f"🔧 Found abstract with regex method: {len(abstract_text)} chars")
            return abstract_text
    
    print("❌ No valid abstract found")
    return ""



def _parse_page1_universal(txt: str, format_detected: str, override: Optional[str] = None) -> Dict[str, Any]:
    """Parse PDF for 2025 or 2026 format."""
    data: Dict[str, Any] = {"format_detected": format_detected}
    print(f"🔍 Parsing with format: {format_detected}")

    # Extract DOI
    data["doi"] = _extract_doi_universal(txt)

    # Extract title and authors
    title, authors_full = _title_authors_universal(txt, format_detected, override)
    data["title"] = title
    data["authors_full"] = authors_full

    # Parse authors
    data["authors"] = _split_authors(authors_full)

    # Extract correspondence
    correspondence_email, correspondence_full = _correspondence_universal(txt)
    data["correspondence_email"] = correspondence_email if correspondence_email else "-"
    data["correspondence_full"] = correspondence_full

    # Extract affiliations
    data["affiliations"] = _affiliations_universal(txt, format_detected)

    # Detect article type
    data["article_type"] = _detect_article_type(txt)

    # Parse dates - ENHANCED FOR 2025 and 2026
    dates = _parse_dates_flexible(txt)
    data["received_date"] = dates["received"]
    data["revised_date"] = dates["revised"]
    data["accepted_date"] = dates["accepted"]

    # Extract academic editor - ENHANCED FOR 2025
    data["academic_editor"] = _extract_academic_editor(txt)

    # Extract keywords
    keywords_match = re.search(r"Keywords?:\s*([^\n\r]+)", txt, re.I)
    keywords = keywords_match.group(1).strip() if keywords_match else ""
    # Replace semicolons with commas
    keywords = keywords.replace(";", ",")
    data["keywords"] = keywords

    # Extract abstract
    data["abstract"] = _extract_abstract_improved(txt)

    # Citation - extract for 2026 format
    citation = ""
    citation_match = re.search(r"Citation:\s*([^\n\r]+(?:[\n\r]+[^\n\r]+)*?)(?=\n\s*\n|Received:|$)", txt, re.I | re.S)
    if citation_match:
        citation = citation_match.group(1).strip()
        # Clean up multiple spaces and newlines
        citation = re.sub(r'\s+', ' ', citation)
        print(f"🔧 Found citation: {citation[:100]}...")
    data["citation"] = citation

    # Article file (will be set by scrape function)
    data["article_file"] = ""

    # Issue and year (will be set if provided)
    data["issue"] = ""
    data["year"] = ""

    return data

def _warm_up(session: requests.Session, base_url: str) -> None:
    """Visit the homepage once so cpGuard sees a normal browsing flow.

    This collects any session cookies the WAF wants to set and primes the
    keep-alive connection. Failures are non-fatal — the actual PDF GET below
    works on its own with the browser headers.
    """
    try:
        r = session.get(base_url, timeout=15, allow_redirects=True)
        # Some servers return huge HTML; we don't need the body.
        r.close()
    except Exception as e:
        print(f"⚠️  warm-up to {base_url} failed (non-fatal): {e}")


def _download_pdf(url: str) -> bytes:
    """Download a PDF using a browser-like requests session.

    cpGuard on revistamedicinamilitara.ro blocks requests whose User-Agent
    looks like a script (e.g. `python-requests/...`, empty UA, `curl/...`)
    by closing the TLS handshake. Sending a realistic Chrome User-Agent and
    matching headers makes the request indistinguishable from a normal visit
    and the server returns the PDF directly — no headless browser needed.
    """
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}/"

    # First request from this process: warm up so we look like a user that
    # arrived from the homepage. Subsequent calls reuse the cookie jar.
    if not SESSION.cookies:
        _warm_up(SESSION, base_url)
        # Tiny pause so the request rate doesn't look automated.
        time.sleep(0.5)

    headers = {
        # PDFs need a slightly different Accept / Sec-Fetch profile than HTML.
        "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Dest": "document",
        "Referer": base_url,
    }

    last_exc: Optional[Exception] = None
    for attempt in range(1, 4):
        try:
            resp = SESSION.get(url, headers=headers, timeout=30, allow_redirects=True)
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").lower()
            if "pdf" not in ctype and not resp.content.startswith(b"%PDF"):
                raise RuntimeError(
                    f"unexpected content-type {ctype!r} (size={len(resp.content)}) "
                    f"— server may have served an HTML challenge page"
                )
            return resp.content
        except Exception as e:
            last_exc = e
            print(f"⚠️  PDF download attempt {attempt}/3 failed: {e}")
            time.sleep(1.5 * attempt)

    raise RuntimeError(f"failed to download PDF from {url}: {last_exc}")


def scrape(url: str, title_override: Optional[str] = None, issue: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Main scraping function.

    Uses a browser-like requests session to bypass the cpGuard / ModSecurity
    rule that blocks generic HTTP-client User-Agents. No headless browser is
    required for revistamedicinamilitara.ro.
    """
    try:
        print(f"📥 Downloading PDF: {url}")
        pdf_content = _download_pdf(url)

        print("📄 Extracting text from first page only...")
        text = _extract_first_page_text(io.BytesIO(pdf_content))
        format_detected = _detect_format(text, url)
        
        # Parse the content
        print("🔍 Parsing PDF content...")
        data = _parse_page1_universal(text, format_detected, title_override)
        
        # Set article file URL
        data["article_file"] = url
        
        # Parse issue if provided
        if issue:
            issue_data = _parse_issue(issue)
            data["issue"] = issue_data["issue"]
            data["year"] = issue_data["year"]
        
        print("✅ Scraping completed successfully!")
        return data

    except Exception as e:
        print(f"❌ Error during scraping: {e}")
        import traceback
        traceback.print_exc()
        return None

# HTML template — V7: full-width two-pane layout, PDF viewer on the left,
# parsed metadata + JSON on the right.
HTML = """
<!DOCTYPE html>
<html>
<head>
<title>RJMM PDF Scraper – 2025/2026</title>
<style>
*{box-sizing:border-box}
html,body{height:100%;margin:0;padding:0}
body{font-family:Arial,sans-serif;background-color:#f5f5f5;color:#222}

/* Top bar */
.topbar{display:flex;align-items:center;gap:1rem;padding:.6rem 1rem;background:#fff;border-bottom:1px solid #e3e3e3;box-shadow:0 1px 2px rgba(0,0,0,.04);position:sticky;top:0;z-index:10}
.topbar h1{font-size:1.05rem;margin:0;color:#333;white-space:nowrap}
.topbar form{flex:1;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;margin:0}
.topbar input[type=text]{flex:1;min-width:240px;padding:.45rem .6rem;border:1px solid #ccc;border-radius:4px;font-size:.85rem}
.topbar button{background:#007cba;color:#fff;border:none;padding:.5rem .9rem;border-radius:4px;cursor:pointer;font-size:.85rem;white-space:nowrap}
.topbar button:hover{background:#005a87}
.topbar .secondary{background:#6c757d}
.topbar .secondary:hover{background:#545b62}
.version-badge{background:#e74c3c;color:#fff;padding:.15rem .6rem;border-radius:12px;font-size:.7rem;font-weight:bold}
.format-badge{display:inline-block;padding:.15rem .6rem;border-radius:10px;font-size:.7rem;font-weight:bold;color:#fff;margin-left:.4rem}
.format-2022{background:#ff6b6b}.format-2023{background:#4ecdc4}.format-2024{background:#45b7d1}
.format-2025{background:#9b59b6}.format-2026{background:#16a085}

/* 2-pane layout: form on the LEFT, PDF on the RIGHT */
.split{display:flex;height:calc(100vh - 56px);width:100%}
.pane{height:100%;overflow:auto}
.pane-left{flex:0 0 45%;padding:0 1.1rem 4rem;background:#f5f5f5;border-right:1px solid #d0d0d0}
.pane-right.pdf-pane{flex:1;background:#222;display:flex;align-items:stretch;justify-content:center}
.pane-right.pdf-pane iframe,.pane-right.pdf-pane embed{width:100%;height:100%;border:0;background:#222}
.pane-right.pdf-pane .placeholder{color:#bbb;font-size:.95rem;align-self:center;text-align:center;padding:2rem}

/* Sticky quick-copy toolbar at the top of the form pane */
.toolbar{position:sticky;top:0;z-index:5;background:#fff;border:1px solid #e3e3e3;border-radius:6px;
  padding:.5rem .65rem;margin:.6rem 0 .9rem;display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;
  box-shadow:0 2px 4px rgba(0,0,0,.06)}
.toolbar strong{font-size:.78rem;color:#555;margin-right:.25rem}
.toolbar button{background:#007cba;color:#fff;border:none;padding:.35rem .65rem;border-radius:4px;
  cursor:pointer;font-size:.74rem;line-height:1.1}
.toolbar button:hover{background:#005a87}
.toolbar button.copied{background:#28a745}

/* Resizer */
.resizer{flex:0 0 6px;cursor:col-resize;background:#d6d6d6;position:relative}
.resizer:hover{background:#bdbdbd}
.resizer::before{content:"";position:absolute;top:50%;left:50%;width:2px;height:32px;background:#888;transform:translate(-50%,-50%);border-radius:2px}

/* Form fields on the right pane */
label{display:block;margin:.7rem 0 .25rem;font-weight:bold;color:#333;font-size:.82rem}
input[type=text],input:not([type]),textarea{width:100%;padding:.5rem .6rem;border:1px solid #ddd;border-radius:4px;font-size:.85rem;font-family:inherit;background:#fff}
textarea{min-height:90px;resize:vertical;font-family:ui-monospace,Consolas,monospace}
#json{min-height:280px;background:#fafafa;font-family:ui-monospace,Consolas,monospace;font-size:.78rem}
.section{background:#fff;padding:.8rem 1rem;border-radius:6px;box-shadow:0 1px 2px rgba(0,0,0,.05);margin-bottom:1rem}
.section h3{margin:0 0 .4rem;font-size:.95rem;color:#444}
.button-group{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.5rem}
.button-group button{margin:0;background:#007cba;color:#fff;border:none;padding:.45rem .8rem;border-radius:4px;cursor:pointer;font-size:.8rem}
.button-group button:hover{background:#005a87}

/* Authors */
.author-row{display:flex;gap:.5rem;align-items:center;margin-bottom:.35rem}
.author-row input{flex:1}
.author-order{max-width:110px}
.author-status-bullet{width:18px;height:18px;border-radius:50%;cursor:pointer;flex-shrink:0}
.author-status-bullet.status-exists{background:#dc3545}
.author-status-bullet.status-not-exists{background:#28a745}
.author-number{min-width:24px;font-weight:bold;text-align:center;color:#666;font-size:.8rem}
.copy-btn{background:#007cba;color:#fff;border:none;padding:.25rem .55rem;border-radius:3px;cursor:pointer;font-size:.72rem;min-width:48px;height:26px}
.copy-btn:hover{background:#005a87}
.corresponding-author{background:#fff9c4 !important}
.author-row-corresponding{background:#fff9c4;border-radius:4px;padding:.25rem .35rem}

/* Mobile / narrow viewports: stack panes (form first, PDF below) */
@media (max-width: 900px){
  .split{flex-direction:column;height:auto}
  .pane-left{flex:0 0 auto;width:100%;border-right:0;border-bottom:1px solid #d0d0d0}
  .pane-right.pdf-pane{flex:0 0 70vh;width:100%}
  .resizer{display:none}
  .toolbar{position:static}
}
</style>
</head>
<body>

<div class="topbar">
  <h1>RJMM PDF Scraper <span class="version-badge">2025 / 2026</span></h1>
  <form method="post" id="scrapeForm">
    <input type="text" name="url" value="{{url or ''}}" placeholder="PDF URL *" required>
    <input type="text" name="title_override" value="{{override or ''}}" placeholder="Title override (optional)">
    <input type="text" name="issue" value="{{issue or ''}}" placeholder="Issue (e.g. No.5 / 2025, Vol. CXXVIII, September)">
    <button type="submit">Scrape</button>
    {% if url %}<button type="button" class="secondary" onclick="location.href='/'">Reset</button>{% endif %}
  </form>
</div>

<div class="split" id="split">

  <!-- LEFT pane: parsed metadata + form -->
  <div class="pane pane-left" id="paneLeft">
    {% if data %}
      <div class="toolbar">
        <strong>Quick copy:</strong>
        <button type="button" id="copyJSON">Copy Full JSON</button>
        <button type="button" id="copyAffiliations">Copy Affiliations JSON</button>
        <button type="button" id="copyTitle">Copy Title</button>
        <button type="button" id="copyDOI">Copy DOI</button>
        <button type="button" id="copyAbstract">Copy Abstract</button>
        <button type="button" id="copyKeywords">Copy Keywords</button>
        <button type="button" id="copyEmail">Copy Corresp. e-mail</button>
      </div>
      <div class="section">
        <h3>Article
          {% if data.format_detected %}
            <span class="format-badge format-{{data.format_detected}}">{{data.format_detected.upper()}}</span>
          {% endif %}
        </h3>
        <label>Title</label><input id="fld_title" readonly onclick="cp(this)" value="{{data.title}}">
        <label>DOI</label><input id="fld_doi" readonly onclick="cp(this)" value="{{data.doi}}">
        <label>Article Type</label><input id="fld_type" readonly onclick="cp(this)" value="{{data.article_type}}">
        {% if data.issue %}<label>Issue</label><input readonly onclick="cp(this)" value="{{data.issue}}">{% endif %}
        {% if data.year %}<label>Year</label><input readonly onclick="cp(this)" value="{{data.year}}">{% endif %}
      </div>

      <div class="section">
        <h3>Authors</h3>
        <label>Authors (full line)</label>
        <textarea readonly onclick="cp(this)">{{data.authors_full}}</textarea>
        <label>Authors (table)</label>
        {% for a in data.authors %}
        <div class="author-row {% if a.name in (data.correspondence_full or '') %}author-row-corresponding{% endif %}">
          <div class="author-status-bullet {% if a.exists %}status-exists{% else %}status-not-exists{% endif %}" onclick="cp(this)"
               title="{% if a.exists %}Author exists (RED){% else %}Author not found (GREEN){% endif %}"></div>
          <button class="copy-btn" onclick="copyAuthorJSON('{{a.name}}', '{{data.correspondence_email if a.name in (data.correspondence_full or '') else ''}}', '{{a.orders}}', {{loop.index}})">Copy</button>
          <div class="author-number">{{loop.index}}.</div>
          <input readonly onclick="cp(this)" value="{{a.name}}" {% if a.name in (data.correspondence_full or '') %}class="corresponding-author"{% endif %}>
          <input class="author-order" readonly onclick="cp(this)" value="{{a.orders}}">
        </div>
        {% endfor %}
      </div>

      <div class="section">
        <h3>Correspondence</h3>
        <label>E-mail</label><input id="fld_email" readonly onclick="cp(this)" value="{{data.correspondence_email}}">
        <label>Full</label><textarea readonly onclick="cp(this)">{{data.correspondence_full}}</textarea>
      </div>

      <div class="section">
        <h3>Affiliations</h3>
        {% if data.affiliations %}
          {% for aff in data.affiliations %}
            <label>Affiliation {{aff[0]}}</label>
            <textarea readonly onclick="cp(this)" style="min-height:54px">{{aff[1]}}</textarea>
          {% endfor %}
        {% else %}
          <em style="color:#999">No affiliations detected.</em>
        {% endif %}
      </div>

      <div class="section">
        <h3>Abstract & Keywords</h3>
        <label>Abstract</label><textarea id="fld_abstract" readonly onclick="cp(this)" style="min-height:160px">{{data.abstract}}</textarea>
        <label>Keywords</label><input id="fld_keywords" readonly onclick="cp(this)" value="{{data.keywords}}">
      </div>

      <div class="section">
        <h3>Editorial dates</h3>
        <label>Received</label><input readonly onclick="cp(this)" value="{{data.received_date}}">
        <label>Revised</label><input readonly onclick="cp(this)" value="{{data.revised_date}}">
        <label>Accepted</label><input readonly onclick="cp(this)" value="{{data.accepted_date}}">
        <label>Academic Editor</label><input readonly onclick="cp(this)" value="{{data.academic_editor}}">
      </div>

      <div class="section">
        <h3>JSON</h3>
        <textarea id="json" readonly onclick="cp(this)">{{data|tojson(indent=2)}}</textarea>
      </div>

    {% else %}
      <div class="section">
        <p style="color:#666;margin:0">
          No article scraped yet. Paste the PDF URL in the top bar and press <b>Scrape</b>.<br>
          The PDF will load on the right so you can verify the parsed fields side by side.
        </p>
      </div>
    {% endif %}
  </div>

  <div class="resizer" id="resizer" title="Drag to resize"></div>

  <!-- RIGHT pane: PDF viewer -->
  <div class="pane pane-right pdf-pane" id="paneRight">
    {% if url %}
      <iframe id="pdfFrame" src="{{url}}#view=FitH" title="PDF preview"></iframe>
    {% else %}
      <div class="placeholder">
        Paste a PDF URL above and click <b>Scrape</b>.<br>
        The PDF will appear here for side-by-side review.
      </div>
    {% endif %}
  </div>

</div>

<script>
function cp(el){
  const txt=el.value||el.innerText||'';navigator.clipboard.writeText(txt).then(()=>{
    el.style.outline='2px solid lime';setTimeout(()=>el.style.outline='',400);
  });
}

function copyAuthorJSON(name, email, orders, authorNumber) {
  const button = event.target;
  const authorData = {
    name: name,
    email: email,
    order: orders,
    author_number: authorNumber
  };
  const jsonString = JSON.stringify(authorData, null, 2);
  
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(jsonString).then(() => {
      showCopySuccess(button);
    }).catch(err => {
      fallbackCopyToClipboard(jsonString, button);
    });
  } else {
    fallbackCopyToClipboard(jsonString, button);
  }
}

function fallbackCopyToClipboard(text, button) {
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    textarea.setSelectionRange(0, 99999);
    
    const successful = document.execCommand('copy');
    document.body.removeChild(textarea);
    
    if (successful) {
      showCopySuccess(button);
    } else {
      showCopyError(button, text, 'Fallback copy failed');
    }
  } catch (err) {
    showCopyError(button, text, 'Copy failed: ' + err.message);
  }
}

function showCopySuccess(button) {
  button.style.backgroundColor = '#28a745';
  button.textContent = 'Copied!';
  setTimeout(() => {
    button.style.backgroundColor = '#dc3545';
    button.textContent = 'Used';
  }, 1000);
}

function showCopyError(button, jsonString, message) {
  console.error('Copy failed:', message);
  button.style.backgroundColor = '#dc3545';
  button.textContent = 'Error!';
  setTimeout(() => {
    button.style.backgroundColor = '#007cba';
    button.textContent = 'Copy';
  }, 2000);
  alert('Copy failed! Here is the JSON to copy manually:\\n\\n' + jsonString);
}

/* --- Resizable split between PDF viewer and form pane --- */
(function(){
  const split = document.getElementById('split');
  const left  = document.getElementById('paneLeft');
  const bar   = document.getElementById('resizer');
  if (!split || !left || !bar) return;

  // Restore saved width (% of viewport).
  const saved = parseFloat(localStorage.getItem('rjmm_left_pct'));
  if (!isNaN(saved) && saved > 20 && saved < 85) {
    left.style.flex = '0 0 ' + saved + '%';
  }

  let dragging = false;
  bar.addEventListener('mousedown', e => {
    dragging = true;
    document.body.style.cursor = 'col-resize';
    document.body.style.userSelect = 'none';
    e.preventDefault();
  });
  document.addEventListener('mousemove', e => {
    if (!dragging) return;
    const rect = split.getBoundingClientRect();
    let pct = ((e.clientX - rect.left) / rect.width) * 100;
    if (pct < 20) pct = 20;
    if (pct > 85) pct = 85;
    left.style.flex = '0 0 ' + pct + '%';
  });
  document.addEventListener('mouseup', () => {
    if (!dragging) return;
    dragging = false;
    document.body.style.cursor = '';
    document.body.style.userSelect = '';
    const rect = split.getBoundingClientRect();
    const lrect = left.getBoundingClientRect();
    const pct = (lrect.width / rect.width) * 100;
    localStorage.setItem('rjmm_left_pct', pct.toFixed(2));
  });
})();

{% if data %}
localStorage.setItem('article_meta', JSON.stringify({{data|tojson}}));

function _flash(btn, label){
  if(!btn) return;
  const orig = btn.textContent;
  btn.classList.add('copied');
  btn.textContent = label || (orig + ' ✔');
  setTimeout(()=>{ btn.classList.remove('copied'); btn.textContent = orig; }, 1100);
}
function _bindCopy(id, getValue){
  const btn = document.getElementById(id);
  if (!btn) return;
  btn.onclick = e => {
    e.preventDefault();
    const v = (typeof getValue === 'function') ? getValue() : getValue;
    if (v == null) return;
    navigator.clipboard.writeText(String(v)).then(()=>_flash(btn));
  };
}

_bindCopy('copyJSON',         () => document.getElementById('json').value);
_bindCopy('copyAffiliations', () => JSON.stringify({{data.affiliations|tojson}}));
_bindCopy('copyTitle',        () => document.getElementById('fld_title')?.value || '');
_bindCopy('copyDOI',          () => document.getElementById('fld_doi')?.value || '');
_bindCopy('copyAbstract',     () => document.getElementById('fld_abstract')?.value || '');
_bindCopy('copyKeywords',     () => document.getElementById('fld_keywords')?.value || '');
_bindCopy('copyEmail',        () => document.getElementById('fld_email')?.value || '');
{% endif %}
</script>
</body>
</html>
"""

@app.route("/",methods=["GET","POST"])
def index():
    url=override=issue=data=None
    if request.method=="POST":
        url=request.form.get("url","").strip()
        override=request.form.get("title_override","").strip()
        issue=request.form.get("issue","").strip()
        if url:
            data=scrape(url,override or None, issue or None)
    return render_template_string(HTML,url=url,override=override,issue=issue,data=data)

if __name__=="__main__":
    app.run(debug=True)


