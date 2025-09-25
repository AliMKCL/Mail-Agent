import re
from bs4 import BeautifulSoup

FOOTER_MARKERS = [
    r'to unsubscribe', r'click here to unsubscribe', r'unsubscribe from this list',
    r'privacy\s+notice', r'privacy\s+policy',
    r'do\s+not\s+reply\s+to\s+this', r'please\s+do\s+not\s+reply\s+to\s+this',
    r'confidentiality\s+notice', r'view\s+in\s+browser',
    r'follow\s+us\s+on\s+(facebook|twitter|linkedin)', r'manage\s+your\s+preferences',
    r'careers@southampton\.ac\.uk', r'\+44\(0\)23\s*8059',
    r'highfield\s+campus', r'city\s+centre\s+campus',
]

SIGNATURE_MARKERS = [
    r'^--\s*$', r'^—\s*$', r'^___+$', r'^cheers,?$', r'^regards,?$', r'^best( wishes)?,?$',
]

ADDRESS_OR_META_LINE = re.compile(
    r'(?i)(^[A-Z][A-Za-z .,&-]+(?:\s\|\s|\s{2,}))+[A-Za-z0-9 .,&-]+$'  # lines like "Dept | Address | Phone"
)

CONTACT_LINE = re.compile(
    r'(?i)(tel|phone|mobile|fax|email|mail|web|www|http|https|@|\.com|\.ac\.)'
)

def has_important_content(text: str) -> bool:
    """Check if text contains important information that shouldn't be truncated"""
    important_patterns = [
        r'\b(deadline|due\s+date|closing\s+date|expires?|end\s+date)\b',  # Date-related keywords
        r'\b\d{1,2}[-/]\w{3}[-/]\d{4}\b',  # Date formats like 12-Oct-2025
        r'\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b',  # Date formats like 12/10/2025
        r'\b(january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{1,2}',  # Month day
        r'\b(meeting|appointment|event|conference|seminar|interview|exam|test|assignment|project)\b',  # Event keywords
        r'\b(job|position|internship|opportunity|application|vacancy)\b',  # Job-related keywords
        r'\b(apply|submit|register|enroll|book)\s+(by|before|until)\b',  # Action + deadline words
    ]
    
    for pattern in important_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False

def html_to_text(html: str) -> str:
    if not html or not html.strip():
        return ""
    
    try:
        # Try lxml first, fallback to html.parser if not available
        soup = BeautifulSoup(html, "lxml")
    except:
        soup = BeautifulSoup(html, "html.parser")
    
    # Remove style, script, and noscript tags
    for tag in soup(["style", "script", "noscript"]):
        tag.decompose()
    
    # Get reasonably structured text
    text = soup.get_text(separator="\n")
    return text

def truncate_at_markers(text: str, markers) -> str:
    # Find earliest occurrence of any marker and truncate the body at that point
    # But be careful not to truncate if there's important content after the marker
    earliest = None
    earliest_marker = None
    
    for pattern in markers:
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            idx = m.start()
            if earliest is None or idx < earliest:
                earliest = idx
                earliest_marker = pattern
    
    if earliest is not None:
        # Check if there's important content after the marker
        content_after = text[earliest:earliest + 500]  # Check next 500 chars
        if has_important_content(content_after):
            # Don't truncate if there's important content after the marker
            return text
        else:
            return text[:earliest].rstrip()
    
    return text

def drop_signature_block(lines):
    kept = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        
        # Only break on signature markers if we're past a reasonable amount of content (20+ lines)
        # and the line is short (likely a signature, not content)
        if (len(kept) > 20 and len(stripped) < 50 and 
            any(re.search(pat, line, flags=re.IGNORECASE) for pat in SIGNATURE_MARKERS)):
            break
            
        # Only break on address/meta lines if we're well into the email and it looks like contact info
        if (len(kept) > 15 and len(stripped) < 100 and 
            ADDRESS_OR_META_LINE.search(stripped)):
            break
            
        # Only drop contact lines if they're very short, near the end, and don't contain dates
        if (len(stripped) < 50 and CONTACT_LINE.search(line) and len(kept) > 20 and
            not re.search(r'\b\d{1,2}[-/]\w{3}[-/]\d{4}\b|\b\d{1,2}[-/]\d{1,2}[-/]\d{4}\b|deadline|due|closing', line, re.IGNORECASE)):
            break
            
        kept.append(line)
    return kept

def collapse_blank_lines(text: str) -> str:
    text = re.sub(r'[ \t]+', ' ', text)                   # collapse runs of spaces
    text = re.sub(r'\n{3,}', '\n\n', text)                # max two consecutive newlines
    return text.strip()

def clean_email(body_text: str, body_html: str | None = None) -> str:
    try:
        # Handle empty inputs
        if not body_text and not body_html:
            return ""
        
        text = ""
        
        # Check if body_text contains HTML tags - if so, treat it as HTML
        if body_text and ('<html' in body_text.lower() or '<div' in body_text.lower() or '<table' in body_text.lower()):
            # body_text contains HTML, so parse it
            text = html_to_text(body_text)
        elif body_html and body_html.strip():
            # Use dedicated HTML version if available
            text = html_to_text(body_html)
        else:
            # Use body_text as plain text
            text = body_text or ""

        # Normalize line endings
        text = text.replace('\r\n', '\n').replace('\r', '\n')

        # Remove any remaining HTML/CSS artifacts line by line
        lines = [ln.rstrip() for ln in text.split('\n')]
        
        cleaned_lines = []
        for line in lines:
            stripped = line.strip()
            
            # Skip empty lines
            if not stripped:
                cleaned_lines.append('')
                continue
                
            # Skip lines that are clearly HTML/CSS artifacts
            if (stripped.startswith(('<!', '</', '<html', '<head', '<body', '<style', '<script', '<meta', '<title', '<link')) or
                stripped.startswith(('{', '}', '/*', '*/', '<!--', '-->')) or
                stripped.endswith(('{', '}', '<!--', '-->')) or
                re.match(r'^<[^>]+>$', stripped) or  # Single HTML tags
                re.match(r'^[{}/*\s\-]*$', stripped) or  # Lines with only CSS/comment characters
                (stripped.startswith('*') and len(stripped) < 5)):  # Short CSS comment fragments
                continue
                
            # Remove any remaining HTML tags from the line content
            line_cleaned = re.sub(r'<[^>]+>', '', line)
            
            # Skip lines that become empty after HTML tag removal
            if not line_cleaned.strip():
                continue
                
            cleaned_lines.append(line_cleaned)

        # Join back and collapse excessive blank lines
        cleaned = '\n'.join(cleaned_lines)
        cleaned = collapse_blank_lines(cleaned)

        return cleaned
        
    except Exception as e:
        # If cleaning fails, return the original body_text as fallback
        print(f"Warning: Email cleaning failed: {e}")
        return body_text or ""
