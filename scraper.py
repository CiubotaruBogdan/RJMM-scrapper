#!/usr/bin/env python3

import io
import re
import unicodedata
from typing import Dict, Any, List, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, render_template_string, request
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams

app = Flask(__name__)

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
    """Check if author exists on the website with proper diacritics normalization"""
    slug = _normalize_author_name(author_name)
    url = f"https://revistamedicinamilitara.ro/article-author/{slug}/"
    try:
        response = requests.head(url)
        return response.status_code != 404
    except:
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
        # 2026 format: affiliations appear before the Correspondence line
        corr_idx = -1
        for i, line in enumerate(lines):
            if "Correspondence" in line.strip():
                corr_idx = i
                break

        if corr_idx == -1:
            return affs

        affil_lines = lines[:corr_idx]
        i = 0
        while i < len(affil_lines):
            line = affil_lines[i].strip()

            match = re.match(r"^\s*(\d+|[" + _SUP_RANGE + r"]+)\s+(.+)$", line)
            if match:
                num = match.group(1).translate(_SUP_TO_DIG)
                content = match.group(2).strip()
                if _looks_like_institution(content):
                    affs.append((num, content))
            elif re.match(r"^\s*(\d+|[" + _SUP_RANGE + r"]+)\s*$", line):
                match = re.match(r"^\s*(\d+|[" + _SUP_RANGE + r"]+)\s*$", line)
                num = match.group(1).translate(_SUP_TO_DIG)
                content_lines = []
                j = i + 1
                while j < len(affil_lines):
                    next_line = affil_lines[j].strip()
                    if not next_line:
                        j += 1
                        continue
                    if re.match(r"^\s*(\d+|[" + _SUP_RANGE + r"]+)", next_line):
                        break
                    if _looks_like_institution_continuation(next_line):
                        content_lines.append(next_line)
                    j += 1
                if content_lines:
                    content = " ".join(content_lines)
                    if _looks_like_institution(content):
                        affs.append((num, content))
                i = j - 1

            i += 1
    
    # Sort affiliations by number
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

def _detect_article_type(text: str) -> str:
    """Detect article type from PDF content"""
    text_lower = text.lower()
    
    # Define patterns for different article types
    patterns = {
        "Original Research": [r"original\s+research", r"research\s+article", r"original\s+article"],
        "Review": [r"review\s+article", r"systematic\s+review", r"literature\s+review", r"mini.?review", r"^review$"],
        "Case Report": [r"case\s+report", r"case\s+study", r"clinical\s+case"],
        "Clinical Practice": [r"clinical\s+practice"],
        "Editorial": [r"editorial", r"editor.?s?\s+note"],
        "Letter": [r"letter\s+to\s+editor", r"correspondence", r"letter"],
        "Short Communication": [r"short\s+communication", r"brief\s+communication", r"rapid\s+communication"],
        "Commentary": [r"commentary", r"perspective", r"viewpoint"],
        "Technical Note": [r"technical\s+note", r"methodology", r"protocol"],
        "Education and Imaging": [r"education\s+and\s+imaging"]
    }
    
    # Check each pattern
    for article_type, type_patterns in patterns.items():
        for pattern in type_patterns:
            if re.search(pattern, text_lower):
                # WordPress mapping: convert "Original articles" (plural) to "Original article" (singular)
                if article_type == "Original Research":
                    return "Original article"
                return article_type
    
    return "Original article"  # Default (WordPress compatible)

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

