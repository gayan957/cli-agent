"""Turning notes into text for display."""


def parse_date(value):
    """Return the date part of an ISO timestamp."""
    return value.split("T")[0]


def format_row(note, width=40):
    date = parse_date(note.created)
    tags = ",".join(note.tags)
    return f"{note.id:>3}  {date}  {note.title[:24]:<24}  {tags:<16}  {note.summary(width)}"


def format_table(notes, width=40):
    if not notes:
        return "No notes found."
    header = f"{'ID':>3}  {'DATE':<10}  {'TITLE':<24}  {'TAGS':<16}  BODY"
    lines = [header, "-" * len(header)]
    for note in notes:
        lines.append(format_row(note, width))
    return "\n".join(lines)
