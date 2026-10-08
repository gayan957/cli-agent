"""Command line interface for the notes library."""

import argparse
import sys

from notes.formatting import format_table
from notes.search import by_tag, by_text, sort_notes
from notes.storage import NoteStore

DEFAULT_PATH = "notes.json"


def build_parser():
    parser = argparse.ArgumentParser(prog="notes")
    parser.add_argument("--file", default=DEFAULT_PATH)
    sub = parser.add_subparsers(dest="command")

    add = sub.add_parser("add")
    add.add_argument("title")
    add.add_argument("--body", default="")
    add.add_argument("--tag", action="append", default=[])

    listing = sub.add_parser("list")
    listing.add_argument("--sort", default="created")
    listing.add_argument("--tag")

    search = sub.add_parser("search")
    search.add_argument("query")

    remove = sub.add_parser("delete")
    remove.add_argument("id", type=int)

    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    store = NoteStore(args.file)

    if args.command == "add":
        note = store.add(args.title, args.body, args.tag)
        print(f"Added note {note.id}")
        return 0

    if args.command == "list":
        notes = store.all()
        if args.tag:
            notes = by_tag(notes, args.tag)
        print(format_table(sort_notes(notes, args.sort)))
        return 0

    if args.command == "search":
        print(format_table(by_text(store.all(), args.query)))
        return 0

    if args.command == "delete":
        removed = store.delete(args.id)
        print(f"Deleted {removed} note(s)")
        return 0

    build_parser().print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
