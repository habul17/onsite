import pytest

from onsite import bank
from onsite.models import Level, Track
from onsite.resume import MAX_CHARS, ResumeError, ResumeQuestion, ResumeQuestions, questions_from_resume, read_resume


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


RESUME = """Jane Doe, Senior Software Engineer
Acme Payments, 2021 to now
Led the migration of the ledger from MySQL to Postgres with zero downtime
Built a fraud scoring service handling 4,000 requests a second
Mentored three engineers through their first on-call rotation"""


def resume_question(quote, title="Ledger migration", signals=("Says what they did themselves",)):
    return ResumeQuestion(
        resume_quote=quote,
        title=title,
        prompt=f"You wrote '{quote}'. Tell me about the hardest call you made there.",
        strong_signals=list(signals),
        follow_ups=["What would you do differently?"],
    )


def test_questions_point_at_real_resume_lines(fake_llm):
    reply = ResumeQuestions(
        questions=[
            resume_question("Led the migration of the ledger from MySQL to Postgres"),
            resume_question("Rewrote the search engine in Rust", title="Made up"),
            resume_question("built a fraud scoring service, handling 4000 requests a second", title="Fraud"),
        ]
    )
    fake = fake_llm(reply)
    questions = questions_from_resume(fake, bank.load(Track.BEHAVIORAL), Level.SENIOR, RESUME, 3)

    assert [q.title for q in questions] == ["Ledger migration", "Fraud"]
    assert [q.id for q in questions] == ["resume-1", "resume-2"]
    assert questions[0].based_on == "Led the migration of the ledger from MySQL to Postgres"
    assert questions[0].track == Track.BEHAVIORAL and questions[0].levels == [Level.SENIOR]

    call = fake.calls[0]
    assert RESUME in call["prompt"] and "<resume>" in call["prompt"]
    assert "Write 3 questions." in call["system"]
    assert bank.load(Track.BEHAVIORAL).level_bar[Level.SENIOR] in call["system"]


def test_the_same_resume_line_is_only_used_once(fake_llm):
    quote = "Mentored three engineers through their first on-call rotation"
    again = "  mentored three ENGINEERS through their first on-call rotation"
    reply = ResumeQuestions(questions=[resume_question(quote), resume_question(again)])
    questions = questions_from_resume(fake_llm(reply), bank.load(Track.BEHAVIORAL), Level.MID, RESUME, 2)
    assert len(questions) == 1


def test_questions_need_signals_and_are_capped_at_the_count(fake_llm):
    reply = ResumeQuestions(
        questions=[
            resume_question("Led the migration of the ledger", signals=()),
            resume_question("Built a fraud scoring service"),
            resume_question("Mentored three engineers"),
        ]
    )
    questions = questions_from_resume(fake_llm(reply), bank.load(Track.SYSTEM_DESIGN), Level.SENIOR, RESUME, 1)
    assert [q.based_on for q in questions] == ["Built a fraud scoring service"]


def test_coding_rounds_do_not_use_the_resume(fake_llm):
    with pytest.raises(ValueError):
        questions_from_resume(fake_llm(), bank.load(Track.CODING), Level.MID, RESUME, 1)


def test_asking_for_no_questions_skips_the_model(fake_llm):
    fake = fake_llm()
    assert questions_from_resume(fake, bank.load(Track.BEHAVIORAL), Level.MID, RESUME, 0) == []
    assert fake.calls == []
