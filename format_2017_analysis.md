# Format 2017 Analysis

## Structure Pattern Identified:

### Line Structure:
- Line 0: "ORIGINAL ARTICLES" 
- Line 2: "Article received on [date] and accepted for publishing on [date]."
- Line 4-6: Title (multiple lines)
- Line 8: Authors
- Line 10+: Abstract starts with "Abstract:"
- Keywords line: "Keywords: [list]"

### Key Characteristics:
1. **Date Pattern**: "Article received on [Month Day, Year] and accepted for publishing on [Month Day, Year]."
2. **No DOI**: These 2017 articles don't have DOI
3. **Abstract**: Starts with "Abstract:" keyword
4. **Keywords**: Starts with "Keywords:" keyword
5. **Authors**: Single line after title
6. **Affiliations**: Appear later in the document (around line 45+)

### Detection Pattern:
- Look for "Article received on" + year 2017
- No DOI present
- "Abstract:" keyword present

### Parsing Strategy:
- Title: Lines 4-6 (before authors)
- Authors: Line around 7-8 
- Abstract: After "Abstract:" keyword
- Keywords: After "Keywords:" keyword
- Dates: Extract from "Article received on..." line