def scrape(url: str, title_override: Optional[str] = None, issue: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Main scraping function."""
    import random, time

    BASE_URL = "https://revistamedicinamilitara.ro"

    session = requests.Session()

    retry_strategy = Retry(
        total=3,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["HEAD", "GET", "OPTIONS"],
        backoff_factor=2,
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("http://", adapter)
    session.mount("https://", adapter)

    # Realistic browser headers — Chrome 136 on Windows
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/136.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "DNT": "1",
    })

    try:
        # Warm-up: visit the homepage first to get cookies and establish session
        print(f"🌐 Warming up session on {BASE_URL}...")
        session.get(
            BASE_URL,
            timeout=15,
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        time.sleep(random.uniform(1.0, 2.5))

        print(f"📥 Downloading PDF from: {url}")
        response = session.get(
            url,
            timeout=30,
            allow_redirects=True,
            headers={
                "Accept": "application/pdf,application/octet-stream,*/*;q=0.8",
                "Referer": BASE_URL + "/",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
                "Upgrade-Insecure-Requests": "1",
            },
        )
        response.raise_for_status()
        
        print("📄 Extracting text from first page only...")
        text = _extract_first_page_text(io.BytesIO(response.content))
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

# HTML template - V6 with first page optimization badge
HTML = """
<!DOCTYPE html>
<html>
<head><title>RJMM PDF Scraper – 2025/2026</title>
<style>
body{font-family:Arial,sans-serif;max-width:1200px;margin:0 auto;padding:20px;background-color:#f5f5f5}
form{background:white;padding:20px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-bottom:20px}
label{display:block;margin:15px 0 5px;font-weight:bold;color:#333}
input,textarea,button{width:100%;padding:10px;border:1px solid #ddd;border-radius:4px;font-size:14px;box-sizing:border-box}
textarea{height:100px;resize:vertical;font-family:monospace}
button{background-color:#007cba;color:white;border:none;cursor:pointer;margin-top:10px}
button:hover{background-color:#005a87}
hr{margin:30px 0;border:none;border-top:2px solid #ddd}
#json{height:400px;background-color:#f8f9fa;font-family:monospace;font-size:12px}
.author-row{display:flex;gap:.5rem;align-items:center}.author-row input{flex:1}.author-order{max-width:120px}
.author-status-bullet{width:20px;height:20px;border-radius:50%;cursor:pointer;flex-shrink:0}
.author-status-bullet.status-exists{background-color:#dc3545}
.author-status-bullet.status-not-exists{background-color:#28a745}
.author-number{min-width:30px;font-weight:bold;text-align:center;color:#666}
.issue-example{font-size:0.9em;color:#666;margin-top:5px}
.button-group{display:flex;gap:10px;margin-top:10px}
.button-group button{margin-top:0}
.copy-btn{background-color:#007cba;color:white;border:none;padding:5px 10px;border-radius:3px;cursor:pointer;font-size:12px;min-width:50px;height:25px}
.copy-btn:hover{background-color:#005a87}
.copy-btn:active{background-color:#004570}
.corresponding-author{background-color:#fff9c4 !important}
.author-row-corresponding{background-color:#fff9c4;border-radius:5px;padding:5px}
.format-badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:bold;margin-left:10px}
.format-2022{background-color:#ff6b6b;color:white}
.format-2023{background-color:#4ecdc4;color:white}
.format-2024{background-color:#45b7d1;color:white}
.format-2025{background-color:#9b59b6;color:white}
.version-badge{background-color:#e74c3c;color:white;padding:5px 10px;border-radius:15px;font-size:12px;font-weight:bold;margin-left:10px}
</style>
</head>
<body>
<h1>RJMM PDF Scraper <span class="version-badge">2025 / 2026</span></h1>

<form method="post">
  <label>PDF URL *</label>
  <input name="url" value="{{url or ''}}" required>
  <label>Title override (optional)</label>
  <input name="title_override" value="{{override or ''}}" placeholder="Paste exact title if detection fails">
  <label>Issue (optional)</label>
  <input name="issue" value="{{issue or ''}}" placeholder="e.g., No.5 / 2025, Vol. CXXVIII, September">
  <div class="issue-example">Examples: No.5 / 2025, Vol. CXXVIII, September | No.6 / 2024, Vol. CXXVII, November</div>
  <button type="submit">Scrape</button>
  {% if data %}
  <div class="button-group">
    <button type="button" id="copyJSON">Copy Full JSON</button>
    <button type="button" id="copyAffiliations">Copy Affiliations JSON</button>
  </div>
  {% endif %}
</form>

{% if data %}
<hr>
{% if data.format_detected %}
<label>Detected Format <span class="format-badge format-{{data.format_detected}}">{{data.format_detected.upper()}}</span></label>
{% endif %}
<label>Title</label><input readonly onclick="cp(this)" value="{{data.title}}">
<label>Authors (full line)</label><textarea readonly onclick="cp(this)">{{data.authors_full}}</textarea>
<label>Authors (table)</label>
{% for a in data.authors %}
<div class="author-row {% if a.name in (data.correspondence_full or '') %}author-row-corresponding{% endif %}">
  <div class="author-status-bullet {% if a.exists %}status-exists{% else %}status-not-exists{% endif %}" onclick="cp(this)" title="{% if a.exists %}Author exists (RED){% else %}Author not found (GREEN){% endif %}"></div>
  <button class="copy-btn" onclick="copyAuthorJSON('{{a.name}}', '{{data.correspondence_email if a.name in (data.correspondence_full or '') else ''}}', '{{a.orders}}', {{loop.index}})">Copy</button>
  <div class="author-number">{{loop.index}}.</div>
  <input readonly onclick="cp(this)" value="{{a.name}}" {% if a.name in (data.correspondence_full or '') %}class="corresponding-author"{% endif %}>
  <input class="author-order" readonly onclick="cp(this)" value="{{a.orders}}">
</div>{% endfor %}
<label>Correspondence e‑mail</label><input readonly onclick="cp(this)" value="{{data.correspondence_email}}">
<label>Correspondence (full)</label><textarea readonly onclick="cp(this)">{{data.correspondence_full}}</textarea>
{% for aff in data.affiliations %}<label>Affiliation {{aff[0]}}</label><input readonly onclick="cp(this)" value="{{aff[1]}}">{% endfor %}
<label>DOI</label><input readonly onclick="cp(this)" value="{{data.doi}}">
<label>Abstract</label><textarea readonly onclick="cp(this)">{{data.abstract}}</textarea>
<label>Received</label><input readonly onclick="cp(this)" value="{{data.received_date}}">
<label>Revised</label><input readonly onclick="cp(this)" value="{{data.revised_date}}">
<label>Accepted</label><input readonly onclick="cp(this)" value="{{data.accepted_date}}">
<label>Academic Editor</label><input readonly onclick="cp(this)" value="{{data.academic_editor}}">
<label>Keywords</label><input readonly onclick="cp(this)" value="{{data.keywords}}">
<label>Article Type</label><input readonly onclick="cp(this)" value="{{data.article_type}}">
{% if data.issue %}<label>Issue</label><input readonly onclick="cp(this)" value="{{data.issue}}">{% endif %}
{% if data.year %}<label>Year</label><input readonly onclick="cp(this)" value="{{data.year}}">{% endif %}

<h3>JSON</h3>
<textarea id="json" readonly onclick="cp(this)">{{data|tojson(indent=2)}}</textarea>
{% endif %}

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

{% if data %}
localStorage.setItem('article_meta', JSON.stringify({{data|tojson}}));

document.getElementById('copyJSON').onclick=e=>{
  e.preventDefault();navigator.clipboard.writeText(document.getElementById('json').value)
    .then(()=>alert('Full JSON copied ✔'));
};

document.getElementById('copyAffiliations').onclick=e=>{
  e.preventDefault();
  const affiliationsOnly = {{data.affiliations|tojson}};
  navigator.clipboard.writeText(JSON.stringify(affiliationsOnly))
    .then(()=>alert('Affiliations JSON copied ✔'));
};
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


