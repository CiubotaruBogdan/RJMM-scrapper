#!/usr/bin/env python3
"""
RJMM Article Scraper - Standalone Main Script
Processes PDF articles and extracts metadata for Romanian Journal of Military Medicine
"""

import io
import json
import os
import re
import unicodedata
from typing import Dict, Any, List, Optional, Tuple

import requests
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams
from pdfminer.pdfpage import PDFPage


# ============================================================================
# HELPER FUNCTIONS FROM SCRAPER
# ============================================================================

# Superscript to digit mapping
_SUP_RANGE = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUP_TO_DIG = str.maketrans(_SUP_RANGE, "0123456789")

# Pattern for affiliation start
AFFIL_START = re.compile(r"^\s*(\d+|[" + _SUP_RANGE + r"]+)\s")


def _normalize_author_name(author_name: str) -> str:
    """Normalize author name for URL slug creation with proper diacritics handling"""
    name = author_name.lower().strip()
    
    # Normalize Unicode characters (NFD = decomposed form)
    name = unicodedata.normalize('NFD', name)
    
    # Remove combining characters (accents, diacritics)
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
        names = [n.strip() for n in authors_full.split(",")]
        for name in names:
            if name and len(name) > 2:
                exists = _check_author_exists(name)
                out.append({"name": name, "orders": "", "exists": exists})
    
    return out


def _extract_first_page_text(pdf_file) -> str:
    """Extract text from first page only"""
    try:
        first_page = next(PDFPage.get_pages(pdf_file, maxpages=1))
        laparams = LAParams()
        text = extract_text(pdf_file, page_numbers=[0], laparams=laparams)
        return text
    except:
        return extract_text(pdf_file)


def _detect_format(text: str) -> str:
    """Detect PDF format (2020, 2022, 2023, 2024, 2025) based on content patterns"""
    
    if re.search(r"https://doi\.org/10\.55453/rjmm\.2025\.", text, re.I):
        print("🔧 Detected 2025 format based on DOI pattern")
        return "2025"
    
    if (re.search(r"(The )?[Aa]rticle (was )?received on .+accepted for publishing on .+2020\.", text, re.I) and
        not re.search(r"https://doi\.org/", text, re.I) and
        not re.search(r"doi:\s*\d", text, re.I)):
        print("🔧 Detected 2020 format based on accepted year 2020 + no DOI")
        return "2020"
    
    if re.search(r"doi:\s*\d", text, re.I):
        return "2022"
    
    if re.search(r"Vol\.\s+[IVXLC]+.*Romanian Journal", text, re.I):
        if re.search(r"Corresponding author:", text, re.I):
            return "2022"
        else:
            return "2023"
    
    if re.search(r"The article was received on [^,]+, \d{4}, and accepted for publishing on [^.]+\.", text, re.I):
        print("🔧 Detected 2022 format without DOI based on date pattern")
        return "2022"
    
    return "2024"


def _extract_doi_universal(text: str) -> str:
    """Extract DOI from any format and normalize to full URL"""
    doi_match = re.search(r"(https?://doi\.org/\S+)", text, re.I)
    if doi_match:
        return doi_match.group(1).strip()
    
    doi_match = re.search(r"doi:\s*(\S+)", text, re.I)
    if doi_match:
        doi_id = doi_match.group(1).strip()
        return f"https://doi.org/{doi_id}"
    
    return ""


def _parse_dates_flexible(text: str) -> Dict[str, str]:
    """Parse dates from all template formats"""
    dates = {"received": "", "revised": "", "accepted": ""}
    
    date_patterns = [
        r"Received:\s*([^\n\r]+).*?(?:Revised:\s*([^\n\r]+).*?)?Accepted:\s*([^\n\r]+)",
        r"Received:\s*([^R\n]+?)(?:\s+Revised:\s*([^A\n]+?))?\s+Accepted:\s*([^\n\r]+)",
        r"received on ([^,]+(?:, \d{4})?),?\s*(?:revised on ([^,]+),\s*)?and accepted for publishing on ([^.]+)\.",
        r"(?:The\s+)?article was received on ([^,]+(?:, \d{4})?),?\s*(?:revised on ([^,]+),\s*)?and accepted for publishing on ([^.]+)\."
    ]
    
    for pattern in date_patterns:
        match = re.search(pattern, text, re.I | re.S)
        if match:
            dates["received"] = match.group(1).strip()
            if match.group(2):
                dates["revised"] = match.group(2).strip()
            dates["accepted"] = match.group(3).strip()
            return dates
    
    return dates


def _extract_academic_editor(text: str) -> str:
    """Extract academic editor"""
    editor_patterns = [
        r"Academic Editor:\s*([^\n\r]+?)(?:\s+Received:|$)",
        r"Academic Editor[:\s]*([^\n\r]+)",
        r"Editor[:\s]*([^\n\r]+)"
    ]
    
    for pattern in editor_patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return match.group(1).strip()
    
    return ""


