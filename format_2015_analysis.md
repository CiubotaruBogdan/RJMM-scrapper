# 2015 Format Analysis

## Key Differences from Other Formats

### Issue Format
- **Pattern**: `No.X / 2015, Vol. CXVIII, New Series`
- **Example**: `No. 3/2015 • Romanian Journal of Military Medicine`
- **Location**: Line 1 of PDF

### Header Structure
Line 1: `Vol. CXVIII • New Series • No. X/2015 • Romanian Journal of Military Medicine`

### Article Type
Line 3: `ORIGINAL ARTICLES` (or other types)

### Dates Format
Line 5: `Article received on March 17, 2015 and accepted for publishing on April 19 2015.`
- Pattern: `Article received on [Month Day, Year] and accepted for publishing on [Month Day Year].`
- Note: Missing comma before year in accepted date

### Title
Line 7: Article title

### Authors
Lines 9-10: Authors with superscript affiliations
- Example: `Mariana Jinga 1,2, Irina I. Sima4, ...`

### Abstract
- Starts with `Abstract:` keyword
- Contains structured sections: Background, Introduction, Purpose, Goals & methods, Results, Conclusions

### Keywords
- Format: `Keywords: keyword1, keyword2, keyword3`

### Affiliations
- NOT visible on first page in samples analyzed
- Likely on subsequent pages or footer

## Comparison with 2017 Format

### Similarities
- Both have received/accepted dates
- Both have authors with superscript affiliation numbers
- Both have structured abstracts
- Both have keywords

### Differences
- **Issue format**: 2015 has "New Series", 2017 doesn't
- **Date format**: 2015 uses full text format, 2017 may differ
- **Header**: 2015 has bullet points (•) separator

## Detection Strategy

To detect 2015 format:
1. Check for "New Series" in first few lines
2. Check for date pattern: "Article received on ... and accepted for publishing on ..."
3. Check for Vol. CXVIII or similar Roman numeral volume
4. Check for year 2015 in URL or content

## Parsing Strategy

1. Extract issue from line 1: `No.X / 2015, Vol. CXVIII, New Series`
2. Extract article type from line 3
3. Extract dates from line 5 using regex for the specific pattern
4. Extract title from line 7
5. Extract authors from lines 9-10
6. Extract abstract (starts with "Abstract:")
7. Extract keywords (starts with "Keywords:")
8. Extract affiliations (need to check subsequent pages or footer)
