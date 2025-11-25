#!/usr/bin/env python3

import io
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams

def analyze_2017_pdf(filename):
    """Analyze 2017 format PDF structure"""
    print(f"\n{'='*60}")
    print(f"ANALYZING: {filename}")
    print(f"{'='*60}")
    
    try:
        # Extract first page text
        with open(filename, 'rb') as file:
            laparams = LAParams()
            text = extract_text(file, page_numbers=[0], laparams=laparams)
        
        lines = text.split('\n')
        
        print(f"Total lines: {len(lines)}")
        print(f"\nFirst 50 lines:")
        print("-" * 40)
        
        for i, line in enumerate(lines[:50]):
            line = line.strip()
            if line:
                print(f"{i:2d}: {line}")
        
        # Look for key patterns
        print(f"\n🔍 KEY PATTERNS FOUND:")
        print("-" * 40)
        
        for i, line in enumerate(lines):
            line = line.strip()
            if any(keyword in line.lower() for keyword in ['received', 'accepted', 'doi', 'abstract', 'keywords', 'correspondence']):
                print(f"Line {i:2d}: {line}")
        
        return text
        
    except Exception as e:
        print(f"❌ Error analyzing {filename}: {e}")
        return None

if __name__ == "__main__":
    pdfs = [
        "pdf_2017_1.pdf",
        "pdf_2017_2.pdf", 
        "pdf_2017_3.pdf"
    ]
    
    for pdf in pdfs:
        analyze_2017_pdf(pdf)
