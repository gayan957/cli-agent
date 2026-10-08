"""Data model for a single note."""

import datetime as _dt
from dataclasses import dataclass, field, asdict


@dataclass
class Note:
    """A single note with a title, body and tags."""

    id: int
    title: str
    body: str = ""
    tags: list = field(default_factory=list)
    created: str = ""

    def __post_init__(self):
        if not self.created:
            self.created = _dt.datetime.now().isoformat(timespec="seconds")
        if isinstance(self.tags, str):
            self.tags = [self.tags]

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(
            id=data["id"],
            title=data["title"],
            body=data.get("body", ""),
            tags=list(data.get("tags", [])),
            created=data.get("created", ""),
        )

    def has_tag(self, tag):
        return tag.lower() in [t.lower() for t in self.tags]

    def summary(self, width=40):
        text = self.body.replace("\n", " ").strip()
        if len(text) <= width:
            return text
        return text[: width - 3] + "..."
