#!/usr/bin/env python3.11
"""Test 2026 format parsing"""

import sys
sys.path.insert(0, '/home/ubuntu/RJMM-scrapper')

from scraper import scrape
import json

# Test URL
test_url = "https://revistamedicinamilitara.ro/wp-content/uploads/2026/01/10.-The-diagnostic-value-of-cone-beam-computed-tomography-CBCT-in-the-evaluation-of-sinonasal-pathology-a-narrative-review.pdf"

print("=" * 80)
print("TESTING 2026 FORMAT PARSING")
print("=" * 80)

print(f"\nURL: {test_url}\n")

try:
    result = scrape(test_url)
    
    if result:
        print("\n✅ PARSING SUCCESSFUL")
        print(f"\nFormat detected: {result.get('format_detected', 'N/A')}")
        print(f"Title: {result.get('title', 'N/A')[:80]}...")
        print(f"Authors: {result.get('authors_full', 'N/A')[:80]}...")
        print(f"\n📅 DATES:")
        print(f"  Received: {result.get('received_date', 'N/A')}")
        print(f"  Revised: {result.get('revised_date', 'N/A')}")
        print(f"  Accepted: {result.get('accepted_date', 'N/A')}")
        print(f"\n📝 CITATION:")
        print(f"  {result.get('citation', 'N/A')}")
        print(f"\nAbstract: {result.get('abstract', 'N/A')[:100]}...")
        print(f"Keywords: {result.get('keywords', 'N/A')}")
        print(f"DOI: {result.get('doi', 'N/A')}")
        print(f"Affiliations: {len(result.get('affiliations', []))} found")
        for j, aff in enumerate(result.get('affiliations', [])[:3], 1):
            print(f"  {j}. [{aff[0]}] {aff[1][:60]}...")
        print(f"Correspondence: {result.get('correspondence_full', 'N/A')[:60]}...")
        
        # Save detailed result
        with open('test_2026_result.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"\n💾 Detailed result saved to test_2026_result.json")
    else:
        print("\n❌ PARSING FAILED - No result returned")
        
except Exception as e:
    print(f"\n❌ ERROR: {e}")
    import traceback
    traceback.print_exc()

print(f"\n{'='*80}")
print("TESTING COMPLETE")
print(f"{'='*80}\n")
