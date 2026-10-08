"""Loading and saving notes to a JSON file."""

import json
import os

from notes.models import Note


class NoteStore:
    def __init__(self, path):
        self.path = path
        self._notes = []
        self._loaded = False

    def load(self):
        if not os.path.exists(self.path):
            self._notes = []
            self._loaded = True
            return self._notes
        with open(self.path, encoding="utf-8") as handle:
            raw = json.load(handle)
        self._notes = [Note.from_dict(item) for item in raw]
        self._loaded = True
        return self._notes

    def save(self):
        parent = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(parent, exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump([n.to_dict() for n in self._notes], handle, indent=2)

    def all(self):
        if not self._loaded:
            self.load()
        return list(self._notes)

    def add(self, title, body="", tags=None):
        notes = self.all()
        new_id = max([n.id for n in notes], default=0) + 1
        note = Note(id=new_id, title=title, body=body, tags=tags or [])
        self._notes.append(note)
        self.save()
        return note

    def get(self, note_id):
        for note in self.all():
            if note.id == note_id:
                return note
        return None

    def delete(self, note_id):
        notes = self.all()
        remaining = [n for n in notes if n.id != note_id]
        removed = len(notes) - len(remaining)
        self._notes = remaining
        self.save()
        return removed

    def count(self):
        return len(self.all())
