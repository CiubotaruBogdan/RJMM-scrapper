"""
RJMM PDF Scraper - 2014 Format Only
Standalone scraper dedicated exclusively to 2014 PDF format
"""

import io
import re
import unicodedata
from typing import Dict, Any, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from flask import Flask, render_template_string, request
from pdfminer.high_level import extract_text
from pdfminer.layout import LAParams
from pdfminer.pdfpage import PDFPage

app = Flask(__name__)

# Superscript to digit mapping
_SUP_RANGE = "⁰¹²³⁴⁵⁶⁷⁸⁹"
_SUP_TO_DIG = str.maketrans(_SUP_RANGE, "0123456789")

def _normalize_author_name(author_name: str) -> str:
    """Normalize author name for URL slug creation with proper diacritics handling"""
    name = author_name.lower().strip()
    name = unicodedata.normalize('NFD', name)
    name = ''.join(c for c in name if unicodedata.category(c) != 'Mn')
    name = re.sub(r'[\s.]+', '-', name)
    name = re.sub(r'[^a-z0-9\-]', '', name)
    name = re.sub(r'-+', '-', name).strip('-')
    return name

def _check_author_exists(author_name: str) -> bool:
    """Check if author exists on the website"""
    slug = _normalize_author_name(author_name)
    url = f"https://revistamedicinamilitara.ro/article-author/{slug}/"
    try:
        response = requests.head(url, timeout=5)
        return response.status_code != 404
    except:
        return False

def _split_authors(authors_full: str) -> list[dict[str, str]]:
    """Split authors string into individual authors with affiliation numbers"""
    if not authors_full:
        return []
    
    sup_digits = _SUP_RANGE
    pat = re.compile(
        r"\s*([A-Z][A-Za-zÀ-ÖØ-öø-ÿăâîșțĂÂÎȘȚ.\-'\s]+?)\s*"
        r"([0-9" + sup_digits + r"]+(?:\s*,\s*[0-9" + sup_digits + r"]+)*)"
    )
    out = []
    for m in pat.finditer(authors_full):
        name = m.group(1).strip()
        orders = ", ".join(
            m.group(2).translate(_SUP_TO_DIG).replace(" ", "").split(",")
        )
        exists = _check_author_exists(name)
        out.append({"name": name, "orders": orders, "exists": exists})
    
    # Fallback: if no authors found, split by comma
    if not out and "," in authors_full:
        names = [n.strip() for n in authors_full.split(",")]
        for name in names:
            if name and len(name) > 2:
                exists = _check_author_exists(name)
                out.append({"name": name, "orders": "", "exists": exists})
    
    return out

