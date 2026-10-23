"""Skills from ``.claude/skills/<name>/SKILL.md``, as in Claude Code.

A skill teaches the agent one task. Progressive disclosure keeps it cheap:
only each skill's name and description go into the system prompt; the
body enters the conversation only when the model calls the Skill tool (or
the user types /name), and reference files only if the model then Reads
them. On a 32k window this is what makes a skill library affordable.

Skills are context only. They never grant or deny permission.
"""

import os
import re
from dataclasses import dataclass

from va_agent.core import config
from va_agent.harness import rules
from va_agent.ui.console import console

SKILLS_SUBDIR = os.path.join(".claude", "skills")
COMMANDS_SUBDIR = os.path.join(".claude", "commands")
SKILL_FILE = "SKILL.md"

# User-level skills. A module attribute so tests can point it elsewhere.
USER_SKILLS_DIR = os.path.join(os.path.expanduser("~"), SKILLS_SUBDIR)

NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_NAME = 64
MAX_DESCRIPTION = 1024


@dataclass
class Skill:
    name: str
    description: str
    body: str
    source: str         # absolute path of SKILL.md (or the command .md)
    directory: str      # the skill's folder
    origin: str         # "project" or "user"


def validate(name, description) -> str | None:
    """Why this name/description pair is invalid, or None if it is fine."""
    if not isinstance(name, str) or not name.strip():
        return "name is required"
    if len(name) > MAX_NAME:
        return f"name must be at most {MAX_NAME} characters"
    if not NAME_PATTERN.match(name):
        return ("name must be lowercase letters, digits and hyphens only, "
                "with no leading, trailing or doubled hyphen")
    if not isinstance(description, str) or not description.strip():
        return "description is required"
    if len(description) > MAX_DESCRIPTION:
        return f"description must be at most {MAX_DESCRIPTION} characters"
    return None


def _read(path):
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(), None
    except OSError as error:
        return None, f"cannot read file: {error}"


def _load_skill(path, origin):
    """A Skill from one SKILL.md, or (path, reason) if it is invalid."""
    text, problem = _read(path)
    if problem:
        return path, problem
    meta, body = rules.parse(text)
    if meta is None:
        return path, "frontmatter failed to parse"
    name, description = meta.get("name"), meta.get("description")
    problem = validate(name, description)
    if problem:
        return path, problem

    directory = os.path.dirname(path)
    folder = os.path.basename(directory)
    if name != folder:
        console.print(f"[yellow]  skills: {path} is named {name!r} in its "
                      f"frontmatter but its folder is {folder!r}; using "
                      f"{name!r}[/yellow]")
    return Skill(name=name, description=" ".join(description.split()),
                 body=body.strip(), source=path, directory=directory,
                 origin=origin)


def _first_line(body):
    """The first non-empty body line, without a markdown heading mark."""
    for line in body.splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:MAX_DESCRIPTION]
    return ""


def _load_command(path):
    """A Skill from a bare .claude/commands/<name>.md, or (path, reason).
    The filename stem is the name; frontmatter is optional and a missing
    description defaults to the first non-empty body line."""
    text, problem = _read(path)
    if problem:
        return path, problem
    meta, body = rules.parse(text)
    if meta is None:
        return path, "frontmatter failed to parse"
    name = os.path.splitext(os.path.basename(path))[0]
    description = meta.get("description") or _first_line(body)
    problem = validate(name, description)
    if problem:
        return path, problem
    return Skill(name=name, description=" ".join(str(description).split()),
                 body=body.strip(), source=path,
                 directory=os.path.dirname(path), origin="project")


def _command_files(commands_dir):
    """<commands_dir>/*.md, top level only."""
    if not os.path.isdir(commands_dir):
        return []
    return [os.path.abspath(os.path.join(commands_dir, name))
            for name in sorted(os.listdir(commands_dir))
            if name.endswith(".md")
            and os.path.isfile(os.path.join(commands_dir, name))]


def _skill_files(skills_dir):
    """<skills_dir>/*/SKILL.md, exactly that name, one level deep only."""
    if not os.path.isdir(skills_dir):
        return []
    found = []
    for folder in sorted(os.listdir(skills_dir)):
        path = os.path.join(skills_dir, folder, SKILL_FILE)
        # isfile() alone is case-insensitive on macOS; check the real name.
        if os.path.isfile(path) and SKILL_FILE in os.listdir(
                os.path.join(skills_dir, folder)):
            found.append(os.path.abspath(path))
    return found


def _watched(project):
    """Every file discovery reads, for hot reload."""
    return (_command_files(os.path.join(project, COMMANDS_SUBDIR))
            + _skill_files(USER_SKILLS_DIR)
            + _skill_files(os.path.join(project, SKILLS_SUBDIR)))


_scanned_dir = None     # project dir of the last discover(), for reload
_mtimes = {}            # path -> mtime of every file discover() read


def _report(path, reason):
    config.SKILLS_BROKEN.append((path, reason))
    console.print(f"[yellow]  skills: {path} not loaded: {reason}[/yellow]")


