"""LinkedIn / GitHub URLs that a CV carries behind link words ("Linkedin")."""
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

import cv_parser
from agents.cv_profile import fill_profile_links


def _pdf_with_links(path):
    c = canvas.Canvas(str(path), pagesize=A4)
    c.drawString(72, 800, "Jane Doe | Linkedin | Github")
    c.linkURL("https://www.linkedin.com/in/janedoe/", (150, 795, 200, 812))
    c.linkURL("https://github.com/janedoe", (210, 795, 250, 812))
    c.linkURL("mailto:jane@example.com", (72, 795, 140, 812))
    c.save()


def test_reads_the_links_behind_the_words(tmp_path):
    path = tmp_path / "cv.pdf"
    _pdf_with_links(path)
    assert "linkedin.com" not in cv_parser.parse_cv(str(path))   # the text only has the word
    assert cv_parser.cv_links(str(path)) == ["https://www.linkedin.com/in/janedoe/",
                                             "https://github.com/janedoe"]


def test_docx_links(tmp_path):
    from docx import Document
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    doc = Document()
    doc.add_paragraph("Linkedin")
    doc.part.relate_to("https://www.linkedin.com/in/janedoe", RT.HYPERLINK, is_external=True)
    path = tmp_path / "cv.docx"
    doc.save(path)
    assert cv_parser.cv_links(str(path)) == ["https://www.linkedin.com/in/janedoe"]


def test_link_words_become_urls_and_real_urls_stay():
    links = ["https://github.com/janedoe/project", "https://github.com/janedoe",
             "https://www.linkedin.com/in/janedoe/"]
    profile = fill_profile_links({"contact": {"linkedin": "Linkedin", "github": "Github"}}, links)
    assert profile["contact"] == {"linkedin": "https://www.linkedin.com/in/janedoe/",
                                  "github": "https://github.com/janedoe"}   # the profile, not a repo

    typed = {"contact": {"linkedin": "linkedin.com/in/other", "github": ""}}
    assert fill_profile_links(typed, links)["contact"]["linkedin"] == "linkedin.com/in/other"
    assert fill_profile_links({"contact": {"linkedin": "Linkedin"}}, [])["contact"]["linkedin"] == "Linkedin"


def test_unreadable_file_has_no_links(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    assert cv_parser.cv_links(str(path)) == []
