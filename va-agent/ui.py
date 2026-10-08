"""Shared rich console + presentation helpers for the CLI.

Kept separate from cli.py/agent.py so both can print in a consistent,
friendly style without duplicating formatting logic.
"""

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()

# One icon per tool so the step log reads at a glance instead of as a wall
# of identical dim text.
TOOL_ICONS = {
    "list_files": "📁",
    "read_file": "📄",
    "write_file": "📝",
    "grep": "🔎",
    "run_command": "⚙️ ",
}


def describe_call(name, arguments):
    """One-line summary of a tool call for the terminal — never dumps a
    file's actual content (e.g. write_file's HTML/CSS/code) into the CLI."""
    if name == "write_file":
        path = arguments.get("path", "?")
        length = len(arguments.get("content", ""))
        return f"{path}  ({length} chars)"
    return str(arguments)[:160]


def banner():
    """Friendly welcome panel shown once at startup."""
    body = (
        "[bold]va-agent[/bold] [dim]— a local, tool-using coding assistant[/dim]\n\n"
        "It reads, writes, greps and runs commands inside a sandboxed folder,\n"
        "asking before anything that changes files or your system.\n\n"
        "[dim]Type a task in plain English, or one of:[/dim] "
        "[cyan]help[/cyan] · [cyan]workspace[/cyan] · [cyan]model[/cyan] · [cyan]exit[/cyan]"
    )
    console.print(Panel(body, title="[bold magenta]🤖 va-agent[/bold magenta]",
                         border_style="magenta", padding=(1, 2)))


def help_table():
    table = Table(title="Commands", show_header=True, header_style="bold cyan",
                  border_style="dim")
    table.add_column("Command")
    table.add_column("What it does")
    table.add_row("<any text>", "Give the agent a task to work on")
    table.add_row("workspace", "Switch which folder the agent works in")
    table.add_row("model", "Switch the Ollama model in use")
    table.add_row("help", "Show this list again")
    table.add_row("exit / quit", "Leave va-agent")
    console.print(table)


def rule(title=None, style="grey30"):
    console.rule(title, style=style)


def prompt_task():
    return console.input("\n[bold green]➜ va>[/bold green] ").strip()


def announce_task(task):
    rule(f"[bold]{task}[/bold]")


def tool_call(step, name, arguments):
    icon = TOOL_ICONS.get(name, "🔧")
    console.print(f"  [dim][{step}][/dim] {icon} [cyan]{name}[/cyan]"
                  f"([yellow]{describe_call(name, arguments)}[/yellow])")


def tool_denied():
    console.print("  [yellow]✋ denied[/yellow]")


def tool_error(message):
    console.print(f"  [red]✗ {message}[/red]")


def unknown_tool(name):
    console.print(f"  [red]✗ no tool named '{name}'[/red]")


def empty_reply():
    console.print("  [yellow]…empty reply — nudging model to act[/yellow]")


def llm_error(error):
    console.print(f"  [bold red]✗ LLM error:[/bold red] {error}")


def step_limit(limit):
    console.print(f"[bold yellow]⏱ stopped: hit the {limit}-step limit[/bold yellow]")


def final_reply(text):
    console.print(Panel(text, title="[bold green]✓ done[/bold green]",
                         border_style="green", padding=(1, 2)))


def summary(stats):
    ok = stats["tool_failures"] == 0
    fail_style = "green" if ok else "red"
    fail_icon = "✓" if ok else "✗"
    # Colour by `finished` too — a run that hit the step limit with no tool
    # failures is still unfinished and must not read as a green success.
    colour = "green" if stats.get("finished") and ok else "yellow"
    console.print(
        f"  [dim]steps[/dim] [cyan]{stats['steps']}[/cyan]"
        f"   [dim]tool calls[/dim] [cyan]{stats['tool_calls']}[/cyan]"
        f"   [dim]failures[/dim] [{fail_style}]{fail_icon} {stats['tool_failures']}[/{fail_style}]"
        f"   [dim]time[/dim] [cyan]{stats['seconds']}s[/cyan]"
        f"   [{colour}]{'finished' if stats.get('finished') else 'unfinished'}[/{colour}]"
    )
