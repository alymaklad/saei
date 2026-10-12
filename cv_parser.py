"""Extract plain text from a CV, whether it's a PDF or a Word doc."""
import os


def parse_cv(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return _parse_pdf(path)
    if ext in (".docx", ".dotx"):
        return _parse_docx(path)
    raise ValueError(f"Unsupported CV format: {ext} (use .pdf or .docx)")


def _parse_pdf(path: str) -> str:
    import pdfplumber
    text_parts = []
    with pdfplumber.open(path) as pdf:
        for page in pdf.pages:
            text_parts.append(page.extract_text() or "")
    return "\n".join(text_parts)


def _parse_docx(path: str) -> str:
    from docx import Document
    doc = Document(path)
    return "\n".join(p.text for p in doc.paragraphs)


def cv_links(path: str) -> list[str]:
    """The web links a CV carries as clickable links -- e.g. the word
    "Linkedin" pointing at linkedin.com/in/you. parse_cv() reads only the
    visible words, so these URLs never reach the text. In document order,
    without duplicates; mailto:/tel: links are left out."""
    ext = os.path.splitext(path)[1].lower()
    urls = []
    try:
        if ext == ".pdf":
            import pdfplumber
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    urls += [h.get("uri") or "" for h in page.hyperlinks]
        elif ext in (".docx", ".dotx"):
            from docx import Document
            from docx.opc.constants import RELATIONSHIP_TYPE as RT
            rels = Document(path).part.rels.values()
            urls += [r.target_ref for r in rels if r.reltype == RT.HYPERLINK and r.is_external]
    except Exception:  # noqa: BLE001 -- links are a bonus; the text still parses
        return []
    seen = []
    for url in urls:
        url = (url or "").strip()
        if url.lower().startswith(("http://", "https://", "www.")) and url not in seen:
            seen.append(url)
    return seen


def find_default_cv(directory: str = "cv") -> str | None:
    """
    Looks for cv/current_cv.pdf or cv/current_cv.docx -- whichever was most
    recently uploaded via the CV page (or placed there by hand). Returns None
    if neither exists.
    """
    for ext in (".pdf", ".docx"):
        candidate = os.path.join(directory, f"current_cv{ext}")
        if os.path.exists(candidate):
            return candidate
    return None
