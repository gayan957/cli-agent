"""A small note-taking library used as a benchmark fixture."""

from notes.models import Note
from notes.storage import NoteStore

__all__ = ["Note", "NoteStore"]
__version__ = "0.1.0"
