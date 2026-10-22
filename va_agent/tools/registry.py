"""Which tools the model gets, built fresh for every run.

``config.EDIT_MODE`` (write | edit | both) and ``config.TODOS`` are the
Phase 4 experiments, so both the callables and the schemas are built at
call time, never at import. A tool left out of the schemas is also left out
of the registry, so a model that names it anyway cannot use it.

Claude Code names are offered in the schemas. The old names (read_file,
write_file, run_command, grep, list_files) stay callable for models that
still emit them, and old ``path`` arguments are accepted for ``file_path``.
"""

from va_agent.core import config
from va_agent.harness import skills
from va_agent.tools import fs, shell, todo

EDIT_MODES = ("write", "edit", "both")
EDIT_TOOLS = {"Write", "Edit", "write_file"}


def edit_mode():
    """The validated edit mode. Raises on a typo rather than silently
    running the wrong experiment."""
    if config.EDIT_MODE not in EDIT_MODES:
        raise ValueError(f"VA_EDIT_MODE must be one of {EDIT_MODES}, "
                         f"not {config.EDIT_MODE!r}")
    return config.EDIT_MODE


def _file_path_compat(function):
    """Accept the old ``path`` argument where ``file_path`` is expected."""
    def call(file_path=None, path=None, **kwargs):
        target = file_path if file_path is not None else path
        if target is None:
            raise TypeError("file_path is required")
        return function(target, **kwargs)
    call.__name__ = function.__name__
    call.__doc__ = function.__doc__
    return call


def build_registry():
    """name -> callable for this run."""
    read = _file_path_compat(fs.Read)
    tools = {
        "Read": read,
        "read_file": read,
        "Glob": fs.Glob,
        "list_files": lambda path=".": fs.Glob("**/*", path),
        "Grep": fs.Grep,
        "grep": fs.Grep,
        "Bash": shell.Bash,
        "run_command": shell.Bash,
    }
    mode = edit_mode()
    if mode in ("write", "both"):
        write = _file_path_compat(fs.Write)
        tools["Write"] = write
        tools["write_file"] = write
    if mode in ("edit", "both"):
        tools["Edit"] = _file_path_compat(fs.Edit)
    if config.TODOS:
        tools["TodoWrite"] = todo.TodoWrite
    if _skills_offered():
        tools["Skill"] = skills.skill_tool
    return tools


def _skills_offered():
    """The Skill tool exists only when there is a skill to load."""
    return config.SKILLS_ENABLED and bool(config.SKILLS)


def _schema(name, description, properties, required):
    return {"type": "function", "function": {
        "name": name,
        "description": description,
        "parameters": {"type": "object",
                       "properties": properties,
                       "required": required},
    }}


def _string(description):
    return {"type": "string", "description": description}


READ = _schema(
    "Read",
    "Read a file. Lines come back numbered like 'cat -n': the line "
    "number, a tab, then the line. The number is not part of the file. "
    "Use offset and limit to page through long files.",
    {"file_path": _string("Path of the file to read"),
     "offset": {"type": "integer", "description": "First line, 1-based"},
     "limit": {"type": "integer", "description": "How many lines"}},
    ["file_path"])

WRITE = _schema(
    "Write",
    "Write a whole file, creating it and its folders if needed. You MUST "
    "provide the COMPLETE new file content. Never send a diff, a patch, or "
    "only the changed lines.",
    {"file_path": _string("Path of the file to write"),
     "content": _string("Complete new file content")},
    ["file_path", "content"])

EDIT = _schema(
    "Edit",
    "Change an existing file by replacing an exact string. Read the file "
    "first. old_string must match the file exactly, including "
    "indentation and without the line-number prefix, and must be unique "
    "unless replace_all is true.",
    {"file_path": _string("Path of the file to edit"),
     "old_string": _string("Exact text to replace"),
     "new_string": _string("Text to put in its place"),
     "replace_all": {"type": "boolean",
                     "description": "Replace every occurrence"}},
    ["file_path", "old_string", "new_string"])

BASH = _schema(
    "Bash",
    "Run a shell command in a persistent shell; cd and export carry over "
    "between calls. Use it to run tests and programs.",
    {"command": _string("Shell command"),
     "timeout": {"type": "integer",
                 "description": "Seconds before the command is killed "
                                "(default 120)"}},
    ["command"])

GLOB = _schema(
    "Glob",
    "Find files by name pattern, e.g. '**/*.py'. Newest first.",
    {"pattern": _string("Glob pattern; ** matches any folders"),
     "path": _string("Folder to search, default '.'")},
    ["pattern"])

GREP = _schema(
    "Grep",
    "Search file contents with a regex. Use it to find code before "
    "reading files.",
    {"pattern": _string("Regex to search for"),
     "path": _string("File or folder to search, default '.'"),
     "glob": _string("Only search files matching this, e.g. '*.py'"),
     "output_mode": {"type": "string",
                     "enum": ["content", "files_with_matches"],
                     "description": "Matching lines, or file paths only"}},
    ["pattern"])

TODO_WRITE = _schema(
    "TodoWrite",
    "Replace your todo list for this task. Use it to plan multi-step work "
    "and mark progress.",
    {"todos": {"type": "array", "description": "The full list",
               "items": {"type": "object",
                         "properties": {
                             "content": _string("What to do"),
                             "status": {"type": "string",
                                        "enum": ["pending", "in_progress",
                                                 "completed"]}},
                         "required": ["content", "status"]}}},
    ["todos"])


SKILL = _schema(
    "Skill",
    "Load a skill's step-by-step instructions by name. Call it when the "
    "task matches one of the available skills listed in your instructions.",
    {"name": _string("The skill's name, exactly as listed")},
    ["name"])


def build_schemas():
    """JSON schemas for this run, honouring EDIT_MODE and TODOS."""
    mode = edit_mode()
    schemas = [READ]
    if mode in ("write", "both"):
        schemas.append(WRITE)
    if mode in ("edit", "both"):
        schemas.append(EDIT)
    schemas += [BASH, GLOB, GREP]
    if config.TODOS:
        schemas.append(TODO_WRITE)
    if _skills_offered():
        schemas.append(SKILL)
    return schemas
