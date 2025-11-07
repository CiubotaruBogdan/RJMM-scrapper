#!/usr/bin/env python3
"""
RJMM Article Scraper - Main Script
Processes PDF articles and extracts metadata for Romanian Journal of Military Medicine
"""

import io
import json
import os
from typing import Dict, Any, Optional

# Import scraper functions
from scraper import _parse_page1_universal, _extract_first_page_text
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams
import re


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
            # Extract first 2 pages for 2020 format to get affiliations
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
    print(f"   Name: {data.get('correspondence_name', 'N/A')}")
    print(f"   Email: {data.get('correspondence_email', 'N/A')}")
    print(f"   Full: {data.get('correspondence', 'N/A')}")
    
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
            
            import requests
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
