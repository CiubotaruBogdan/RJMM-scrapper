#!/usr/bin/env python3.11
"""Test 2015 format parsing"""

import sys
sys.path.insert(0, '/home/ubuntu/RJMM-scrapper')

from scraper import scrape
import json

# Test URLs
test_urls = [
    "https://revistamedicinamilitara.ro/wp-content/uploads/2016/04/2015-03-full.11-13.pdf",
    "https://revistamedicinamilitara.ro/wp-content/uploads/2016/04/2015-03-full.7-10.pdf",
    "https://revistamedicinamilitara.ro/wp-content/uploads/2016/04/2015-03-full.25-27.pdf",
    "https://revistamedicinamilitara.ro/wp-content/uploads/2016/04/2015-02-full.19-22.pdf",
    "https://revistamedicinamilitara.ro/wp-content/uploads/2016/04/2015-02-full.23-35.pdf"
]

print("=" * 80)
print("TESTING 2015 FORMAT PARSING")
print("=" * 80)

for i, url in enumerate(test_urls, 1):
    print(f"\n{'='*80}")
    print(f"TEST {i}/5: {url}")
    print(f"{'='*80}\n")
    
    try:
        result = scrape(url)
        
        if result:
            print("\n✅ PARSING SUCCESSFUL")
            print(f"\nFormat detected: {result.get('format_detected', 'N/A')}")
            print(f"Title: {result.get('title', 'N/A')[:80]}...")
            print(f"Authors: {result.get('authors_full', 'N/A')[:80]}...")
            print(f"Issue: {result.get('issue', 'N/A')}")
            print(f"Year: {result.get('year', 'N/A')}")
            print(f"Article Type: {result.get('article_type', 'N/A')}")
            print(f"Received: {result.get('received_date', 'N/A')}")
            print(f"Accepted: {result.get('accepted_date', 'N/A')}")
            print(f"Abstract: {result.get('abstract', 'N/A')[:100]}...")
            print(f"Keywords: {result.get('keywords', 'N/A')}")
            print(f"Affiliations: {len(result.get('affiliations', []))} found")
            for j, aff in enumerate(result.get('affiliations', [])[:3], 1):
                print(f"  {j}. [{aff[0]}] {aff[1][:60]}...")
            print(f"Correspondence: {result.get('correspondence_full', 'N/A')[:60]}...")
            
            # Save detailed result
            with open(f'test_2015_result_{i}.json', 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=2, ensure_ascii=False)
            print(f"\n💾 Detailed result saved to test_2015_result_{i}.json")
        else:
            print("\n❌ PARSING FAILED - No result returned")
            
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()

print(f"\n{'='*80}")
print("TESTING COMPLETE")
print(f"{'='*80}\n")
