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
    
    # Extract issue from line 1
    issue = ""
    if len(lines) > 0:
        first_line = lines[0].strip()
        issue_match = re.search(r"(Vol\.\s+[IVXLC]+.*?No\.\s*[\d\-]+/2014)", first_line, re.I)
        if issue_match:
            issue = issue_match.group(1).strip()
    
    year = "2014"
    
    # Extract article type from line 3
    article_type = ""
    if len(lines) > 2:
        type_line = lines[2].strip()
        if type_line:
            article_type = type_line
    
    # Extract dates
    received_date = ""
    accepted_date = ""
    
    for line in lines[:10]:
        if "Article received on" in line:
            date_match = re.search(r"Article received on (.+?) and accepted for publishing on (.+?)\.?$", line, re.I)
            if date_match:
                received_date = date_match.group(1).strip()
                accepted_date = date_match.group(2).strip()
            break
    
    # Extract title
    title = ""
    title_lines = []
    for i in range(6, min(12, len(lines))):
        line = lines[i].strip()
        if (line and 
            not line.startswith("http") and 
            not "received on" in line.lower() and
            not "accepted for publishing" in line.lower() and
            not re.match(r"^\s*\w+\s+\w+\s*\d", line) and
            not line.startswith("Abstract:")):
            title_lines.append(cleaned(line))
        if (re.search(r"[A-Z][a-z]+ [A-Z][a-z]+.*?\d", line) or 
            line.startswith("Abstract:")):
            break
        if len(title_lines) >= 2:
            break
    
    title = " ".join(title_lines)
    
    # Extract authors
    authors_full = ""
    for i in range(6, min(15, len(lines))):
        line = lines[i].strip()
        if re.search(r"[A-Z][a-z]+ [A-Z][a-z]+.*?\d", line) and not line.startswith("Abstract:"):
            authors_full = cleaned(line)
            if i + 1 < len(lines):
                next_line = lines[i + 1].strip()
                if next_line and not next_line.startswith("Abstract:") and re.search(r"[A-Z][a-z]+ [A-Z][a-z]+", next_line):
                    authors_full += " " + cleaned(next_line)
            break
    
    # Extract abstract
    abstract = ""
    for i, line in enumerate(lines):
        if line.strip().startswith("Abstract:"):
            abstract_lines = []
            for j in range(i, len(lines)):
                abstract_line = lines[j].strip()
                if (abstract_line.startswith("Keywords:") or 
                    abstract_line in ["INTRODUCTION", "ANATOMY OF THE PROSTATE GLAND", "MATERIAL AND METHODS"]):
                    break
                if abstract_line:
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
    
    # Extract affiliations
    affiliations = []
    affiliation_starts = []
    for i, line in enumerate(lines):
        line = line.strip()
        if line.startswith(("1 ", "2 ", "3 ", "4 ", "5 ")):
            affiliation_starts.append(i)
    
    for start_idx in affiliation_starts:
        affiliation_parts = []
        current_line_idx = start_idx
        
        first_line = lines[start_idx].strip()
        affiliation_num = first_line.split()[0]
        affiliation_parts.append(first_line[len(affiliation_num):].strip())
        
        current_line_idx += 1
        while current_line_idx < len(lines):
            next_line = lines[current_line_idx].strip()
            
            if next_line.startswith(("1 ", "2 ", "3 ", "4 ", "5 ")):
                break
            if not next_line:
                current_line_idx += 1
                continue
            
            keywords_list = ['university', 'institute', 'hospital', 'faculty', 'romania', 'bucharest', 'medicine', 'pharmacy', 'department', 'clinic']
            if any(keyword in next_line.lower() for keyword in keywords_list) or len(next_line) < 50:
                affiliation_parts.append(next_line)
            else:
                break
            current_line_idx += 1
        
        if affiliation_parts:
            full_affiliation = " ".join(affiliation_parts)
            affiliations.append((affiliation_num, full_affiliation))
    
    # Extract correspondence
    correspondence_full = ""
    for i, line in enumerate(lines):
        if "corresponding author:" in line.lower():
            correspondence_parts = []
            correspondence_parts.append(line.replace("Corresponding author:", "").strip())
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
    data["doi"] = ""
    data["received_date"] = received_date
    data["accepted_date"] = accepted_date
    data["revised_date"] = ""
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

