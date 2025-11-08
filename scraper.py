#!/usr/bin/env python3

import io
import re
import unicodedata
from typing import Dict, Any, List, Optional, Tuple

import requests
from flask import Flask, render_template_string, request
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams
from pdfminer.pdfpage import PDFPage

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
        # Extract only first page
        first_page = next(PDFPage.get_pages(pdf_file, maxpages=1))
        laparams = LAParams()
        text = extract_text(pdf_file, page_numbers=[0], laparams=laparams)
        return text
    except:
        # Fallback to full document if first page extraction fails
        return extract_text(pdf_file)

def _detect_format(text: str) -> str:
    """Detect PDF format (2020, 2022, 2023, 2024, 2025) based on content patterns"""
    
    # Check for 2025 format first - HTTPS DOI with specific 2025 pattern
    if re.search(r"https://doi\.org/10\.55453/rjmm\.2025\.", text, re.I):
        print("🔧 Detected 2025 format based on DOI pattern")
        return "2025"
    
    # Check for 2020 format - Article type at beginning, no HTTPS DOI
    # 2020 format has: "Article received on..." or "The article was received on..." at the top
    # followed by article type (SYSTEMATIC REVIEW, ORIGINAL ARTICLE, REVIEW, etc.)
    # May or may not have volume header "Vol. CXXIII"
    # Key indicators:
    # 1. Accepted year 2019-2020 AND no DOI, OR
    # 2. Volume header with "/2020" (e.g., "Vol. CXXIII • No. 1/2020")
    has_2020_volume = re.search(r"Vol\.\s+[IVXLC]+.*?/2020", text, re.I)
    has_2019_2020_acceptance = re.search(r"(The )?[Aa]rticle (was )?received on .+accepted for publishing on .+(2019|2020)\.", text, re.I)
    has_no_doi = not re.search(r"https://doi\.org/", text, re.I) and not re.search(r"doi:\s*\d", text, re.I)
    
    if (has_2020_volume or (has_2019_2020_acceptance and has_no_doi)):
        print("🔧 Detected 2020 format based on volume/date pattern + no DOI")
        return "2020"
    
    # Check for 2022 format indicators
    if re.search(r"doi:\s*\d", text, re.I):
        return "2022"
    
    # Check for volume header (2022/2023)
    if re.search(r"Vol\.\s+[IVXLC]+.*Romanian Journal", text, re.I):
        # Further distinguish between 2022 and 2023
        if re.search(r"Corresponding author:", text, re.I):
            return "2022"
        else:
            return "2023"
    
    # Check for 2022 format without DOI but with specific date pattern
    if re.search(r"The article was received on [^,]+, \d{4}, and accepted for publishing on [^.]+\.", text, re.I):
        print("🔧 Detected 2022 format without DOI based on date pattern")
        return "2022"
    
    # Default to 2024 (no header, https DOI)
    return "2024"

def _extract_doi_universal(text: str) -> str:
    """Extract DOI from any format and normalize to full URL"""
    # Try 2025/2023/2024 format first (full URL)
    doi_match = re.search(r"(https?://doi\.org/\S+)", text, re.I)
    if doi_match:
        return doi_match.group(1).strip()
    
    # Try 2022 format (doi: prefix)
    doi_match = re.search(r"doi:\s*(\S+)", text, re.I)
    if doi_match:
        doi_id = doi_match.group(1).strip()
        return f"https://doi.org/{doi_id}"
    
    return ""

def _split_content_universal(text: str) -> str:
    """Split content after DOI for any format"""
    # Try 2025/2023/2024 format first (https://doi.org/)
    parts = re.split(r"https?://doi\.org/\S+\s*", text, maxsplit=1, flags=re.I)
    if len(parts) > 1:
        print("🔧 Split after HTTPS DOI")
        return parts[-1]
    
    # Try 2022 format with DOI (doi: prefix) - but only split after the FIRST occurrence
    # to avoid splitting after DOIs in references
    doi_match = re.search(r"doi:\s*\S+\s*", text, re.I)
    if doi_match:
        # Check if this DOI is near the beginning (within first 2000 characters)
        if doi_match.start() < 2000:
            parts = text[:doi_match.start()], text[doi_match.end():]
            print("🔧 Split after DOI prefix (early in document)")
            return parts[1]
    
    # For 2022 PDFs without DOI, don't split at all
    print("🔧 No DOI found - returning full text for 2022 format parsing")
    return text

