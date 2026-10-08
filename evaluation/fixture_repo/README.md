# notesapp

A small note-taking library and CLI, used as a benchmark fixture.

## Usage

    python -m notes.cli add "Title" --body "text" --tag work
    python -m notes.cli list --sort title
    python -m notes.cli search milk
    python -m notes.cli delete 1

## Layout

    notes/models.py      Note dataclass
    notes/storage.py     NoteStore, JSON persistence
    notes/search.py      filtering and sorting
    notes/formatting.py  table output
    notes/cli.py         argparse CLI

## Tests

    python -m pytest -q