# HTML Template (same structure as scraper.py)
HTML = """
<!DOCTYPE html>
<html>
<head><title>PDF Scraper 2014</title>
<style>
body{font-family:Arial,sans-serif;max-width:1200px;margin:0 auto;padding:20px;background-color:#f5f5f5}
form{background:white;padding:20px;border-radius:8px;box-shadow:0 2px 4px rgba(0,0,0,0.1);margin-bottom:20px}
label{display:block;margin:15px 0 5px;font-weight:bold;color:#333}
input,textarea,button{width:100%;padding:10px;border:1px solid #ddd;border-radius:4px;font-size:14px;box-sizing:border-box}
textarea{height:100px;resize:vertical;font-family:monospace}
button{background-color:#007cba;color:white;border:none;cursor:pointer;margin-top:10px}
button:hover{background-color:#005a87}
hr{margin:30px 0;border:none;border-top:2px solid #ddd}
#json{height:400px;background-color:#f8f9fa;font-family:monospace;font-size:12px}
.author-row{display:flex;gap:.5rem;align-items:center}.author-row input{flex:1}.author-order{max-width:120px}
.author-status-bullet{width:20px;height:20px;border-radius:50%;cursor:pointer;flex-shrink:0}
.author-status-bullet.status-exists{background-color:#dc3545}
.author-status-bullet.status-not-exists{background-color:#28a745}
.author-number{min-width:30px;font-weight:bold;text-align:center;color:#666}
.button-group{display:flex;gap:10px;margin-top:10px}
.button-group button{margin-top:0}
.copy-btn{background-color:#007cba;color:white;border:none;padding:5px 10px;border-radius:3px;cursor:pointer;font-size:12px;min-width:50px;height:25px}
.copy-btn:hover{background-color:#005a87}
.copy-btn:active{background-color:#004570}
.corresponding-author{background-color:#fff9c4 !important}
.author-row-corresponding{background-color:#fff9c4;border-radius:5px;padding:5px}
.format-badge{display:inline-block;padding:2px 8px;border-radius:12px;font-size:11px;font-weight:bold;margin-left:10px;background-color:#e67e22;color:white}
.version-badge{background-color:#e74c3c;color:white;padding:5px 10px;border-radius:15px;font-size:12px;font-weight:bold;margin-left:10px}
</style>
</head>
<body>
<h1>PDF Scraper 2014 <span class="version-badge">2014 ONLY</span></h1>

<form method="post">
  <label>PDF URL *</label>
  <input name="url" value="{{url or ''}}" required>
  <button type="submit">Scrape</button>
  {% if data %}
  <div class="button-group">
    <button type="button" id="copyJSON">Copy Full JSON</button>
    <button type="button" id="copyAffiliations">Copy Affiliations JSON</button>
  </div>
  {% endif %}
</form>

{% if data %}
<hr>
<label>Detected Format <span class="format-badge">{{data.format_detected.upper()}}</span></label>
<label>Title</label><input readonly onclick="cp(this)" value="{{data.title}}">
<label>Authors (full line)</label><textarea readonly onclick="cp(this)">{{data.authors_full}}</textarea>
<label>Authors (table)</label>
{% for a in data.authors %}
<div class="author-row {% if a.name in (data.correspondence_full or '') %}author-row-corresponding{% endif %}">
  <div class="author-status-bullet {% if a.exists %}status-exists{% else %}status-not-exists{% endif %}" onclick="cp(this)" title="{% if a.exists %}Author exists (RED){% else %}Author not found (GREEN){% endif %}"></div>
  <button class="copy-btn" onclick="copyAuthorJSON('{{a.name}}', '{{data.correspondence_email if a.name in (data.correspondence_full or '') else ''}}', '{{a.orders}}', {{loop.index}})">Copy</button>
  <div class="author-number">{{loop.index}}.</div>
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
<label>Keywords</label><input readonly onclick="cp(this)" value="{{data.keywords}}">
<label>Article Type</label><input readonly onclick="cp(this)" value="{{data.article_type}}">
{% if data.issue %}<label>Issue</label><input readonly onclick="cp(this)" value="{{data.issue}}">{% endif %}
{% if data.year %}<label>Year</label><input readonly onclick="cp(this)" value="{{data.year}}">{% endif %}

<h3>JSON</h3>
<textarea id="json" readonly onclick="cp(this)">{{data|tojson(indent=2)}}</textarea>
{% endif %}

<script>
function cp(el){
  const txt=el.value||el.innerText||'';navigator.clipboard.writeText(txt).then(()=>{
    el.style.outline='2px solid lime';setTimeout(()=>el.style.outline='',400);
  });
}

function copyAuthorJSON(name, email, orders, authorNumber) {
  const button = event.target;
  const authorData = {
    name: name,
    email: email,
    order: orders,
    author_number: authorNumber
  };
  const jsonString = JSON.stringify(authorData, null, 2);
  
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(jsonString).then(() => {
      showCopySuccess(button);
    }).catch(err => {
      fallbackCopyToClipboard(jsonString, button);
    });
  } else {
    fallbackCopyToClipboard(jsonString, button);
  }
}

function fallbackCopyToClipboard(text, button) {
  try {
    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.style.position = 'fixed';
    textarea.style.opacity = '0';
    document.body.appendChild(textarea);
    textarea.select();
    textarea.setSelectionRange(0, 99999);
    
    const successful = document.execCommand('copy');
    document.body.removeChild(textarea);
    
    if (successful) {
      showCopySuccess(button);
    } else {
      showCopyError(button, text, 'Fallback copy failed');
    }
  } catch (err) {
    showCopyError(button, text, 'Copy failed: ' + err.message);
  }
}

function showCopySuccess(button) {
  button.style.backgroundColor = '#28a745';
  button.textContent = 'Copied!';
  setTimeout(() => {
    button.style.backgroundColor = '#dc3545';
    button.textContent = 'Used';
  }, 1000);
}

function showCopyError(button, jsonString, message) {
  console.error('Copy failed:', message);
  button.style.backgroundColor = '#dc3545';
  button.textContent = 'Error!';
  setTimeout(() => {
    button.style.backgroundColor = '#007cba';
    button.textContent = 'Copy';
  }, 2000);
  alert('Copy failed! Here is the JSON to copy manually:\\n\\n' + jsonString);
}

{% if data %}
localStorage.setItem('article_meta', JSON.stringify({{data|tojson}}));

document.getElementById('copyJSON').onclick=e=>{
  e.preventDefault();navigator.clipboard.writeText(document.getElementById('json').value)
    .then(()=>alert('Full JSON copied ✔'));
};

document.getElementById('copyAffiliations').onclick=e=>{
  e.preventDefault();
  const affiliationsOnly = {{data.affiliations|tojson}};
  navigator.clipboard.writeText(JSON.stringify(affiliationsOnly))
    .then(()=>alert('Affiliations JSON copied ✔'));
};
{% endif %}
</script>
</body>
</html>
"""

@app.route("/", methods=["GET", "POST"])
def index():
    url = data = None
    if request.method == "POST":
        url = request.form.get("url", "").strip()
        if url:
            data = scrape(url)
    return render_template_string(HTML, url=url, data=data)

if __name__ == "__main__":
    print("🚀 Starting RJMM 2014 Scraper...")
    print("📍 Access at: http://127.0.0.1:5001")
    app.run(host="127.0.0.1", port=5001, debug=True)