def _parse_2014_format(txt: str) -> Dict[str, Any]:
    """Parse 2014 format PDF"""
    data: Dict[str, Any] = {"format_detected": "2014"}
    
    lines = txt.split('\n')
    cleaned = lambda x: re.sub(r'\s+', ' ', x).strip()
    
    # Extract issue from line 1: "Vol. CXVII • New Series • No. 3-4/2014 • Romanian Journal of Military Medicine"
    issue = ""
    if len(lines) > 0:
        first_line = lines[0].strip()
        # Extract the full issue string
        issue_match = re.search(r"(Vol\.\s+[IVXLC]+.*?No\.\s*[\d\-]+/2014)", first_line, re.I)
        if issue_match:
            issue = issue_match.group(1).strip()
    
    # Extract year
    year = "2014"
    
    # Extract article type from line 3
    article_type = ""
    if len(lines) > 2:
        type_line = lines[2].strip()
        if type_line:
            article_type = type_line
    
    # Extract dates from "Article received on" line (typically line 5)
    received_date = ""
    accepted_date = ""
    
    for line in lines[:10]:
        if "Article received on" in line:
            # Pattern: "Article received on July 13, 2014 and accepted for publishing on July 29 2014."
            date_match = re.search(r"Article received on (.+?) and accepted for publishing on (.+?)\.?$", line, re.I)
            if date_match:
                received_date = date_match.group(1).strip()
                accepted_date = date_match.group(2).strip()
            break
    
    # Extract title (typically line 7)
    title = ""
    title_lines = []
    for i in range(6, min(12, len(lines))):
        line = lines[i].strip()
        # Skip empty lines, dates, emails, author patterns
        if (line and 
            not line.startswith("http") and 
            not "received on" in line.lower() and
            not "accepted for publishing" in line.lower() and
            not re.match(r"^\s*\w+\s+\w+\s*\d", line) and
            not line.startswith("Abstract:")):
            title_lines.append(cleaned(line))
        # Stop if we hit an author line or abstract
        if (re.search(r"[A-Z][a-z]+ [A-Z][a-z]+.*?\d", line) or 
            line.startswith("Abstract:")):
            break
        if len(title_lines) >= 2:
            break
    
    title = " ".join(title_lines)
    
    # Extract authors (lines after title, before abstract)
    authors_full = ""
    for i in range(6, min(15, len(lines))):
        line = lines[i].strip()
        # Look for author pattern: names with superscript numbers
        if re.search(r"[A-Z][a-z]+ [A-Z][a-z]+.*?\d", line) and not line.startswith("Abstract:"):
            authors_full = cleaned(line)
            # Check if authors continue on next line
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith("Abstract:") and re.search(r"[A-Z][a-z]+ [A-Z][a-z]+", next_line):
                    authors_full += " " + cleaned(next_line)
            break
    
    # Extract abstract (starts with "Abstract:")
    abstract = ""
    for i, line in enumerate(lines):
        if line.strip().startswith("Abstract:"):
            # Collect abstract lines until Keywords or first section
            abstract_lines = []
            for j in range(i, len(lines)):
                abstract_line = lines[j].strip()
                if (abstract_line.startswith("Keywords:") or 
                    abstract_line in ["INTRODUCTION", "ANATOMY OF THE PROSTATE GLAND", "MATERIAL AND METHODS"]):
                    break
                if abstract_line:
                    # Remove "Abstract:" prefix
                    abstract_line = abstract_line.replace("Abstract:", "").strip()
                    if abstract_line:
                        abstract_lines.append(abstract_line)
            abstract = " ".join(abstract_lines)
            break
    
    # Extract keywords
    keywords = ""
    for i, line in enumerate(lines):
        if line.strip().startswith("Keywords:"):
            keywords = line.replace("Keywords:", "").strip()
            break
    
    # Extract affiliations (look for numbered institutions)
    affiliations = []
    
    # Find lines that start with numbers (affiliation markers)
    affiliation_starts = []
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith(("1 ", "2 ", "3 ", "4 ", "5 ")):
            affiliation_starts.append(i)
    
    # For each affiliation start, collect consecutive lines
    for start_idx in affiliation_starts:
        affiliation_parts = []
        current_line_idx = start_idx
        
        # Extract the number from the starting line
        first_line = lines[start_idx].strip()
        affiliation_num = first_line.split()[0]
        
        # Add the starting line (without the number)
        affiliation_parts.append(first_line[len(affiliation_num):].strip())
        
        # Look for continuation lines
        current_line_idx += 1
        while current_line_idx < len(lines):
            next_line = lines[current_line_idx].strip()
            
            # Stop if we hit another numbered affiliation
            if next_line.startswith(("1 ", "2 ", "3 ", "4 ", "5 ")):
                break
                
            # Stop if empty line
            if not next_line:
                current_line_idx += 1
                continue
            
            # Add line if it looks like part of affiliation
            keywords_list = ['university', 'institute', 'hospital', 'faculty', 'romania', 'bucharest', 'medicine', 'pharmacy', 'department', 'clinic']
            if any(keyword in next_line.lower() for keyword in keywords_list) or len(next_line) < 50:
                affiliation_parts.append(next_line)
            else:
                break
                
            current_line_idx += 1
        
        # Join the parts and add to affiliations as tuple (num, content)
        if affiliation_parts:
            full_affiliation = " ".join(affiliation_parts)
            affiliations.append((affiliation_num, full_affiliation))
    
    # Extract correspondence (look for "Corresponding author:")
    correspondence_full = ""
    for i, line in enumerate(lines):
        if "corresponding author:" in line.lower():
            correspondence_parts = []
            correspondence_parts.append(line.replace("Corresponding author:", "").strip())
            
            # Check if next line has email
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if "@" in next_line:
                    correspondence_parts.append(next_line)
            
            correspondence_full = " ".join(correspondence_parts)
            break
    
    # Parse authors
    authors = _split_authors(authors_full)
    
    # Set data
    data["title"] = title
    data["authors"] = authors
    data["authors_full"] = authors_full
    data["abstract"] = abstract
    data["keywords"] = keywords
    data["doi"] = ""  # 2014 format doesn't have DOI
    data["received_date"] = received_date
    data["accepted_date"] = accepted_date
    data["revised_date"] = ""  # 2014 format doesn't have revised date
    data["academic_editor"] = ""
    data["correspondence_full"] = correspondence_full
    data["affiliations"] = affiliations
    data["citation"] = ""
    data["issue"] = issue
    data["year"] = year
    data["article_type"] = article_type
    data["correspondence_email"] = "-"
    
    return data

