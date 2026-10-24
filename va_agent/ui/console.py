"""Terminal output. All user-facing printing goes through here."""

try:
    from rich.console import Console
    console = Console()
    _RICH = True
except ImportError:                                    # pragma: no cover
    class _Plain:
        def print(self, *args, **kwargs):
            text = " ".join(str(a) for a in args)
            for tag in ("[dim]", "[/dim]", "[bold]", "[/bold]", "[green]",
                        "[/green]", "[yellow]", "[/yellow]", "[red]", "[/red]",
                        "[cyan]", "[/cyan]"):
                text = text.replace(tag, "")
            print(text)

    console = _Plain()
    _RICH = False


def describe_call(name, arguments, step=None):
    """One compact line describing a tool call."""
    shown = str(arguments)
    if len(shown) > 120:
        shown = shown[:120] + "..."
    prefix = f"[{step}] " if step is not None else ""
    return f"[dim]  {prefix}{name}({shown})[/dim]"


def banner(sandbox, model, mode="default", branch=None):
    """One line: model, mode, working directory, and the worktree branch
    when not in the main worktree."""
    where = f" [dim]|[/dim] worktree: {branch}" if branch else ""
    console.print(f"[bold cyan]va-agent[/bold cyan] [dim]|[/dim] "
                  f"model: {model} [dim]|[/dim] mode: {mode} [dim]|[/dim] "
                  f"cwd: {sandbox}{where}")


def summary(stats):
    """Print the per-run stats line, coloured by outcome."""
    if not stats.get("finished"):
        colour = "red"
    elif stats.get("tool_failures"):
        colour = "yellow"
    else:
        colour = "green"

    console.print(
        f"[{colour}]  steps={stats['steps']}  "
        f"tool_calls={stats['tool_calls']}  "
        f"failures={stats['tool_failures']}  "
        f"time={stats['seconds']}s  "
        f"finished={stats['finished']}[/{colour}]"
    )
    

TODO_MARKS = {"completed": "☒", "pending": "☐", "in_progress": "▶"}


def todo_lines(todos):
    """Plain-text todo list: ☒ completed, ▶ in progress, ☐ pending."""
    return "\n".join(f"  {TODO_MARKS.get(t['status'], '☐')} {t['content']}"
                     for t in todos) or "  (no todos)"


def render_todos(todos):
    """Show the todo list in the terminal."""
    console.print("[bold]  Todos[/bold]")
    styles = {"completed": "dim", "in_progress": "cyan"}
    for todo in todos:
        mark = TODO_MARKS.get(todo["status"], "☐")
        text = f"  {mark} {todo['content']}"
        if _RICH:
            from rich.markup import escape
            style = styles.get(todo["status"])
            text = f"[{style}]{escape(text)}[/{style}]" if style else escape(text)
        console.print(text)
