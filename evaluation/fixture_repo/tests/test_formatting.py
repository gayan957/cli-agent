from notes.formatting import format_table, parse_date
from notes.models import Note


def test_parse_date():
    assert parse_date("2024-01-02T10:00:00") == "2024-01-02"


def test_format_table_empty():
    assert format_table([]) == "No notes found."


def test_format_table_has_header_and_rows():
    notes = [Note(id=1, title="A", body="b", created="2024-01-01T00:00:00")]
    out = format_table(notes)
    assert "TITLE" in out
    assert len(out.splitlines()) == 3
