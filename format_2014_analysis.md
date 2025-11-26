# 2014 Format Analysis

## Key Characteristics

### Header Structure
Line 1: `Vol. CXVII • New Series • No. 3-4/2014 • Romanian Journal of Military Medicine`

### Article Type
Line 3: `SYSTEMATIC REVIEW` (or other types like ORIGINAL ARTICLES, REVIEW, etc.)

### Dates Format
Line 5: `Article received on [Month Day, Year] and accepted for publishing on [Month Day Year].`
- Pattern: Same as 2015 format
- Example: `Article received on July 13, 2014 and accepted for publishing on July 29 2014.`
- Note: Missing comma before year in accepted date

### Title
Line 7-9: Article title (may span multiple lines)

### Authors
Line 9-11: Authors with superscript affiliations
- Example: `Dragoș Cuzino1, Oana M. Baston1, Cătălin Blaj1`
- Example: `Felician Chirteş1`

### Abstract
- Starts with `Abstract:` keyword
- May have structured sections (Introduction, Material and methods, Results and discussion, Conclusion)
- Or may be unstructured

### Keywords
- Format: `Keywords: keyword1, keyword2, keyword3`

### Affiliations
- NOT visible on first page in samples analyzed
- Likely on subsequent pages or footer

## Comparison with 2015 Format

### Similarities
- Both have "New Series" in header
- Both use bullet points (•) as separator
- Both have received/accepted dates in same format
- Both have authors with superscript affiliation numbers
- Both have keywords

### Differences
- **Year**: 2014 vs 2015
- **Volume**: Vol. CXVII (2014) vs Vol. CXVIII (2015)
- **Issue format**: No. 3-4/2014 (combined issue) vs No. 3/2015 (single issue)

## Detection Strategy

To detect 2014 format:
1. Check for "New Series" in first line
2. Check for year 2014 in header or dates
3. Check for Vol. CXVII (Roman numeral for 117)
4. Check for date pattern: "Article received on ... and accepted for publishing on ..."

## Parsing Strategy

Since 2014 format is nearly identical to 2015 format, we can:
1. Use the same parsing logic as 2015
2. Only adjust year detection (2014 instead of 2015)
3. Adjust volume number pattern (CXVII instead of CXVIII)
4. Handle combined issue numbers (3-4 instead of just 3)

## Implementation Plan

Create `scraper_2014.py` as a standalone scraper:
- Copy base structure from `scraper.py`
- Simplify to only handle 2014 format
- Use same parsing logic as `_parse_2015_format`
- Adjust regex patterns for 2014-specific patterns
- Keep same Flask interface for consistency
