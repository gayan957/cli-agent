"""Command line entry point."""

import os
import sys

import llm
import tools
from agent import run
from ui import console


def show(stats):
    fail_style = "red" if stats["tool_failures"] else "green"
    console.print(
        f"\n  [dim]steps=[/dim][cyan]{stats['steps']}[/cyan]"
        f"  [dim]tool_calls=[/dim][cyan]{stats['tool_calls']}[/cyan]"
        f"  [dim]failures=[/dim][{fail_style}]{stats['tool_failures']}[/{fail_style}]"
        f"  [dim]time=[/dim][cyan]{stats['seconds']}s[/cyan]"
    )


def pick_workspace():
    """Let the user point the agent at an existing codebase instead of the
    default sandbox. Everything the agent reads, writes, or runs stays
    confined to whatever directory this ends up as (see tools._safe) — so
    picking a path here is what actually lets you 'build in your own repo'
    rather than the throwaway default sandbox."""
    console.print(f"[bold]Workspace:[/bold] [cyan]{tools.SANDBOX}[/cyan] [dim](current)[/dim]")
    path = console.input(
        "  [bold]Path to build in — Enter to keep current:[/bold] "
    ).strip()
    if not path:
        return

    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        console.print(f"[red]'{path}' is a file, not a directory — keeping {tools.SANDBOX}[/red]")
        return

    if not os.path.isdir(path):
        answer = console.input(f"  '{path}' doesn't exist — create it? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            console.print(f"[yellow]Keeping {tools.SANDBOX}[/yellow]")
            return

    tools.set_sandbox(path)
    if not os.path.isdir(os.path.join(tools.SANDBOX, ".git")):
        console.print("[yellow]Note: this isn't a git repo, so there's no "
                      "auto-checkpoint safety net — changes here can't be "
                      "undone with `git log` / `git reset` like the default "
                      "sandbox.[/yellow]")
    console.print(f"[green]Workspace set to {tools.SANDBOX}[/green]")


def pick_model():
    """List locally registered Ollama models (local + already-pulled cloud
    ones) and let the user choose one, or pull a new Ollama Cloud model by
    name. Falls back to the VA_MODEL default if Ollama is unreachable, so
    this never blocks a working setup."""
    try:
        models = llm.list_models()
    except Exception as error:
        console.print(f"[yellow]Could not reach Ollama ({error}) — "
                      f"using default model {llm.MODEL}[/yellow]")
        return

    console.print("[bold]Available models:[/bold]")
    for index, name in enumerate(models, 1):
        tag = " [magenta](cloud)[/magenta]" if llm.is_cloud_model(name) else ""
        marker = " [dim](default)[/dim]" if name.split(":")[0] == llm.MODEL else ""
        console.print(f"  [cyan]{index}[/cyan]. {name}{tag}{marker}")
    console.print("  [cyan]c[/cyan]. Pull an Ollama Cloud model by name")

    choice = console.input(
        f"  [bold]Select [1-{len(models)}, c, Enter for default]:[/bold] "
    ).strip()
    if not choice:
        return
    if choice.lower() == "c":
        pick_cloud_model()
        return
    try:
        llm.set_model(models[int(choice) - 1])
    except (ValueError, IndexError):
        console.print(f"[yellow]Invalid choice — using default {llm.MODEL}[/yellow]")
        return
    console.print(f"[green]Using {llm.MODEL}[/green]")


def pick_cloud_model():
    """Pull (register) an Ollama Cloud model by name and switch to it.
    Requires `ollama signin` to have been run already."""
    console.print("[bold]Known Ollama Cloud models[/bold] [dim](or type any other -cloud name):[/dim]")
    for name in llm.CLOUD_MODELS:
        console.print(f"    {name}")

    name = console.input("  [bold]Cloud model name:[/bold] ").strip()
    if not name:
        console.print(f"[yellow]No model entered — using default {llm.MODEL}[/yellow]")
        return

    try:
        with console.status(f"[cyan]pulling {name}...[/cyan]", spinner="dots"):
            llm.pull_model(name)
    except Exception as error:
        console.print(f"[red]Could not pull '{name}': {error}[/red]")
        console.print("[yellow]If this is a cloud model, make sure you're "
                      "signed in first: ollama signin[/yellow]")
        return

    llm.set_model(name)
    console.print(f"[green]Using {llm.MODEL}[/green]")


def main():
    console.print("[bold magenta]va-agent[/bold magenta]")
    pick_workspace()
    pick_model()
    if len(sys.argv) > 1:
        show(run(" ".join(sys.argv[1:])))
        return
    while True:
        try:
            task = console.input("\n[bold green]va>[/bold green] ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            return
        if task in {"exit", "quit"}:
            return
        if task:
            show(run(task))


if __name__ == "__main__":
    main()
