from notes.models import Note
from notes.search import all_tags, by_tag, by_text, sort_notes

NOTES = [
    Note(id=1, title="Shopping", body="milk and bread", tags=["home"], created="2024-01-02T10:00:00"),
    Note(id=2, title="Standup", body="talk about MILK supply", tags=["Work"], created="2024-01-01T09:00:00"),
    Note(id=3, title="Ideas", body="", tags=["work", "misc"], created="2024-01-03T08:00:00"),
]


def test_by_text_matches_title_and_body():
    assert {n.id for n in by_text(NOTES, "milk")} == {1, 2}


def test_by_tag_is_case_insensitive():
    assert {n.id for n in by_tag(NOTES, "work")} == {2, 3}


def test_sort_by_title():
    assert [n.id for n in sort_notes(NOTES, "title")] == [3, 1, 2]


def test_sort_by_created():
    assert [n.id for n in sort_notes(NOTES, "created")] == [2, 1, 3]


def test_all_tags():
    assert all_tags(NOTES) == ["home", "misc", "work"]
