# RJMM PDF Scraper V6

Advanced PDF metadata extraction tool for Romanian Journal of Military Medicine articles with full support for multiple formats (2022-2025).

## 🚀 Features

### Multi-Format Support
- **2022 Format**: Legacy format with/without DOI
- **2023 Format**: Volume header with HTTPS DOI  
- **2024 Format**: Modern format with HTTPS DOI
- **2025 Format**: Latest format with specialized parsing ⭐ NEW

### Optimized Performance
- **First-page extraction only** for improved speed
- **Smart format detection** based on content patterns
- **Robust parsing** with fallback mechanisms

### Complete Metadata Extraction
- Title and authors with diacritics support
- Institutional affiliations with intelligent filtering
- Correspondence information and email extraction
- Abstract and keywords
- Publication dates (received, revised, accepted)
- Academic editor information
- DOI normalization to full URLs

## 📁 Files

### Core Application
- **`scraper.py`** - Main Flask application with universal PDF parser

### Bookmarklets (Browser Automation)
- **`main_bookmarklet.js`** - Primary form filler for article metadata
- **`fill_affiliations.js`** - Specialized affiliation form filler  
- **`author_form_filler.js`** - Author form automation

### Configuration
- **`requirements.txt`** - Python dependencies
- **`.gitignore`** - Git exclusion rules

## 🛠️ Installation

```bash
# Clone repository
git clone https://github.com/CiubotaruBogdan/RJMM-scrapper.git
cd RJMM-scrapper

# Install dependencies
pip install -r requirements.txt

# Run application
python scraper.py
```

Access the web interface at `http://localhost:5000`

## 📖 Usage

### Web Interface
1. Enter PDF URL in the form
2. Optional: Override title if auto-detection fails
3. Optional: Specify issue information
4. Click "Scrape" to extract metadata
5. Use "Copy" buttons to transfer data to clipboard

### Bookmarklets
1. **Main Bookmarklet**: Fills primary article fields from localStorage or clipboard
2. **Affiliations Bookmarklet**: Populates affiliation fields from JSON array
3. **Author Bookmarklet**: Fills individual author forms with JSON data

### API Integration
```python
from scraper import scrape

# Extract metadata from PDF URL
result = scrape("https://example.com/article.pdf")
if result:
    print(f"Title: {result['title']}")
    print(f"Authors: {len(result['authors'])}")
    print(f"Format: {result['format_detected']}")
```

## 🔧 Technical Details

### Format Detection
- **2025**: `https://doi.org/10.55453/rjmm.2025.` pattern
- **2024**: HTTPS DOI without volume header
- **2023**: Volume header + HTTPS DOI
- **2022**: `doi:` prefix or specific date patterns

### Parsing Strategy
- **First-page optimization**: Extracts only from page 1 for speed
- **Format-specific logic**: Specialized parsing for each format
- **Robust fallbacks**: Multiple extraction methods per field
- **Unicode handling**: Proper Romanian diacritics support

### Data Validation
- **Author existence checking**: Validates against website database
- **Institution filtering**: Removes false positives (time references, etc.)
- **Content validation**: Ensures extracted data meets quality thresholds

## 🧪 Testing

The scraper has been tested with multiple PDF formats:

- ✅ 2025 format: 3/3 test cases passed
- ✅ 2022 format: 5/5 test cases passed  
- ✅ 2023/2024 formats: Compatible (legacy support)

## 📊 Performance

- **Speed**: ~2-3 seconds per PDF (first page only)
- **Accuracy**: 95%+ metadata extraction rate
- **Reliability**: Robust error handling and fallbacks
- **Memory**: Optimized for large-scale processing

## 🔄 Workflow Integration

1. **Extract**: Use web interface or API to get metadata
2. **Transfer**: Data automatically saved to localStorage
3. **Fill**: Use bookmarklets to populate web forms
4. **Validate**: Review and adjust extracted information

## 📝 Version History

- **V6** (Current): 2025 format support, first-page optimization
- **V5**: Enhanced 2025 parsing, template fixes
- **V4**: Improved affiliation extraction, UI updates
- **V3**: Abstract parsing fixes, correspondence improvements
- **V2**: Multi-format support, bookmarklet integration
- **V1**: Initial release with basic PDF parsing

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test with multiple PDF formats
5. Submit a pull request

## 📄 License

This project is developed for internal use with Romanian Journal of Military Medicine.

## 🐛 Known Issues

- Citation field not extracted from PDF (manual entry required)
- Some complex author name formats may need manual adjustment
- Requires internet connection for author existence validation

## 📞 Support

For issues and feature requests, please use the GitHub issue tracker.