def _parse_dates_flexible(text: str) -> Dict[str, str]:
    """Parse dates from all template formats - ENHANCED FOR 2025"""
    dates = {"received": "", "revised": "", "accepted": ""}
    
    # Enhanced patterns for 2025 format
    date_patterns = [
        # 2025 separate lines format: "Received: 21 June 2025" "Revised: 17 August 2025" "Accepted: 28 August 2025"
        r"Received:\s*([^\n\r]+).*?(?:Revised:\s*([^\n\r]+).*?)?Accepted:\s*([^\n\r]+)",
        # 2025 inline format: "Received: 21 June 2025  Revised: 17 August 2025  Accepted: 28 August 2025"
        r"Received:\s*([^R\n]+?)(?:\s+Revised:\s*([^A\n]+?))?\s+Accepted:\s*([^\n\r]+)",
        # 2025 format: "received on DD Month YYYY, and accepted for publishing on DD Month YYYY"
        r"received on ([^,]+(?:, \d{4})?),?\s*(?:revised on ([^,]+),\s*)?and accepted for publishing on ([^.]+)\.",
        # Legacy format: "The article was received on..."
        r"(?:The\s+)?article was received on ([^,]+(?:, \d{4})?),?\s*(?:revised on ([^,]+),\s*)?and accepted for publishing on ([^.]+)\."
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            dates["received"] = match.group(1).strip()
            if match.group(2):  # revised date is optional
                dates["revised"] = match.group(2).strip()
            dates["accepted"] = match.group(3).strip()
            print(f"🔧 Found dates: received='{dates['received']}', revised='{dates['revised']}', accepted='{dates['accepted']}'")
            return dates
    
    # Fallback: try individual line patterns for 2025
    received_match = re.search(r"Received:\s*([^\n\r]+)", text, re.I)
    if received_match:
        dates["received"] = received_match.group(1).strip()
    
    revised_match = re.search(r"Revised:\s*([^\n\r]+)", text, re.I)
    if revised_match:
        dates["revised"] = revised_match.group(1).strip()
    
    accepted_match = re.search(r"Accepted:\s*([^\n\r]+)", text, re.I)
    if accepted_match:
        dates["accepted"] = accepted_match.group(1).strip()
    
    # If we found at least received and accepted, return
    if dates["received"] and dates["accepted"]:
        print(f"🔧 Found dates (fallback): received='{dates['received']}', revised='{dates['revised']}', accepted='{dates['accepted']}'")
        return dates
    
    # Final fallback: try simpler pattern
    simple_pattern = r"received on ([^,]+(?:, \d{4})?)[^.]*accepted.*?on ([^.]+)"
    simple_match = re.search(simple_pattern, text, re.I | re.S)
    if simple_match:
        dates["received"] = simple_match.group(1).strip()
        dates["accepted"] = simple_match.group(2).strip()
    
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

    # NEW APPROACH FOR 2025 FORMAT
    if format_type == "2025":
        print("🔧 Using specialized 2025 parsing logic")
        
        lines = text.splitlines()
        
        # For 2025, structure is very predictable:
        # Line 0: ARTICLE_TYPE
        # Line 2: DOI
        # Line 4: TITLE
        # Line 6: AUTHORS
        # Line 8+: AFFILIATIONS
        
        title = ""
        authors = ""
        
        # Extract title (should be at line 4, but let's be flexible)
        for i in range(3, min(7, len(lines))):  # Check lines 3-6
            line = lines[i].strip()
            if line and not line.startswith("http") and not re.match(r"^\s*\w+\s*,", line):
                title = cleaned(line)
                print(f"🔧 2025 Found title at line {i}: '{title}'")
                break
        
        # Extract authors (should be at line 6, but let's be flexible)
        for i in range(5, min(9, len(lines))):  # Check lines 5-8
            line = lines[i].strip()
            if line and "," in line and (re.search(r"\d", line) or re.search("[" + _SUP_RANGE + "]", line)):
                authors = cleaned(line)
                print(f"🔧 2025 Found authors at line {i}: '{authors}'")
                break
        
        return title, authors

    # APPROACH FOR 2020 FORMAT
    elif format_type == "2020":
        print("🔧 Using specialized 2020 parsing logic")
        
        lines = text.splitlines()
        
        # Find article type line (SYSTEMATIC REVIEW, ORIGINAL ARTICLE, etc.)
        # All possible article types in the journal
        article_types = [
            "ARTICLE",
            "CLINICAL PRACTICE",
            "COMMENTARY",
            "COMMUNICATION",
            "LETTER",
            "LITERATURE REVIEW",
            "NARRATIVE REVIEW",
            "ORIGINAL ARTICLE",
            "ORIGINAL ARTICLES",
            "ORIGINAL RESEARCH",
            "REVIEW",
            "REVIEW ARTICLE",
            "SYSTEMATIC REVIEW",
            "CASE REPORT",
            "EDITORIAL",
            "VARIA"
        ]
        article_type_idx = -1
        
        for i, line in enumerate(lines[:20]):  # Check first 20 lines
            line_upper = line.strip().upper()
            if line_upper in article_types:
                article_type_idx = i
                print(f"🔧 Found article type at line {i}: {line.strip()}")
                break
        
        if article_type_idx == -1:
            print("❌ Could not find article type in 2020 format")
            return "", ""
        
        # Skip to after article type and date line
        # Structure: article_type (line 2) -> empty -> date (line 4) -> empty -> title starts (line 6)
        i = article_type_idx + 1
        
        # Skip empty line after article type
        while i < len(lines) and not lines[i].strip():
            i += 1
        
        # Skip date line (contains "Article received on" or "The article was received on")
        if i < len(lines) and ("Article received on" in lines[i] or "The article was received on" in lines[i]):
            print(f"🔧 Skipping date line at {i}: {lines[i].strip()[:50]}...")
            i += 1
        
        # Skip empty lines after date
        while i < len(lines) and not lines[i].strip():
            i += 1
        
        # Extract title lines (until we find a line that looks like authors)
        title_lines = []
        def looks_like_authors(ln):
            # Authors line has multiple names with commas
            # Must have comma, must NOT be a date line, must NOT start with Abstract/Keywords
            # Can have numbers (superscripts) or not
            has_comma = "," in ln
            not_date = "received" not in ln.lower() and "accepted" not in ln.lower()
            not_abstract = not ln.strip().startswith("Abstract:") and not ln.strip().startswith("Keywords:")
            # Check if it looks like names (has capital letters after commas)
            has_names = re.search(r",\s*[A-Z]", ln) is not None
            return has_comma and not_date and not_abstract and has_names
        
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            
            if looks_like_authors(line):
                print(f"🔧 Found authors line at index {i}: {line}")
                break
            
            # Stop if we hit abstract or keywords
            if line.startswith("Abstract:") or line.startswith("Keywords:"):
                break
            
            title_lines.append(line)
            i += 1
        
        # Extract authors lines
        authors_lines = []
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            
            # Stop at abstract, keywords, or affiliations
            if (line.startswith("Abstract:") or 
                line.startswith("Keywords:") or
                AFFIL_START.match(line) or
                "Correspondence" in line or 
                "Corresponding author" in line):
                break
            
            if looks_like_authors(line):
                authors_lines.append(line)
            
            i += 1
        
        title = cleaned(" ".join(title_lines))
        authors = cleaned(" ".join(authors_lines))
        
        print(f"🔧 2020 Extracted title: '{title}'")
        print(f"🔧 2020 Extracted authors: '{authors}'")
        
        return title, authors
    
    # APPROACH FOR 2022 FORMAT
    elif format_type == "2022":
        print("🔧 Using specialized 2022 parsing logic")
        
        lines = text.splitlines()
        date_line_idx = -1
        
        # Find the date line
        for i, line in enumerate(lines):
            if "The article was received on" in line and "accepted for publishing on" in line:
                date_line_idx = i
                print(f"🔧 Found date line at index {i}: {line.strip()}")
                break
        
        if date_line_idx == -1:
            print("❌ Could not find date line in 2022 format")
            return "", ""
        
        # Start looking for title after the date line
        i = date_line_idx + 1
        
        # Skip empty lines after date
        while i < len(lines) and not lines[i].strip():
            i += 1
        
        # Extract title lines (until we find a line that looks like authors)
        title_lines = []
        def looks_like_authors(ln):
            # Authors line typically has names with numbers/superscripts and commas
            return ("," in ln and 
                    (re.search(r"\d", ln) or re.search("[" + _SUP_RANGE + "]", ln)) and
                    not ln.strip().startswith("Abstract:") and
                    not ln.strip().startswith("Keywords:"))
        
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            
            if looks_like_authors(line):
                print(f"🔧 Found authors line at index {i}: {line}")
                break
            
            # Stop if we hit abstract or keywords
            if line.startswith("Abstract:") or line.startswith("Keywords:"):
                break
            
            title_lines.append(line)
            i += 1
        
        # Extract authors lines
        authors_lines = []
        while i < len(lines):
            line = lines[i].strip()
            if not line:
                i += 1
                continue
            
            # Stop at abstract, keywords, or affiliations
            if (line.startswith("Abstract:") or 
                line.startswith("Keywords:") or
                AFFIL_START.match(line) or
                "Correspondence" in line or 
                "Corresponding author" in line):
                break
            
            if looks_like_authors(line):
                authors_lines.append(line)
            
            i += 1
        
        title = cleaned(" ".join(title_lines))
        authors = cleaned(" ".join(authors_lines))
        
        print(f"🔧 2022 Extracted title: '{title}'")
        print(f"🔧 2022 Extracted authors: '{authors}'")
        
        return title, authors
    
    # For other formats (2023, 2024), use the existing logic with split content
    after = _split_content_universal(text)
    
    lines = after.splitlines()
    i = 0
    
    # Skip the date line (present in 2023/2024 formats)
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("The article was received on") or line.startswith("article was received on"):
            i += 1
            break
        elif line:  # If we find non-date content, stop skipping
            break
        i += 1
    
    # Skip empty lines after date
    while i < len(lines) and not lines[i].strip():
        i += 1
    
    # Skip format-specific headers
    if format_type in ["2022", "2023"] and i < len(lines):
        line = lines[i].strip()
        # Check for volume header pattern
        if re.match(r"Vol\.\s+[IVXLC]+\s*•.*Romanian Journal of Military Medicine", line, re.I):
            print(f"🔍 Skipping {format_type} header: {line}")
            i += 1
            # Skip empty lines after header
            while i < len(lines) and not lines[i].strip():
                i += 1
    
    # Skip article type line for 2022/2023 formats
    if format_type in ["2022", "2023"] and i < len(lines):
        line = lines[i].strip()
        # All possible article types
        article_types = [
            "ARTICLE", "CLINICAL PRACTICE", "COMMENTARY", "COMMUNICATION",
            "LETTER", "LITERATURE REVIEW", "NARRATIVE REVIEW", "ORIGINAL ARTICLE",
            "ORIGINAL ARTICLES", "ORIGINAL RESEARCH", "REVIEW", "REVIEW ARTICLE",
            "SYSTEMATIC REVIEW", "CASE REPORT", "EDITORIAL", "VARIA", "SHORT COMMUNICATION"
        ]
        if line.upper() in article_types:
            print(f"🔍 Skipping article type: {line}")
            i += 1
            # Skip empty lines after article type
            while i < len(lines) and not lines[i].strip():
                i += 1
    
    # Extract title lines
    title_lines = []
    def looks_author(ln):
        return "," in ln and (re.search(r"\d", ln) or re.search("[" + _SUP_RANGE + "]", ln))

    while i < len(lines):
        ln = lines[i].strip()
        if not ln or looks_author(ln):
            break
        title_lines.append(ln)
        i += 1

    # Skip empty lines before authors
    while i < len(lines) and not lines[i].strip():
        i += 1

    # Extract author lines
    authors = []
    while i < len(lines):
        ln = lines[i]
        if (AFFIL_START.match(ln) or "Correspondence" in ln or "Corresponding author" in ln
                or re.match(r"^\s*(Abstract|Keywords?)\b", ln, re.I)):
            break
        if ln.strip():
            authors.append(ln.strip())
        i += 1

    return cleaned(" ".join(title_lines)), cleaned(" ".join(authors))

def _correspondence_universal(text: str) -> Tuple[str, str]:
    """Extract correspondence email and full text for all formats"""
    email = ""
    full_text = ""
    
    # Try 2025 format: "Correspondence: Name, e-mail: email" OR "Correspondence: email"
    corr_match_2025 = re.search(r"Correspondence:\s*([^\n\r]+)", text, re.I)
    if corr_match_2025:
        full_text = corr_match_2025.group(1).strip()
        # Extract email from the line
        email_match = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", full_text)
        if email_match:
            email = email_match.group(1)
        print(f"🔧 Correspondence found: '{full_text}' -> email: '{email}'")
        return email, full_text
    
    # Try 2022 format: "Corresponding author: Name" followed by email on next line
    corr_match_2022 = re.search(r"Corresponding author:\s*([^\n\r]+)(?:\s*\n\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}))?", text, re.I)
    if corr_match_2022:
        full_text = corr_match_2022.group(1).strip()
        if corr_match_2022.group(2):  # Email on next line
            email = corr_match_2022.group(2).strip()
        else:
            # Look for email in the same line
            email_match = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", full_text)
            if email_match:
                email = email_match.group(1)
        return email, full_text
    
    # Fallback: search for any email in the text
    email_match = re.search(r"([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)
    if email_match:
        email = email_match.group(1)
    
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
    
    elif format_type == "2020":
        # For 2020: affiliations are AFTER correspondence, multi-line format
        correspondence_idx = -1
        abstract_idx = -1
        
        # For 2020, affiliations can be BEFORE or AFTER correspondence
        # Search the entire document but stop at major sections
        start_idx = 0
        end_idx = len(lines)
        
        # Find the end point (METHODS, RESULTS, DISCUSSION sections - not INTRODUCTION/BACKGROUND as affiliations may appear there)
        for i in range(0, len(lines)):
            line = lines[i].strip()
            if re.match(r'^(METHODS|RESULTS|DISCUSSION|REFERENCES|CONCLUSIONS?)$', line, re.I):
                end_idx = i
                break
        
        print(f"🔧 2020 Looking for affiliations between lines {start_idx} and {end_idx}")
        
        sup_range = "⁰¹²³⁴⁵⁶⁷⁸⁹"
        
        i = start_idx
        while i < min(end_idx, len(lines)):
            line = lines[i].strip()
            
            # Match affiliation number at start of line
            match = re.match(r"^\s*(\d+|[" + sup_range + r"]+)\s+(.*)$", line)
            if match:
                num = match.group(1).translate(_SUP_TO_DIG)
                content = match.group(2).strip()
                
                # Only accept affiliation numbers 1-9 (filter out false positives like "20 February")
                if int(num) > 9:
                    i += 1
                    continue
                
                if _looks_like_institution(content) or len(content) > 5:
                    # Collect continuation lines (multi-line affiliations)
                    content_lines = [content] if content else []
                    j = i + 1
                    
                    while j < min(end_idx, len(lines)):
                        next_line = lines[j].strip()
                        
                        # Empty line - skip but continue
                        if not next_line:
                            j += 1
                            continue
                        
                        # New affiliation number - stop
                        if re.match(r"^\s*(\d+|[" + sup_range + r"]+)\s+[A-Z]", next_line):
                            break
                        
                        # Abstract or other section - stop
                        if (next_line.startswith("Abstract:") or next_line.startswith("Keywords:") or
                            next_line.startswith("INTRODUCTION") or next_line.startswith("BACKGROUND")):
                            break
                        
                        # Looks like continuation of institution name
                        # Stop if line starts with lowercase (likely body text)
                        if next_line and next_line[0].islower():
                            break
                        
                        if _looks_like_institution_continuation(next_line):
                            content_lines.append(next_line)
                            j += 1
                        else:
                            break
                    
                    # Join all lines and clean up
                    full_content = " ".join(content_lines).strip()
                    # Remove page numbers at the end
                    full_content = re.sub(r'\s+\d+\s*$', '', full_content)
                    
                    if full_content and len(full_content) > 10 and _looks_like_institution(full_content):
                        affs.append((num, full_content))
                        print(f"🔧 2020 Added affiliation {num}: {full_content[:60]}...")
                    
                    i = j - 1
            
            i += 1
    
    elif format_type == "2022":
        # For 2022: affiliations are BEFORE correspondence
        correspondence_idx = -1
        abstract_idx = -1
        
        for i, line in enumerate(lines):
            if "Corresponding author:" in line:
                correspondence_idx = i
            elif line.strip().startswith("Abstract:"):
                abstract_idx = i
                break
        
        # Search for affiliations in the document
        start_idx = 0
        end_idx = correspondence_idx if correspondence_idx > 0 else len(lines)
        
        print(f"🔧 Looking for affiliations between lines {start_idx} and {end_idx}")
        
        sup_range = "⁰¹²³⁴⁵⁶⁷⁸⁹"
        
        for i in range(start_idx, min(end_idx, len(lines))):
            line = lines[i].strip()
            
            match = re.match(r"^\s*(\d+|[" + sup_range + r"]+)\s+(.*)$", line)
            if match:
                num = match.group(1).translate(_SUP_TO_DIG)
                content = match.group(2).strip()
                
                if _looks_like_institution(content):
                    # Collect continuation lines
                    content_lines = [content] if content else []
                    j = i + 1
                    
                    while j < min(end_idx, len(lines)):
                        next_line = lines[j].strip()
                        if not next_line:
                            j += 1
                            continue
                        
                        if re.match(r"^\s*(\d+|[" + sup_range + r"]+)\s+[A-Z]", next_line):
                            break
                        
                        if ("Correspondence" in next_line or "Corresponding author" in next_line or
                            re.match(r"^\s*(Abstract|Keywords?|INTRODUCTION|REFERENCES)\b", next_line, re.I)):
                            break
                        
                        if _looks_like_institution_continuation(next_line):
                            content_lines.append(next_line)
                        else:
                            break
                        
                        j += 1
                    
                    full_content = " ".join(content_lines).strip()
                    full_content = re.sub(r'\s+\d+\s*$', '', full_content)
                    
                    if full_content and len(full_content) > 10 and _looks_like_institution(full_content):
                        affs.append((num, full_content))
                        print(f"🔧 Added affiliation {num}: {full_content[:50]}...")
    
    else:
        # For 2023/2024: affiliations are right after authors (top of page)
        corr_idx = -1
        for i, line in enumerate(lines):
            if ("Correspondence" in line.strip() or "Corresponding author" in line.strip()):
                corr_idx = i
                break
        
        if corr_idx == -1:
            return affs
        
        affil_lines = lines[:corr_idx]
        
        i = 0
        while i < len(affil_lines):
            line = affil_lines[i].strip()
            
            if re.match(r"^\s*(\d+|[" + _SUP_RANGE + r"]+)\s+(.+)$", line):
                match = re.match(r"^\s*(\d+|[" + _SUP_RANGE + r"]+)\s+(.+)$", line)
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
        "Editorial": [r"editorial", r"editor.?s?\s+note"],
        "Letter": [r"letter\s+to\s+editor", r"correspondence", r"letter"],
        "Short Communication": [r"short\s+communication", r"brief\s+communication", r"rapid\s+communication"],
        "Commentary": [r"commentary", r"perspective", r"viewpoint"],
        "Technical Note": [r"technical\s+note", r"methodology", r"protocol"]
    }
    
    # Check each pattern
    for article_type, type_patterns in patterns.items():
        for pattern in type_patterns:
            if re.search(pattern, text_lower):
                return article_type
    
    return "Original Research"  # Default

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

def _parse_page1_universal(txt: str, override: Optional[str] = None) -> Dict[str, Any]:
    """Universal parser for all PDF formats (2022, 2023, 2024, 2025) - V6 OPTIMIZED FOR FIRST PAGE"""
    data: Dict[str, Any] = {}

    # Detect format first
    format_detected = _detect_format(txt)
    data["format_detected"] = format_detected
    print(f"🔍 Detected format: {format_detected}")

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
    data["correspondence_email"] = correspondence_email
    data["correspondence_full"] = correspondence_full

    # Extract affiliations
    data["affiliations"] = _affiliations_universal(txt, format_detected)

    # Detect article type
    data["article_type"] = _detect_article_type(txt)

    # Parse dates - ENHANCED FOR 2025
    dates = _parse_dates_flexible(txt)
    data["received"] = dates["received"]
    data["revised"] = dates["revised"]
    data["accepted"] = dates["accepted"]

    # Extract academic editor - ENHANCED FOR 2025
    data["academic_editor"] = _extract_academic_editor(txt)

    # Extract keywords
    keywords_match = re.search(r"Keywords?:\s*([^\n\r]+)", txt, re.I)
    data["keywords"] = keywords_match.group(1).strip() if keywords_match else ""

    # Extract abstract
    data["abstract"] = _extract_abstract_improved(txt)

    # Citation (placeholder - not typically in PDF)
    data["citation"] = ""

    # Article file (will be set by scrape function)
    data["article_file"] = ""

    # Issue and year (will be set if provided)
    data["issue"] = ""
    data["year"] = ""

    return data

def scrape(url: str, title_override: Optional[str] = None, issue: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Main scraping function - V6 OPTIMIZED FOR FIRST PAGE ONLY"""
    try:
        print(f"📥 Downloading PDF from: {url}")
        
        # Download PDF
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        # Extract text - first page only for most formats, 2 pages for 2020
        pdf_file = io.BytesIO(response.content)
        
        # Quick check if it's 2020 format (needs 2 pages for affiliations)
        temp_text = _extract_first_page_text(io.BytesIO(response.content))
        is_2020 = (re.search(r"(The )?[Aa]rticle (was )?received on .+accepted for publishing on .+2020\.", temp_text, re.I) and
                   not re.search(r"https://doi\.org/", temp_text, re.I) and
                   not re.search(r"doi:\s*\d", temp_text, re.I))
        
        if is_2020:
            print("📄 Extracting text from first 2 pages (2020 format for affiliations)...")
            # Extract first 2 pages for 2020 format to get affiliations
            from pdfminer.high_level import extract_text
            from pdfminer.layout import LAParams
            laparams = LAParams()
            text = extract_text(pdf_file, page_numbers=[0, 1], laparams=laparams)
        else:
            print("📄 Extracting text from first page only...")
            text = temp_text
        
        # Parse the first page
        print("🔍 Parsing PDF content...")
        data = _parse_page1_universal(text, title_override)
        
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
<head><title>PDF Scraper V6</title>
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
<h1>PDF Scraper V6 <span class="version-badge">FIRST PAGE OPTIMIZED</span></h1>

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
{% for n,aff in data.affiliations %}<label>Affiliation {{loop.index}}</label><input readonly onclick="cp(this)" value="{{aff}}">{% endfor %}
<label>DOI</label><input readonly onclick="cp(this)" value="{{data.doi}}">
<label>Abstract</label><textarea readonly onclick="cp(this)">{{data.abstract}}</textarea>
<label>Received</label><input readonly onclick="cp(this)" value="{{data.received}}">
<label>Revised</label><input readonly onclick="cp(this)" value="{{data.revised}}">
<label>Accepted</label><input readonly onclick="cp(this)" value="{{data.accepted}}">
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
