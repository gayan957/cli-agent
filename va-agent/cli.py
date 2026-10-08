"""Command line entry point."""

import os
import sys

# --print: non-interactive one-shot mode (CI). Must run before `import agent`,
# because safety.py reads VA_AUTO_APPROVE once at import time.
PRINT_MODE = "--print" in sys.argv
if PRINT_MODE:
    os.environ["VA_AUTO_APPROVE"] = "1"
    sys.argv.remove("--print")

import llm
import tools
import ui
from agent import run
from ui import console


def pick_workspace():
    """Let the user point the agent at an existing codebase instead of the
    default sandbox. Everything the agent reads, writes, or runs stays
    confined to whatever directory this ends up as (see tools._safe) — so
    picking a path here is what actually lets you 'build in your own repo'
    rather than the throwaway default sandbox."""
    console.print(f"[bold]📂 Workspace:[/bold] [cyan]{tools.SANDBOX}[/cyan] [dim](current)[/dim]")
    path = console.input(
        "  [bold]Path to build in — Enter to keep current:[/bold] "
    ).strip()
    if not path:
        return

    path = os.path.abspath(os.path.expanduser(path))
    if os.path.isfile(path):
        console.print(f"[red]✗ '{path}' is a file, not a directory — keeping {tools.SANDBOX}[/red]")
        return

    if not os.path.isdir(path):
        answer = console.input(f"  '{path}' doesn't exist — create it? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            console.print(f"[yellow]Keeping {tools.SANDBOX}[/yellow]")
            return

    tools.set_sandbox(path)
    if not os.path.isdir(os.path.join(tools.SANDBOX, ".git")):
        console.print("[yellow]⚠ Note: this isn't a git repo, so there's no "
                      "auto-checkpoint safety net — changes here can't be "
                      "undone with `git log` / `git reset` like the default "
                      "sandbox.[/yellow]")
    console.print(f"[green]✓ Workspace set to {tools.SANDBOX}[/green]")


def pick_model():
    """List locally registered Ollama models (local + already-pulled cloud
    ones) and let the user choose one, or pull a new Ollama Cloud model by
    name. Falls back to the VA_MODEL default if Ollama is unreachable, so
    this never blocks a working setup."""
    try:
        models = llm.list_models()
    except Exception as error:
        console.print(f"[yellow]⚠ Could not reach Ollama ({error}) — "
                      f"using default model {llm.MODEL}[/yellow]")
        return

    if not models:
        console.print(f"[yellow]⚠ No models registered with Ollama yet — "
                      f"using default {llm.MODEL}[/yellow]")
        return

    console.print("[bold]🧠 Available models:[/bold]")
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
        console.print(f"[yellow]⚠ Invalid choice — using default {llm.MODEL}[/yellow]")
        return
    console.print(f"[green]✓ Using {llm.MODEL}[/green]")


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
        console.print(f"[red]✗ Could not pull '{name}': {error}[/red]")
        console.print("[yellow]If this is a cloud model, make sure you're "
                      "signed in first: ollama signin[/yellow]")
        return

    llm.set_model(name)
    console.print(f"[green]✓ Using {llm.MODEL}[/green]")


def repl():
    console.print(f"\n[dim]Ready — workspace [cyan]{tools.SANDBOX}[/cyan], "
                  f"model [cyan]{llm.MODEL}[/cyan]. Type [bold]help[/bold] for commands.[/dim]")
    while True:
        try:
            task = ui.prompt_task()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]bye 👋[/dim]")
            return

        if not task:
            continue
        if task.lower() in {"exit", "quit"}:
            console.print("[dim]bye 👋[/dim]")
            return
        if task.lower() == "help":
            ui.help_table()
            continue
        if task.lower() == "workspace":
            pick_workspace()
            continue
        if task.lower() == "model":
            pick_model()
            continue

        ui.announce_task(task)
        try:
            stats = run(task)
        except KeyboardInterrupt:
            console.print("\n[yellow]⏹ interrupted — back to prompt[/yellow]")
            continue
        ui.summary(stats)


def main():
    if not PRINT_MODE:
        ui.banner()
        pick_workspace()
        pick_model()
    if len(sys.argv) > 1:
        ui.summary(run(" ".join(sys.argv[1:])))
        return
    repl()


if __name__ == "__main__":
    main()