def discover(project_dir=None) -> dict:
    """Find project and user skills, store the valid ones in config.SKILLS
    and the invalid ones in config.SKILLS_BROKEN, and return config.SKILLS.
    Only frontmatter matters here; bodies stay out of the conversation."""
    global _scanned_dir
    config.SKILLS = {}
    config.SKILLS_BROKEN = []
    if not config.SKILLS_ENABLED:
        return config.SKILLS

    project = project_dir or config.SANDBOX
    found = {}
    # Later entries win: commands, then user skills, then project skills.
    for path in _command_files(os.path.join(project, COMMANDS_SUBDIR)):
        command = _load_command(path)
        if isinstance(command, Skill):
            found[command.name] = command
        else:
            _report(*command)
    for origin, base in (("user", USER_SKILLS_DIR),
                         ("project", os.path.join(project, SKILLS_SUBDIR))):
        for path in _skill_files(base):
            skill = _load_skill(path, origin)
            if isinstance(skill, Skill):
                found[skill.name] = skill
            else:
                _report(*skill)

    config.SKILLS = dict(sorted(found.items()))
    _scanned_dir = project
    _mtimes.clear()
    _mtimes.update(_stat(_watched(project)))

    if len(config.SKILLS) > config.SKILLS_WARN_ABOVE:
        console.print(f"[yellow]  skills: {len(config.SKILLS)} skills found; "
                      f"above {config.SKILLS_WARN_ABOVE} a small model may "
                      f"pick the wrong one[/yellow]")
    return config.SKILLS


def _stat(paths):
    stamps = {}
    for path in paths:
        try:
            stamps[path] = os.stat(path).st_mtime_ns
        except OSError:
            pass
    return stamps


# ---------------------------------------------------------------- disclosure

_task = ""              # the current task, for the invocation log
_pending = None         # (name, body) loaded by the last Skill call
# Skills whose body is in the current run's conversation. Each run starts a
# fresh conversation, so "already loaded" is per run; config.SKILLS_LOADED
# is the session-wide record shown by /skills.
_in_context = set()

_PREFIX = "[skill: "


def begin_task(task: str, context=None) -> None:
    """Start a run: remember the task for the invocation log, and note
    skills already in its opening messages (e.g. loaded with /name)."""
    global _task, _pending
    _task = task
    _pending = None
    _in_context.clear()
    for item in context or []:
        content = str(item.get("content", ""))
        if content.startswith(_PREFIX) and "]" in content:
            _in_context.add(content[len(_PREFIX):content.index("]")])


def prompt_block() -> str:
    """Level 1: one line per skill for the system prompt. No bodies."""
    if not config.SKILLS_ENABLED or not config.SKILLS:
        return ""
    lines = ["Available skills (call the Skill tool to load one):"]
    lines += [f"- {skill.name}: {skill.description}"
              for skill in config.SKILLS.values()]
    return "\n".join(lines)


def message(name: str, body: str) -> dict:
    """How a skill body enters the conversation. role "user": several
    local models ignore system messages that arrive mid-conversation."""
    return {"role": "user", "content": f"[skill: {name}]\n\n{body}"}


def _excerpt(task: str) -> str:
    task = " ".join(str(task).split())
    return task if len(task) <= 60 else task[:57] + "..."


def _find(name):
    skill = config.SKILLS.get(name)
    if skill is None:
        available = ", ".join(config.SKILLS) or "(none)"
        raise ValueError(f"Unknown skill {name!r}. Available skills: "
                         f"{available}")
    return skill


def _record(name, task):
    config.SKILLS_LOADED.add(name)
    config.SKILL_INVOCATIONS.append((name, _excerpt(task)))


def load(name: str):
    """For the Skill tool: log the call and return the body, or None when
    it is already in this run's conversation. Raises for an unknown name."""
    skill = _find(name)
    _record(name, _task)
    if name in _in_context:
        return None
    _in_context.add(name)
    return skill.body


def invoke(name: str, task: str) -> str:
    """For /name: log the call and return the body, which the new run
    will start with. Raises for an unknown name."""
    skill = _find(name)
    _record(name, task)
    return skill.body


def skill_tool(name: str) -> str:
    """The Skill tool. Level 2: load a skill's instructions by name."""
    global _pending
    body = load(str(name).strip())
    if body is None:
        return (f"Skill {name!r} is already loaded earlier in this "
                "conversation; follow those instructions.")
    _pending = (name, body)
    return body


def take_pending():
    """(name, body) from the last Skill call that loaded a body, once."""
    global _pending
    pending, _pending = _pending, None
    return pending


# ---------------------------------------------------------------- reload

def maybe_reload() -> bool:
    """Re-stat every skill and command file; rediscover if any changed,
    appeared or disappeared. Called before each run, so editing a skill
    never needs a restart. Does nothing before the first discover()."""
    if _scanned_dir is None or not config.SKILLS_ENABLED:
        return False
    if _stat(_watched(_scanned_dir)) == _mtimes:
        return False
    discover(_scanned_dir)
    return True


def reset_skills() -> None:
    """Forget which skills are loaded and the invocation log. (Not
    reset_session/reset_rules/reset_hooks: sibling modules own those.)"""
    global _pending
    config.SKILLS_LOADED.clear()
    config.SKILL_INVOCATIONS.clear()
    _in_context.clear()
    _pending = None


def sync_context(messages) -> None:
    """After compaction: a skill counts as in context only if its body is
    still in *messages*, so a summarised-away skill can be loaded again."""
    _in_context.clear()
    for item in messages:
        content = str(item.get("content", ""))
        if content.startswith(_PREFIX) and "]" in content:
            _in_context.add(content[len(_PREFIX):content.index("]")])
