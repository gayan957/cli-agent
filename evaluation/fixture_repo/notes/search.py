"""Searching and filtering notes."""


def by_text(notes, query):
    """Return notes whose title or body contains query (case-insensitive)."""
    q = query.lower()
    return [n for n in notes if q in n.title.lower() or q in n.body.lower()]


def by_tag(notes, tag):
    """Return notes carrying the given tag."""
    return [n for n in notes if n.has_tag(tag)]


def sort_notes(notes, key="created", reverse=False):
    """Sort notes by 'created', 'title' or 'id'."""
    if key == "title":
        return sorted(notes, key=lambda n: n.title.lower(), reverse=reverse)
    if key == "id":
        return sorted(notes, key=lambda n: n.id, reverse=reverse)
    return sorted(notes, key=lambda n: n.created, reverse=reverse)


def all_tags(notes):
    """Return every distinct tag across the given notes, sorted."""
    seen = set()
    for note in notes:
        for tag in note.tags:
            seen.add(tag.lower())
    return sorted(seen)
