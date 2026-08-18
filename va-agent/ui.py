"""Shared rich console for coloured CLI output."""

from rich.console import Console

console = Console()


def describe_call(name, arguments):
    """One-line summary of a tool call for the terminal — never dumps a
    file's actual content (e.g. write_file's HTML/CSS/code) into the CLI."""
    if name == "write_file":
        path = arguments.get("path", "?")
        length = len(arguments.get("content", ""))
        return f"{path}  ({length} chars)"
    return str(arguments)[:160]
