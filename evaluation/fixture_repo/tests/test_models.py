from notes.models import Note


def test_note_defaults():
    note = Note(id=1, title="Hello")
    assert note.body == ""
    assert note.tags == []
    assert "T" in note.created


def test_tags_string_is_wrapped():
    note = Note(id=1, title="X", tags="work")
    assert note.tags == ["work"]


def test_has_tag_is_case_insensitive():
    note = Note(id=1, title="X", tags=["Work"])
    assert note.has_tag("work")
    assert not note.has_tag("home")


def test_summary_truncates():
    note = Note(id=1, title="X", body="a" * 100)
    assert len(note.summary(40)) == 40
    assert note.summary(40).endswith("...")


def test_roundtrip():
    note = Note(id=7, title="T", body="B", tags=["a"])
    assert Note.from_dict(note.to_dict()) == note