def scrape(url: str) -> Optional[Dict[str, Any]]:
    """Main scraping function for 2014 format"""
    try:
        print(f"📥 Downloading PDF from: {url}")
        
        # Download PDF with proper headers
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': 'application/pdf,application/octet-stream,*/*',
        }
        
        session = requests.Session()
        retry_strategy = Retry(
            total=5,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["HEAD", "GET", "OPTIONS"],
            backoff_factor=3
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        session.headers.update(headers)
        
        response = session.get(url, timeout=30)
        response.raise_for_status()
        
        # Extract text from first page
        pdf_file = io.BytesIO(response.content)
        laparams = LAParams(line_margin=0.3, word_margin=0.1, char_margin=2.0)
        text = extract_text(pdf_file, page_numbers=[0], laparams=laparams)
        
        print("🔍 Parsing 2014 format PDF...")
        data = _parse_2014_format(text)
        data["article_file"] = url
        
        print("✅ Scraping completed successfully!")
        return data
        
    except Exception as e:
        print(f"❌ Error during scraping: {e}")
        import traceback
        traceback.print_exc()
        return None

# HTML Template (same as scraper.py)
HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>RJMM 2014 Scraper</title>
<style>
body{font-family:system-ui,sans-serif;max-width:1000px;margin:2rem auto;padding:0 1rem;background:#fafafa}
h1{color:#2c3e50;border-bottom:3px solid #3498db;padding-bottom:0.5rem}
.input-group{margin:2rem 0;padding:1.5rem;background:white;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1)}
input[type="text"]{width:100%;padding:0.75rem;border:2px solid #ddd;border-radius:4px;font-size:1rem;box-sizing:border-box}
input[type="text"]:focus{outline:none;border-color:#3498db}
button{background:#3498db;color:white;border:none;padding:0.75rem 2rem;border-radius:4px;font-size:1rem;cursor:pointer;margin-top:1rem}
button:hover{background:#2980b9}
.result{background:white;padding:1.5rem;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-top:2rem}
.result label{display:block;font-weight:600;color:#2c3e50;margin-top:1rem;margin-bottom:0.25rem}
.result input,.result textarea{width:100%;padding:0.5rem;border:1px solid #ddd;border-radius:4px;font-family:monospace;font-size:0.9rem;background:#f8f9fa;box-sizing:border-box}
.result textarea{min-height:100px;resize:vertical}
.result input:focus,.result textarea:focus{outline:none;border-color:#3498db;background:white}
.author-row{display:grid;grid-template-columns:1fr auto;gap:0.5rem;margin-bottom:0.5rem}
.author-order{width:80px}
.corresponding-author{background:#fff3cd;border-color:#ffc107}
.btn-group{display:flex;gap:1rem;margin-top:1.5rem}
.btn-copy{background:#27ae60;flex:1}
.btn-copy:hover{background:#229954}
.error{background:#fee;border:2px solid #c33;color:#c33;padding:1rem;border-radius:4px;margin-top:1rem}
.format-badge{display:inline-block;background:#3498db;color:white;padding:0.25rem 0.75rem;border-radius:12px;font-size:0.85rem;margin-left:0.5rem}
</style>
</head>
<body>
<h1>📄 RJMM 2014 Scraper<span class="format-badge">2014 Only</span></h1>
<div class="input-group">
<label for="url"><strong>PDF URL:</strong></label>
<input type="text" id="url" placeholder="https://revistamedicinamilitara.ro/.../2014-...pdf" value="{{url}}">
<button onclick="scrape()">🔍 Scrape PDF</button>
</div>
{% if error %}
<div class="error">❌ {{error}}</div>
{% endif %}
{% if data %}
<div class="result">
<h2>📋 Extracted Metadata <span class="format-badge">{{data.format_detected}}</span></h2>
<label>Title</label><input readonly onclick="cp(this)" value="{{data.title}}">
<label>Issue</label><input readonly onclick="cp(this)" value="{{data.issue}}">
<label>Year</label><input readonly onclick="cp(this)" value="{{data.year}}">
<label>Article Type</label><input readonly onclick="cp(this)" value="{{data.article_type}}">
<label>Authors ({{data.authors|length}})</label>
{% for a in data.authors %}<div class="author-row">
  <input readonly onclick="cp(this)" value="{{a.name}}" {% if a.name in (data.correspondence_full or '') %}class="corresponding-author"{% endif %}>
  <input class="author-order" readonly onclick="cp(this)" value="{{a.orders}}">
</div>{% endfor %}
<label>Correspondence e‑mail</label><input readonly onclick="cp(this)" value="{{data.correspondence_email}}">
<label>Correspondence (full)</label><textarea readonly onclick="cp(this)">{{data.correspondence_full}}</textarea>
{% for aff in data.affiliations %}<label>Affiliation {{aff[0]}}</label><input readonly onclick="cp(this)" value="{{aff[1]}}">{% endfor %}
<label>DOI</label><input readonly onclick="cp(this)" value="{{data.doi}}">
<label>Abstract</label><textarea readonly onclick="cp(this)">{{data.abstract}}</textarea>
<label>Received</label><input readonly onclick="cp(this)" value="{{data.received_date}}">
<label>Revised</label><input readonly onclick="cp(this)" value="{{data.revised_date}}">
<label>Accepted</label><input readonly onclick="cp(this)" value="{{data.accepted_date}}">
<label>Academic Editor</label><input readonly onclick="cp(this)" value="{{data.academic_editor}}">
<label>Citation</label><input readonly onclick="cp(this)" value="{{data.citation}}">
<label>Keywords</label><input readonly onclick="cp(this)" value="{{data.keywords}}">
<label>Article File URL</label><input readonly onclick="cp(this)" value="{{data.article_file}}">
<div class="btn-group">
<button class="btn-copy" id="copyAll">📋 Copy All JSON</button>
<button class="btn-copy" id="copyAffiliations">📋 Copy Affiliations JSON</button>
</div>
</div>
{% endif %}
<script>
function scrape(){
const url=document.getElementById('url').value;
if(!url){alert('Please enter a PDF URL');return;}
window.location.href='/?url='+encodeURIComponent(url);
}
function cp(el){
el.select();
document.execCommand('copy');
const orig=el.style.background;
el.style.background='#d4edda';
setTimeout(()=>el.style.background=orig,300);
}
document.getElementById('copyAll').onclick=e=>{
  e.preventDefault();
  const fullData={{data|tojson}};
  localStorage.setItem('article_meta',JSON.stringify(fullData));
  navigator.clipboard.writeText(JSON.stringify(fullData))
    .then(()=>alert('Full JSON copied ✔'));
};

document.getElementById('copyAffiliations').onclick=e=>{
  e.preventDefault();
  const affiliationsOnly = {{data.affiliations|tojson}};
  navigator.clipboard.writeText(JSON.stringify(affiliationsOnly))
    .then(()=>alert('Affiliations JSON copied ✔'));
};
</script>
</body>
</html>"""

@app.route("/")
def index():
    url = request.args.get("url", "")
    if not url:
        return render_template_string(HTML_TEMPLATE, url="", data=None, error=None)
    
    data = scrape(url)
    if data:
        return render_template_string(HTML_TEMPLATE, url=url, data=data, error=None)
    else:
        return render_template_string(HTML_TEMPLATE, url=url, data=None, error="Failed to scrape PDF")

if __name__ == "__main__":
    print("🚀 Starting RJMM 2014 Scraper...")
    print("📍 Access at: http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=True)
