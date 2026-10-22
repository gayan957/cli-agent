"""Skills tests. No Ollama, no network: skills live in tmp_path."""

import os

import pytest

from va_agent.core import config
from va_agent.harness import permissions, skills
from va_agent.tools import registry


@pytest.fixture(autouse=True)
def project(tmp_path, monkeypatch):
    """A fresh project, an empty user skills dir, no skills loaded."""
    root = tmp_path / "project"
    root.mkdir()
    monkeypatch.setattr(config, "SANDBOX", str(root))
    monkeypatch.setattr(config, "SKILLS_ENABLED", True)
    monkeypatch.setattr(config, "SKILLS", {})
    monkeypatch.setattr(config, "SKILLS_LOADED", set())
    monkeypatch.setattr(config, "SKILLS_BROKEN", [])
    monkeypatch.setattr(config, "SKILL_INVOCATIONS", [])
    monkeypatch.setattr(config, "SKILLS_WARN_ABOVE", 15)
    monkeypatch.setattr(config, "EDIT_MODE", "write")
    monkeypatch.setattr(skills, "USER_SKILLS_DIR", str(tmp_path / "home_skills"))
    monkeypatch.setattr(skills, "_scanned_dir", None)
    skills.reset_skills()
    yield root
    skills.reset_skills()


def write_skill(base, folder, name=None, description="Does a thing.",
                body="Step one.", filename="SKILL.md"):
    """A SKILL.md under base/.claude/skills/<folder>/ (or base itself when
    it already is a skills dir)."""
    skills_dir = base if base.name == "home_skills" else base / ".claude" / "skills"
    path = skills_dir / folder / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    name = folder if name is None else name
    path.write_text(f"---\nname: {name}\ndescription: {description}\n---\n{body}\n")
    return path


def write_command(project, name, text):
    path = project / ".claude" / "commands" / f"{name}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


# ------------------------------------------------------------ validation

@pytest.mark.parametrize("name, description, valid", [
    ("azure-deploy-backend", "present", True),
    ("api-v2", "present", True),
    ("Azure-Deploy", "present", False),
    ("deploy_backend", "present", False),
    ("-leading", "present", False),
    ("trailing-", "present", False),
    ("double--hyphen", "present", False),
    ("a" * 65, "present", False),
    ("deploy", "", False),
    ("", "present", False),
    ("deploy", "x" * 1025, False),
])
def test_validation_table(name, description, valid):
    assert (skills.validate(name, description) is None) is valid


def test_validation_limits_are_inclusive():
    assert skills.validate("a" * 64, "d") is None
    assert skills.validate("deploy", "x" * 1024) is None


# ------------------------------------------------------------ discovery

def test_finds_skill_md(project):
    write_skill(project, "review")
    found = skills.discover()
    assert list(found) == ["review"]
    skill = found["review"]
    assert skill.description == "Does a thing."
    assert skill.body == "Step one."
    assert skill.origin == "project"
    assert os.path.isabs(skill.source)
    assert skill.source.endswith(os.path.join(".claude", "skills", "review",
                                              "SKILL.md"))
    assert skill.directory.endswith(os.path.join(".claude", "skills", "review"))


def test_ignores_lowercase_skill_md(project):
    write_skill(project, "review", filename="skill.md")
    assert skills.discover() == {}


def test_does_not_recurse_into_reference(project):
    write_skill(project, "deploy")
    nested = project / ".claude/skills/deploy/reference/inner/SKILL.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("---\nname: inner\ndescription: hidden\n---\nx\n")
    assert list(skills.discover()) == ["deploy"]


def test_missing_skills_dir_returns_empty():
    assert skills.discover() == {}


def test_project_skill_shadows_user_skill(project, tmp_path):
    home = tmp_path / "home_skills"
    write_skill(home, "review", description="User version.")
    write_skill(home, "only-user", description="User only.")
    write_skill(project, "review", description="Project version.")
    found = skills.discover()
    assert found["review"].description == "Project version."
    assert found["review"].origin == "project"
    assert found["only-user"].origin == "user"


def test_discover_populates_config(project):
    write_skill(project, "review")
    returned = skills.discover()
    assert config.SKILLS is returned
    assert list(config.SKILLS) == ["review"]


