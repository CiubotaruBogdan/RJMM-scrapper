#!/usr/bin/env python3

from scraper import scrape
import json

def test_2017_pdfs():
    """Test 2017 format with the downloaded PDFs"""
    
    test_urls = [
        "https://revistamedicinamilitara.ro/wp-content/uploads/2025/11/article_2_p15-20.pdf",
        "https://revistamedicinamilitara.ro/wp-content/uploads/2025/11/article_3_p21-25.pdf", 
        "https://revistamedicinamilitara.ro/wp-content/uploads/2025/11/article_4_p26-30.pdf"
    ]
    
    print("🧪 Testing 2017 Format Support")
    print("=" * 50)
    
    for i, url in enumerate(test_urls, 1):
        print(f"\n📄 Testing PDF {i}: {url}")
        print("-" * 40)
        
        try:
            result = scrape(url)
            
            if result:
                print(f"✅ SUCCESS!")
                print(f"Format detected: {result.get('format_detected', 'Unknown')}")
                print(f"Title: {result.get('title', 'Not found')[:80]}...")
                print(f"Authors: {len(result.get('authors', []))} found")
                print(f"Abstract: {len(result.get('abstract', ''))} characters")
                print(f"Keywords: {result.get('keywords', 'Not found')}")
                print(f"Received: {result.get('received_date', 'Not found')}")
                print(f"Accepted: {result.get('accepted_date', 'Not found')}")
                
                # Save detailed result
                with open(f"test_2017_result_{i}.json", "w") as f:
                    json.dump(result, f, indent=2, ensure_ascii=False)
                print(f"📁 Detailed result saved to test_2017_result_{i}.json")
                
            else:
                print("❌ FAILED - No result returned")
                
        except Exception as e:
            print(f"❌ ERROR: {e}")
    
    print(f"\n🎯 Testing completed!")

if __name__ == "__main__":
    test_2017_pdfs()