def _parse_page1_universal(text: str, title_override: Optional[str] = None) -> Dict[str, Any]:
    """Universal parser for all PDF formats - main parsing function"""
    
    # Detect format
    format_type = _detect_format(text)
    print(f"🔍 Detected format: {format_type}")
    
    data = {}
    data["format_detected"] = format_type
    data["doi"] = _extract_doi_universal(text)
    
    # Parse based on format
    if format_type == "2020":
        # 2020 format parsing
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        
        # Find article type
        article_type_idx = None
        for i, line in enumerate(lines):
            if any(t in line.upper() for t in ["ORIGINAL ARTICLE", "REVIEW", "CASE REPORT", "SYSTEMATIC REVIEW"]):
                article_type_idx = i
                data["article_type"] = line
                break
        
        # Find title and authors
        title_idx = None
        authors_idx = None
        
        for i in range(article_type_idx + 1 if article_type_idx else 0, min(len(lines), 20)):
            line = lines[i]
            
            # Skip date lines
            if re.search(r"(The )?[Aa]rticle (was )?received on", line):
                continue
            
            # Check if this looks like authors (has numbers or superscripts)
            has_affil_nums = bool(re.search(r"[0-9⁰¹²³⁴⁵⁶⁷⁸⁹]", line))
            
            if title_idx is None and not has_affil_nums:
                title_idx = i
            elif title_idx is not None and has_affil_nums:
                authors_idx = i
                break
        
        if title_override:
            data["title"] = title_override
        elif title_idx is not None:
            data["title"] = lines[title_idx]
        else:
            data["title"] = ""
        
        if authors_idx is not None:
            data["authors_full"] = lines[authors_idx]
        else:
            data["authors_full"] = ""
        
        # Parse affiliations (from page 1-2)
        affiliations = []
        for i, line in enumerate(lines):
            match = AFFIL_START.match(line)
            if match:
                num = match.group(1).translate(_SUP_TO_DIG)
                rest = line[match.end():].strip()
                if len(rest) > 10:  # Minimum length for valid affiliation
                    affiliations.append((num, rest[:80] + "..."))
        
        data["affiliations"] = affiliations
        
    else:
        # Other formats - simplified parsing
        data["title"] = title_override if title_override else ""
        data["authors_full"] = ""
        data["affiliations"] = []
    
    # Parse authors
    data["authors"] = _split_authors(data.get("authors_full", ""))
    
    # Parse dates
    dates = _parse_dates_flexible(text)
    data["received"] = dates["received"]
    data["revised"] = dates["revised"]
    data["accepted"] = dates["accepted"]
    
    # Parse academic editor
    data["academic_editor"] = _extract_academic_editor(text)
    
    # Extract correspondence email
    email_match = re.search(r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", text)
    data["correspondence_email"] = email_match.group(1) if email_match else ""
    data["correspondence_full"] = ""
    data["correspondence_name"] = ""
    
    # Extract abstract
    abstract_match = re.search(r"Abstract[:\s]+(.{100,1500}?)(?:\n\n|Keywords?:|INTRODUCTION)", text, re.I | re.S)
    data["abstract"] = abstract_match.group(1).strip() if abstract_match else ""
    
    # Extract keywords
    keywords_match = re.search(r"Keywords?[:\s]+([^\n]+)", text, re.I)
    data["keywords"] = keywords_match.group(1).strip() if keywords_match else ""
    
    data["citation"] = ""
    data["article_file"] = ""
    data["issue"] = ""
    data["year"] = ""
    
    return data


# ============================================================================
# MAIN SCRIPT FUNCTIONS
# ============================================================================

def scrape_local_pdf(pdf_path: str, title_override: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Scrape a local PDF file and extract metadata
    
    Args:
        pdf_path: Path to the local PDF file
        title_override: Optional title override
        
    Returns:
        Dictionary with extracted metadata or None on failure
    """
    try:
        print(f"\n📥 Processing PDF: {pdf_path}")
        
        if not os.path.exists(pdf_path):
            print(f"❌ File not found: {pdf_path}")
            return None
        
        # Read PDF file
        with open(pdf_path, 'rb') as f:
            pdf_content = f.read()
        
        pdf_file = io.BytesIO(pdf_content)
        
        # Quick check if it's 2020 format (needs 2 pages for affiliations)
        temp_text = _extract_first_page_text(io.BytesIO(pdf_content))
        is_2020 = (re.search(r"(The )?[Aa]rticle (was )?received on .+accepted for publishing on .+2020\.", temp_text, re.I) and
                   not re.search(r"https://doi\.org/", temp_text, re.I) and
                   not re.search(r"doi:\s*\d", temp_text, re.I))
        
        if is_2020:
            print("📄 Extracting text from first 2 pages (2020 format for affiliations)...")
            laparams = LAParams()
            text = extract_text(pdf_file, page_numbers=[0, 1], laparams=laparams)
        else:
            print("📄 Extracting text from first page only...")
            text = temp_text
        
        # Parse the PDF content
        print("🔍 Parsing PDF content...")
        data = _parse_page1_universal(text, title_override)
        
        # Set article file path
        data["article_file"] = pdf_path
        
        print("✅ Scraping completed successfully!")
        return data
        
    except Exception as e:
        print(f"❌ Error during scraping: {e}")
        import traceback
        traceback.print_exc()
        return None


def print_article_info(data: Dict[str, Any], article_num: int):
    """Print formatted article information"""
    print(f"\n{'='*80}")
    print(f"ARTICLE {article_num} - EXTRACTED METADATA")
    print('='*80)
    
    print(f"\n📝 Title: {data.get('title', 'N/A')}")
    print(f"📄 Format: {data.get('format_detected', 'N/A')}")
    print(f"🔗 DOI: {data.get('doi', 'N/A')}")
    
    print(f"\n👥 Authors ({len(data.get('authors', []))}):")
    for i, author in enumerate(data.get('authors', []), 1):
        exists_marker = "✓" if author.get('exists') else "✗"
        print(f"   {i}. {author.get('name', 'N/A')} [{exists_marker}] (Affiliations: {author.get('orders', 'N/A')})")
    
    print(f"\n🏛️ Affiliations ({len(data.get('affiliations', []))}):")
    for i, affil in enumerate(data.get('affiliations', []), 1):
        print(f"   {i}. {affil}")
    
    print(f"\n📧 Correspondence:")
    print(f"   Email: {data.get('correspondence_email', 'N/A')}")
    
    print(f"\n📅 Dates:")
    print(f"   Received: {data.get('received', 'N/A')}")
    if data.get('revised'):
        print(f"   Revised: {data.get('revised', 'N/A')}")
    print(f"   Accepted: {data.get('accepted', 'N/A')}")
    
    print(f"\n📚 Academic Editor: {data.get('academic_editor', 'N/A')}")
    
    print(f"\n📖 Abstract:")
    abstract = data.get('abstract', 'N/A')
    if len(abstract) > 300:
        print(f"   {abstract[:300]}...")
    else:
        print(f"   {abstract}")
    
    print(f"\n🔑 Keywords: {data.get('keywords', 'N/A')}")
    
    print('='*80)


def save_to_json(data: Dict[str, Any], output_file: str):
    """Save extracted data to JSON file"""
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"💾 Data saved to: {output_file}")
    except Exception as e:
        print(f"❌ Error saving to JSON: {e}")


def main():
    """Main function to process the three new articles"""
    print("\n" + "="*80)
    print("RJMM ARTICLE SCRAPER - Processing New Articles")
    print("="*80)
    
    # Define the three articles
    articles = [
        {
            'url': 'https://revistamedicinamilitara.ro/wp-content/uploads/2020/07/Evaluation-of-the-expression-level-of-Sine-oculis-homeobox-homolog-1-in-cervical-cancer-tissue-in-comparison-with-healthy-adjacent-tissue.pdf',
            'local_file': 'article1.pdf',
            'name': 'SIX1 Expression in Cervical Cancer'
        },
        {
            'url': 'https://revistamedicinamilitara.ro/wp-content/uploads/2020/07/The-strategic-need-for-the-implementation-of-a-technological-platform-for-the-microproduction-of-antidotes-for-the-CBRN-medical-protection.pdf',
            'local_file': 'article2.pdf',
            'name': 'CBRN Antidotes Platform'
        },
        {
            'url': 'https://revistamedicinamilitara.ro/wp-content/uploads/2020/07/Non%E2%80%91melanoma-skin-cancer-NMSC-Extramammary-Paget-%E2%80%99s-disease.pdf',
            'local_file': 'article3.pdf',
            'name': 'Extramammary Paget\'s Disease'
        }
    ]
    
    # Process each article
    results = []
    for i, article in enumerate(articles, 1):
        print(f"\n\n{'#'*80}")
        print(f"# Processing Article {i}/3: {article['name']}")
        print(f"{'#'*80}")
        
        # Check if local file exists
        if not os.path.exists(article['local_file']):
            print(f"⚠️  Local file not found: {article['local_file']}")
            print(f"📥 Downloading from: {article['url']}")
            
            try:
                response = requests.get(article['url'], timeout=30)
                response.raise_for_status()
                with open(article['local_file'], 'wb') as f:
                    f.write(response.content)
                print(f"✅ Downloaded successfully")
            except Exception as e:
                print(f"❌ Download failed: {e}")
                continue
        
        # Scrape the article
        data = scrape_local_pdf(article['local_file'])
        
        if data:
            # Add source URL to data
            data['source_url'] = article['url']
            data['article_name'] = article['name']
            
            # Print info
            print_article_info(data, i)
            
            # Save to JSON
            output_file = f"article{i}_metadata.json"
            save_to_json(data, output_file)
            
            results.append(data)
        else:
            print(f"❌ Failed to process article {i}")
    
    # Save all results to a combined file
    if results:
        print(f"\n\n{'='*80}")
        print(f"SUMMARY: Successfully processed {len(results)}/3 articles")
        print('='*80)
        
        combined_output = "all_articles_metadata.json"
        save_to_json(results, combined_output)
        print(f"\n💾 All results saved to: {combined_output}")
    else:
        print("\n❌ No articles were successfully processed")
    
    print("\n" + "="*80)
    print("Processing complete!")
    print("="*80 + "\n")


if __name__ == "__main__":
    main()