def test_invalid_skill_is_excluded_and_recorded(project, capsys):
    write_skill(project, "good")
    bad = write_skill(project, "Bad_Name", name="Bad_Name")
    skills.discover()
    assert list(config.SKILLS) == ["good"]
    assert len(config.SKILLS_BROKEN) == 1
    path, reason = config.SKILLS_BROKEN[0]
    assert path.endswith(os.path.join("Bad_Name", "SKILL.md"))
    assert "lowercase" in reason
    assert "Bad_Name" in capsys.readouterr().out


def test_malformed_frontmatter_is_broken(project):
    path = project / ".claude/skills/broken/SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("---\nname: [unclosed\n---\nbody\n")
    skills.discover()
    assert config.SKILLS == {}
    assert config.SKILLS_BROKEN[0][1] == "frontmatter failed to parse"


def test_frontmatter_name_wins_over_folder(project, capsys):
    write_skill(project, "folder-name", name="real-name")
    found = skills.discover()
    assert list(found) == ["real-name"]
    out = capsys.readouterr().out
    assert "real-name" in out and "folder-name" in out


def test_many_skills_warn_but_all_load(project, monkeypatch, capsys):
    monkeypatch.setattr(config, "SKILLS_WARN_ABOVE", 3)
    for number in range(5):
        write_skill(project, f"skill-{number}")
    found = skills.discover()
    assert len(found) == 5
    assert "5 skills found" in capsys.readouterr().out


def test_skills_disabled_scans_nothing(project, monkeypatch):
    write_skill(project, "review")
    monkeypatch.setattr(config, "SKILLS_ENABLED", False)
    assert skills.discover() == {}
    assert skills.prompt_block() == ""


# ------------------------------------------------------------ disclosure

BODY = "DISTINCTIVE-BODY-TEXT-7f3a: run git diff first."


@pytest.fixture
def review(project):
    write_skill(project, "code-review",
                description="Reviews a diff for bugs. Use for reviews.",
                body=BODY)
    write_skill(project, "write-tests", description="Generates pytest tests.")
    skills.discover()


def test_prompt_block_lists_descriptions(review):
    block = skills.prompt_block()
    assert block.startswith("Available skills (call the Skill tool to load one):")
    assert "- code-review: Reviews a diff for bugs. Use for reviews." in block
    assert "- write-tests: Generates pytest tests." in block


def test_prompt_block_has_no_body_text(review):
    assert "DISTINCTIVE-BODY-TEXT-7f3a" not in skills.prompt_block()


def test_prompt_block_empty_without_skills():
    skills.discover()
    assert skills.prompt_block() == ""


def test_skill_tool_returns_the_body(review):
    skills.begin_task("review my changes")
    assert skills.skill_tool("code-review") == BODY
    assert "code-review" in config.SKILLS_LOADED
    assert config.SKILL_INVOCATIONS == [("code-review", "review my changes")]


def test_second_call_does_not_return_body_again(review):
    skills.begin_task("t")
    skills.skill_tool("code-review")
    again = skills.skill_tool("code-review")
    assert BODY not in again
    assert "already loaded" in again


def test_unknown_skill_lists_valid_names(review):
    with pytest.raises(ValueError) as error:
        skills.skill_tool("deploy")
    assert "code-review" in str(error.value)
    assert "write-tests" in str(error.value)


def test_body_reaches_the_model_once_as_a_user_message(review, monkeypatch):
    from va_agent.core import llm
    monkeypatch.setattr(config, "AUTO_APPROVE", True)
    seen = []
    script = iter([
        {"content": "", "tool_calls": [
            {"name": "Skill", "arguments": {"name": "code-review"}}]},
        {"content": "Done.", "tool_calls": []},
    ])

    def chat(messages, tools=None):
        seen.append([dict(m) for m in messages])
        return next(script)

    monkeypatch.setattr(llm, "chat", chat)
    from va_agent.core.agent import run
    run("review my changes", verbose=False)

    assert BODY not in seen[0][0]["content"]
    final = seen[-1]
    carrying = [m for m in final if BODY in m["content"]]
    assert carrying == [{"role": "user",
                         "content": f"[skill: code-review]\n\n{BODY}"}]


# ------------------------------------------------------------ registry

def schema_names():
    return {schema["function"]["name"] for schema in registry.build_schemas()}


def test_skill_tool_absent_without_skills():
    skills.discover()
    assert "Skill" not in schema_names()
    assert "Skill" not in registry.build_registry()


def test_skill_tool_absent_when_disabled(review, monkeypatch):
    assert "Skill" in schema_names()
    monkeypatch.setattr(config, "SKILLS_ENABLED", False)
    assert "Skill" not in schema_names()
    assert "Skill" not in registry.build_registry()


def test_skill_tool_is_read_only():
    assert "Skill" not in permissions.MUTATING
    assert permissions.check("Skill", {"name": "x"})[0] == "allow"


# ------------------------------------------------------------ commands

def test_command_file_is_a_skill(project):
    write_command(project, "deploy",
                  "---\ndescription: Ships the app.\n---\nRun the deploy.\n")
    found = skills.discover()
    assert found["deploy"].description == "Ships the app."
    assert found["deploy"].body == "Run the deploy."


def test_command_description_defaults_to_first_body_line(project):
    write_command(project, "deploy", "\n# Ship it to staging\n\n1. build\n")
    assert skills.discover()["deploy"].description == "Ship it to staging"


def test_skill_shadows_command_of_same_name(project):
    write_command(project, "deploy", "Command version.\n")
    write_skill(project, "deploy", description="Skill version.")
    found = skills.discover()
    assert found["deploy"].description == "Skill version."


def test_slash_invocation_runs_task_with_body(review, monkeypatch):
    from va_agent import cli
    calls = []
    monkeypatch.setattr(cli, "run", lambda task, **kw: calls.append((task, kw))
                        or {"steps": 0, "tool_calls": 0, "tool_failures": 0,
                            "seconds": 0, "finished": True})
    cli._slash_command("/code-review the parser changes")

    task, kwargs = calls[0]
    assert task == "the parser changes"
    assert kwargs["context"][0] == {"role": "user",
                                    "content": f"[skill: code-review]\n\n{BODY}"}
    assert config.SKILL_INVOCATIONS == [("code-review", "the parser changes")]


# ------------------------------------------------------------ reset and reload

def test_reset_skills_clears_loaded(review):
    skills.begin_task("t")
    skills.skill_tool("code-review")
    skills.reset_skills()
    assert config.SKILLS_LOADED == set()
    assert config.SKILL_INVOCATIONS == []


def test_editing_skill_md_triggers_rediscovery(project):
    path = write_skill(project, "review", description="Old.")
    skills.discover()
    write_skill(project, "review", description="New.")
    stamp = os.stat(path).st_mtime_ns + 1_000_000_000
    os.utime(path, ns=(stamp, stamp))
    assert skills.maybe_reload() is True
    assert config.SKILLS["review"].description == "New."
    assert skills.maybe_reload() is False


def test_new_skill_is_picked_up_on_reload(project):
    write_skill(project, "review")
    skills.discover()
    write_skill(project, "deploy")
    assert skills.maybe_reload() is True
    assert set(config.SKILLS) == {"review", "deploy"}


def test_each_run_starts_without_skills_in_context(review):
    skills.begin_task("first")
    assert skills.skill_tool("code-review") == BODY
    skills.begin_task("second")             # a new conversation
    assert skills.skill_tool("code-review") == BODY


# ------------------------------------------------------------ 7B quirks

@pytest.mark.parametrize("reply", [
    # The skill named as a tool, written as text (seen live on qwen 7B).
    {"content": '{"name": "code-review", "arguments": {}}', "tool_calls": []},
    # The same as a structured call.
    {"content": "", "tool_calls": [{"name": "code-review", "arguments": {}}]},
])
def test_skill_name_used_as_tool_name_loads_the_skill(review, monkeypatch, reply):
    from va_agent.core import llm
    monkeypatch.setattr(config, "AUTO_APPROVE", True)
    seen = []
    script = iter([reply, {"content": "Done.", "tool_calls": []}])

    def chat(messages, tools=None):
        seen.append([dict(m) for m in messages])
        return next(script)

    monkeypatch.setattr(llm, "chat", chat)
    from va_agent.core.agent import run
    stats = run("review my changes", verbose=False)

    assert stats["tool_failures"] == 0
    assert "code-review" in config.SKILLS_LOADED
    assert any(m["content"] == f"[skill: code-review]\n\n{BODY}"
               for m in seen[-1])


def test_plain_text_reply_is_left_alone(review):
    from va_agent.core.agent import _skill_aliases
    from va_agent.tools import registry
    reply = {"content": "The answer is 4.", "tool_calls": []}
    assert _skill_aliases(reply, registry.build_registry()) == reply
