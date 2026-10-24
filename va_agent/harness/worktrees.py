"""Git worktrees at ``<repo root>/.claude/worktrees/<branch>``, so several
branches can be worked on in parallel, each with its own session.

The directory sits inside the repository, so it must be gitignored or the
main repo would track a copy of itself; git tracks worktrees through its
own metadata, so ignoring the directory breaks nothing.

Removing a worktree with uncommitted changes is refused unless --force is
asked for explicitly: silently discarding an agent's work would undo what
the permission system and snapshots protect.
"""

import os
import subprocess

from va_agent.core import config

WORKTREES_SUBDIR = os.path.join(".claude", "worktrees")
IGNORE_LINE = ".claude/worktrees/"
BRANCH_EXISTS = "Branch already exists"     # prefix of create()'s answer


def _git(args: list, cwd: str) -> subprocess.CompletedProcess:
    """Run git; never raises (a missing git or bad cwd is a failed run)."""
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                              text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(error))


def parse_porcelain(text: str) -> list:
    """`git worktree list --porcelain` -> [(path, branch, head, is_main)].
    Records are blank-line separated; the first is the main worktree."""
    found = []
    for record in text.strip().split("\n\n"):
        fields = {}
        for line in record.splitlines():
            key, _, value = line.partition(" ")
            fields[key] = value
        if "worktree" not in fields:
            continue
        branch = fields.get("branch", "")
        branch = branch[len("refs/heads/"):] if branch.startswith(
            "refs/heads/") else (branch or "(detached)")
        found.append((fields["worktree"], branch, fields.get("HEAD", ""),
                      not found))
    return found


def list_worktrees() -> list:
    """Every worktree of the repo SANDBOX is in, main first."""
    done = _git(["worktree", "list", "--porcelain"], config.SANDBOX)
    return parse_porcelain(done.stdout) if done.returncode == 0 else []


def main_root() -> str | None:
    """The main worktree's path, or None outside a git repository."""
    trees = list_worktrees()
    return trees[0][0] if trees else None


def path_for(branch: str, root: str | None = None) -> str:
    return os.path.join(root or main_root() or config.SANDBOX,
                        WORKTREES_SUBDIR, branch)


def ensure_ignored(root: str | None = None) -> bool:
    """Add .claude/worktrees/ to the repo's .gitignore if it is not there.
    Returns True when the line was added."""
    root = root or main_root() or config.SANDBOX
    path = os.path.join(root, ".gitignore")
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        text = ""
    wanted = {IGNORE_LINE, IGNORE_LINE.rstrip("/"), "/" + IGNORE_LINE,
              "/" + IGNORE_LINE.rstrip("/")}
    if any(line.strip() in wanted for line in text.splitlines()):
        return False
    with open(path, "a", encoding="utf-8") as handle:
        if text and not text.endswith("\n"):
            handle.write("\n")
        handle.write(IGNORE_LINE + "\n")
    return True


def _valid_branch(branch: str, root: str) -> bool:
    return bool(branch) and _git(["check-ref-format", "--branch", branch],
                                 root).returncode == 0


def create(branch: str, attach: bool = False) -> str:
    """Add a worktree for a new branch (or, with attach, an existing one).
    Returns a message; never raises and never deletes anything."""
    root = main_root()
    if root is None:
        return "not inside a git repository"
    if not _valid_branch(branch, root):
        return f"{branch!r} is not a valid branch name"
    path = path_for(branch, root)
    notes = []
    if ensure_ignored(root):
        notes.append(f"Added {IGNORE_LINE} to {os.path.join(root, '.gitignore')}"
                     " so the repo does not track its own worktrees.")
    if os.path.exists(path):
        return "\n".join(notes + [f"{path} already exists; nothing was "
                                  "deleted. Remove it or pick another name."])

    args = ["worktree", "add", path, branch] if attach else \
        ["worktree", "add", path, "-b", branch]
    done = _git(args, root)
    if done.returncode == 0:
        how = "existing branch" if attach else "new branch"
        return "\n".join(notes + [f"Created worktree {path} on {how} {branch}"])
    error = (done.stderr or done.stdout).strip()
    if "a branch named" in error and "already exists" in error:
        return "\n".join(notes + [
            f"{BRANCH_EXISTS}: {branch}. To check it out in a worktree "
            f"instead, attach it: git worktree add {path} {branch} "
            f"(/worktree create {branch} --attach)."])
    if "already exists" in error:
        return "\n".join(notes + [f"{path} already exists; nothing was "
                                  "deleted."])
    return "\n".join(notes + [f"git worktree add failed: {error}"])


def _find(branch: str):
    for tree in list_worktrees():
        if tree[1] == branch:
            return tree
    return None


def remove(branch: str, force: bool = False) -> str:
    """Remove a worktree. With uncommitted changes this refuses and lists
    them; --force is passed only when *force* is True."""
    tree = _find(branch)
    if tree is None:
        return f"no worktree for branch {branch}"
    path, _, _, is_main = tree
    if is_main:
        return "refusing to remove the main worktree"
    root = main_root()
    args = ["worktree", "remove", *(["--force"] if force else []), path]
    done = _git(args, root)
    if done.returncode == 0:
        return (f"Removed worktree {path}. Branch {branch} is kept; delete "
                f"it with: git branch -d {branch}")
    error = (done.stderr or done.stdout).strip()
    if "modified or untracked files" in error:
        changed = _git(["status", "--short"], path).stdout.rstrip()
        listing = "\n".join(f"  {line}" for line in changed.splitlines()) \
            or "  (could not list them)"
        return (f"Refusing to remove {path}: it has uncommitted changes that "
                f"would be lost:\n{listing}\nTo discard them, run: "
                f"/worktree remove {branch} --force")
    return f"git worktree remove failed: {error}"


def current() -> tuple | None:
    """The worktree record SANDBOX is in, if any."""
    here = os.path.realpath(config.SANDBOX)
    best = None
    for tree in list_worktrees():
        root = os.path.realpath(tree[0])
        if here == root or here.startswith(root + os.sep):
            if best is None or len(root) > len(os.path.realpath(best[0])):
                best = tree
    return best


def banner_branch() -> str | None:
    """The branch to show in the banner: only for a non-main worktree."""
    tree = current()
    return tree[1] if tree and not tree[3] else None
