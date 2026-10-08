import os

from notes.storage import NoteStore


def test_empty_store(tmp_path):
    store = NoteStore(os.path.join(tmp_path, "n.json"))
    assert store.all() == []
    assert store.count() == 0


def test_add_and_get(tmp_path):
    store = NoteStore(os.path.join(tmp_path, "n.json"))
    note = store.add("First", "body", ["x"])
    assert note.id == 1
    assert store.get(1).title == "First"


def test_ids_increment(tmp_path):
    store = NoteStore(os.path.join(tmp_path, "n.json"))
    store.add("A")
    second = store.add("B")
    assert second.id == 2


def test_persistence(tmp_path):
    path = os.path.join(tmp_path, "n.json")
    NoteStore(path).add("Kept")
    assert NoteStore(path).count() == 1


def test_delete(tmp_path):
    store = NoteStore(os.path.join(tmp_path, "n.json"))
    store.add("A")
    assert store.delete(1) == 1
    assert store.count() == 0
