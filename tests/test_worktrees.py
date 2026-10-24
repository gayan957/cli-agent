"""Git worktree tests: git calls are faked for exact invocations, and one
end-to-end test uses real git in tmp_path. No network."""

import subprocess

import pytest

from va_agent.core import config
from va_agent.harness import worktrees


@pytest.fixture(autouse=True)
def repo_dir(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    monkeypatch.setattr(config, "SANDBOX", str(root))
    return root


PORCELAIN = """worktree /work/repo
HEAD 1111111111111111111111111111111111111111
branch refs/heads/develop

worktree /work/repo/.claude/worktrees/feature-x
HEAD 2222222222222222222222222222222222222222
branch refs/heads/feature-x

worktree /work/repo/.claude/worktrees/loose
HEAD 3333333333333333333333333333333333333333
detached
"""


@pytest.fixture
def fake_git(monkeypatch, repo_dir):
    """Replace worktrees._git. Responses are keyed by the git subcommand
    words; every call is recorded."""
    calls = []
    responses = {}

    def run(args, cwd):
        calls.append(list(args))
        key = next((k for k in responses if args[:len(k)] == list(k)), None)
        code, out, err = responses.get(key, (0, "", ""))
        return subprocess.CompletedProcess(args, code, out, err)

    porcelain = f"worktree {repo_dir}\nHEAD {'1' * 40}\nbranch refs/heads/develop\n"
    responses[("worktree", "list", "--porcelain")] = (0, porcelain, "")
    monkeypatch.setattr(worktrees, "_git", run)
    return calls, responses


def test_parse_porcelain_multiple_records():
    trees = worktrees.parse_porcelain(PORCELAIN)
    assert trees == [
        ("/work/repo", "develop", "1" * 40, True),
        ("/work/repo/.claude/worktrees/feature-x", "feature-x", "2" * 40, False),
        ("/work/repo/.claude/worktrees/loose", "(detached)", "3" * 40, False),
    ]


def test_create_runs_worktree_add_with_new_branch(fake_git, repo_dir):
    calls, _ = fake_git
    message = worktrees.create("feature-y")
    path = str(repo_dir / ".claude" / "worktrees" / "feature-y")
    assert ["worktree", "add", path, "-b", "feature-y"] in calls
    assert "Created worktree" in message


def test_create_attach_uses_existing_branch(fake_git, repo_dir):
    calls, _ = fake_git
    worktrees.create("feature-y", attach=True)
    path = str(repo_dir / ".claude" / "worktrees" / "feature-y")
    assert ["worktree", "add", path, "feature-y"] in calls


def test_duplicate_branch_offers_attach_instead(fake_git):
    _, responses = fake_git
    responses[("worktree", "add")] = (
        128, "", "fatal: a branch named 'feature-y' already exists")
    message = worktrees.create("feature-y")
    assert message.splitlines()[-1].startswith(worktrees.BRANCH_EXISTS)
    assert "--attach" in message


def test_existing_directory_is_reported_not_deleted(fake_git, repo_dir):
    calls, _ = fake_git
    existing = repo_dir / ".claude" / "worktrees" / "feature-y"
    existing.mkdir(parents=True)
    (existing / "keep.txt").write_text("precious")
    message = worktrees.create("feature-y")
    assert str(existing) in message and "nothing was deleted" in message
    assert (existing / "keep.txt").read_text() == "precious"
    assert not any(call[:2] == ["worktree", "add"] for call in calls)


def test_invalid_branch_name_is_refused(fake_git):
    _, responses = fake_git
    responses[("check-ref-format",)] = (1, "", "")
    assert "not a valid branch name" in worktrees.create("../../escape")


def test_ensure_ignored_adds_once(repo_dir):
    (repo_dir / ".gitignore").write_text(".venv/")         # no trailing newline
    assert worktrees.ensure_ignored(str(repo_dir)) is True
    assert worktrees.ensure_ignored(str(repo_dir)) is False
    assert (repo_dir / ".gitignore").read_text() == ".venv/\n.claude/worktrees/\n"


def test_ensure_ignored_creates_gitignore(repo_dir):
    assert worktrees.ensure_ignored(str(repo_dir)) is True
    assert (repo_dir / ".gitignore").read_text() == ".claude/worktrees/\n"


def with_worktree(responses, repo_dir, branch="feature-x"):
    path = repo_dir / ".claude" / "worktrees" / branch
    responses[("worktree", "list", "--porcelain")] = (0, (
        f"worktree {repo_dir}\nHEAD {'1' * 40}\nbranch refs/heads/develop\n\n"
        f"worktree {path}\nHEAD {'2' * 40}\nbranch refs/heads/{branch}\n"), "")
    return str(path)


def test_remove_with_changes_refuses_without_force(fake_git, repo_dir):
    calls, responses = fake_git
    with_worktree(responses, repo_dir)
    responses[("worktree", "remove")] = (
        128, "", "fatal: '...' contains modified or untracked files, use --force "
                 "to delete it")
    responses[("status", "--short")] = (0, " M README.md\n?? notes.txt\n", "")
    message = worktrees.remove("feature-x")

    assert message.startswith("Refusing")
    assert "M README.md" in message and "notes.txt" in message
    assert "/worktree remove feature-x --force" in message
    assert not any("--force" in call for call in calls)


def test_remove_force_passes_force(fake_git, repo_dir):
    calls, responses = fake_git
    path = with_worktree(responses, repo_dir)
    message = worktrees.remove("feature-x", force=True)
    assert ["worktree", "remove", "--force", path] in calls
    assert message.startswith("Removed")


def test_remove_main_is_refused(fake_git):
    assert "main worktree" in worktrees.remove("develop")


# ------------------------------------------------------------ real git

def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   text=True)


def test_real_git_end_to_end(repo_dir):
    git("init", "-q", "-b", "develop", cwd=repo_dir)
    (repo_dir / "README.md").write_text("hello\n")
    git("add", "-A", cwd=repo_dir)
    git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "init",
        cwd=repo_dir)

    assert "Created worktree" in worktrees.create("feature-test")
    status = subprocess.run(["git", "status", "--short"], cwd=repo_dir,
                            capture_output=True, text=True).stdout
    assert ".claude/" not in status            # ignored, not tracked
    assert [t[1] for t in worktrees.list_worktrees()] == ["develop", "feature-test"]

    path = repo_dir / ".claude" / "worktrees" / "feature-test"
    (path / "README.md").write_text("changed\n")
    assert worktrees.remove("feature-test").startswith("Refusing")
    assert path.exists()
    assert worktrees.remove("feature-test", force=True).startswith("Removed")
    assert not path.exists()

    assert worktrees.BRANCH_EXISTS in worktrees.create("feature-test")
    assert "Created worktree" in worktrees.create("feature-test", attach=True)
