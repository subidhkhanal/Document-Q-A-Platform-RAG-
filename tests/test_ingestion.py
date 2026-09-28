import pytest

from backend.ingestion.parsers import UnsupportedDocument, detect_type, parse_text, table_to_markdown
from backend.ingestion.pipeline import build_chunks


def test_detect_type_checks_magic_bytes():
    assert detect_type("a.pdf", b"%PDF-1.7 ...")[0] == "pdf"
    assert detect_type("a.docx", b"PK\x03\x04...")[0] == "docx"
    assert detect_type("notes.MD", b"# Title")[0] == "markdown"
    with pytest.raises(UnsupportedDocument):
        detect_type("a.pdf", b"not a pdf")
    with pytest.raises(UnsupportedDocument):
        detect_type("a.exe", b"MZ")
    with pytest.raises(UnsupportedDocument):
        detect_type("a.txt", b"bin\x00ary")


def test_table_to_markdown_preserves_structure():
    md = table_to_markdown([["Plan", "Days"], ["Basic", "20"], ["Senior | Lead", None]])
    assert md.splitlines() == [
        "| Plan | Days |",
        "| --- | --- |",
        "| Basic | 20 |",
        "| Senior \\| Lead |  |",
    ]


def test_markdown_sections_follow_headings(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text("# Handbook\nIntro\n## PTO\n20 days\n```\n# not a heading\n```\n", encoding="utf-8")
    sections = parse_text(str(path), markdown=True)["sections"]
    assert [s["section_title"] for s in sections] == ["Handbook", "PTO"]
    assert sections[1]["heading_hierarchy"] == ["Handbook", "PTO"]
    assert "# not a heading" in sections[1]["text"]


def test_text_decoding_falls_back_with_warning(tmp_path):
    path = tmp_path / "latin.txt"
    path.write_bytes("caf\xe9".encode("cp1252"))
    result = parse_text(str(path), markdown=False)
    assert result["sections"][0]["text"] == "café"
    assert result["warnings"]


def test_docx_headings_and_tables(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_heading("Benefits", level=1)
    document.add_paragraph("Overview text.")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text, table.cell(0, 1).text = "Plan", "Days"
    table.cell(1, 0).text, table.cell(1, 1).text = "Basic", "20"
    path = tmp_path / "b.docx"
    document.save(path)

    from backend.ingestion.parsers import parse_docx

    sections = parse_docx(str(path))["sections"]
    assert sections[0]["section_title"] == "Benefits"
    assert "| Plan | Days |" in sections[0]["text"]


class _SplitEverySentence:
    def chunk_documents(self, docs):
        out = []
        for d in docs:
            for part in d["text"].split(". "):
                out.append({**d, "text": part})
        return out


def test_build_chunks_numbers_globally_with_provenance():
    sections = [
        {"text": "One. Two", "page": 1, "section_title": "A"},
        {"text": "Three", "page": 2, "section_title": "B"},
        {"text": "   ", "page": 3},
    ]
    chunks = build_chunks(sections, _SplitEverySentence(), tenant_id=4, document_id=9, version=2,
                          embedding_model_version="m@v1")
    assert [c["chunk_index"] for c in chunks] == [0, 1, 2]
    assert [c["chunk_id"].rsplit("_", 1)[0] for c in chunks] == ["chk_9_2_0", "chk_9_2_1", "chk_9_2_2"]
    again = build_chunks(sections, _SplitEverySentence(), tenant_id=4, document_id=9, version=2,
                         embedding_model_version="m@v1")
    assert [c["chunk_id"] for c in again] == [c["chunk_id"] for c in chunks]  # deterministic
    assert [c["page_number"] for c in chunks] == [1, 1, 2]
    assert chunks[2]["section_title"] == "B"
    assert all(c["content_hash"].startswith("sha256:") for c in chunks)
    assert {c["embedding_model_version"] for c in chunks} == {"m@v1"}
