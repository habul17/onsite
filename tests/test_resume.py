import pytest

from onsite.resume import MAX_CHARS, ResumeError, read_resume


def make_pdf(lines):
    """A small one-page PDF with real text on it."""
    stream = "BT /F1 11 Tf 72 720 Td 14 TL " + " ".join(f"({line}) Tj T*" for line in lines) + " ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = b"%PDF-1.4\n"
    offsets = []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return out


def test_reads_text_from_a_pdf(tmp_path):
    path = tmp_path / "resume.pdf"
    path.write_bytes(make_pdf(["Jane Doe", "Led the move from MySQL to Postgres at Acme"]))
    text = read_resume(path)
    assert "Jane Doe" in text
    assert "Led the move from MySQL to Postgres at Acme" in text


def test_reads_markdown_and_tidies_whitespace(tmp_path):
    path = tmp_path / "resume.md"
    path.write_text("# Jane   Doe\n\n\n- Built   the billing service\n   \n")
    assert read_resume(path) == "# Jane Doe\n- Built the billing service"


def test_long_resumes_are_cut(tmp_path):
    path = tmp_path / "resume.txt"
    path.write_text("word " * MAX_CHARS)
    assert len(read_resume(path)) == MAX_CHARS


@pytest.mark.parametrize(
    "name, data, message",
    [
        ("resume.docx", b"x", "Use a PDF, .md or .txt"),
        ("blank.pdf", None, "may be a scanned image"),
        ("empty.md", b"  \n\n", "Found no text in empty.md."),
        ("broken.pdf", b"not a pdf at all", "Couldn't read broken.pdf"),
    ],
)
def test_explains_what_went_wrong(tmp_path, name, data, message):
    path = tmp_path / name
    path.write_bytes(make_pdf([]) if data is None else data)
    with pytest.raises(ResumeError, match=message):
        read_resume(path)


def test_missing_file(tmp_path):
    with pytest.raises(ResumeError, match="No file at"):
        read_resume(tmp_path / "nope.pdf")
