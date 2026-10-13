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


def banner(sandbox, model):
    console.print(f"[bold cyan]va-agent[/bold cyan] [dim]|[/dim] "
                  f"model: {model} [dim]|[/dim] sandbox: {sandbox}")


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
    