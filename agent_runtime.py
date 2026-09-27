"""Per-worker adapters for project-declared CLI commands and UTF-8 processes."""
from pathlib import Path
import os
import re
import shutil
import tomllib


_GIT_READ_COMMANDS = {'rev-parse', 'status', 'log', 'show', 'ls-files',
                      'ls-tree', 'describe', 'diff'}
_GIT_FORBIDDEN_OPTIONS = {'-c', '-C', '-o', '--config-env', '--exec-path',
                          '--output', '--ext-diff', '--textconv', '--no-index',
                          '--git-dir', '--work-tree', '--config', '--file',
                          '--help', '-h'}


class ProjectRuntime:
    """One per-worker owner for command resolution and inherited target env.

    Windows descendants must use sys.executable to relaunch Python: a bare
    `python` can find the base executable before PATH. This is not an OS sandbox
    or interception of arbitrary subprocess calls in repository code.
    """

    def __init__(self, checkout, build_env):
        self.checkout = Path(checkout)
        self.build_env = build_env
        self.selected_python = None

    def interpreter(self, selected=None):
        if selected is not None:
            self.selected_python = Path(selected)
        if self.selected_python is not None:
            return self.selected_python
        # Recovery can contain only a dependency CLI or shell command. Discover
        # the already-created environment even when the core passes no Python.
        candidate = self.checkout / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        return candidate if candidate.is_file() else None

    def environment(self, target_process=False):
        env = dict(self.build_env(target_process=target_process))
        if not target_process:
            return env
        env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PIP_REQUIRE_VIRTUALENV='1')
        # Do not inherit the service host's Python home or activation label.
        # The pip guard stops accidental global installation if a descendant
        # launches base Python; it does not constrain other programs or writes.
        env.pop('PYTHONHOME', None)
        env.pop('VIRTUAL_ENV', None)
        python = self.interpreter()
        if python is not None:
            env['PATH'] = str(python.parent) + os.pathsep + env.get('PATH', os.environ.get('PATH', ''))
            if (python.parent.parent / 'pyvenv.cfg').is_file():
                env['VIRTUAL_ENV'] = str(python.parent.parent)
        return env

    def find_executable(self, name):
        env = self.environment(target_process=True)
        return shutil.which(name, path=env.get('PATH', os.environ.get('PATH', '')))


def _validate_git_step(app, step):
    if step.type != 'exec':
        return False, 'Git metadata commands require an exec step'
    try:
        parts = [app.clean_cli_token(part) for part in app.split_command(step.cmd)]
    except ValueError:
        return False, 'invalid Git command quoting'
    if len(parts) < 2 or parts[1].lower() not in _GIT_READ_COMMANDS:
        return False, 'Git command is not an allowed metadata read'
    for token in parts[2:]:
        option = token.split('=', 1)[0].lower()
        # Git accepts unambiguous long-option abbreviations, including --out.
        forbidden_abbreviation = option.startswith('--') and any(
            denied.startswith(option) for denied in _GIT_FORBIDDEN_OPTIONS
            if denied.startswith('--'))
        if option in _GIT_FORBIDDEN_OPTIONS or option.startswith('-o') or forbidden_abbreviation:
            return False, f'Git option is not allowed: {option}'
    return True, ''


def configure_project_runtime(app, checkout, python_version=''):
    if not hasattr(app, '_agent_runtime_originals'):
        app._agent_runtime_originals = (set(app.SAFE_COMMANDS), app.adapt_command,
                                        app.plan_needs_python, app.build_process_env,
                                        getattr(app, 'validate_plan', None),
                                        getattr(app, 'command_succeeded', None),
                                        getattr(app, 'read_python_version_file', None))
    safe, adapt, needs_python, build_env, validate_plan, succeeded, read_version = app._agent_runtime_originals
    runtime = ProjectRuntime(checkout, build_env)
    app._agent_project_runtime = runtime
    if read_version is not None:
        if python_version:
            selected = tuple(map(int, python_version.split('.')))
            def requested_version(repo_path):
                declared = read_version(repo_path)
                if declared is not None and declared != selected:
                    raise app.PythonEnvironmentError(
                        'waiting_python_version',
                        f'Job requests Python {python_version}, but the repository .python-version requires '
                        f'{declared[0]}.{declared[1]}. Prepare a new job with a compatible version.')
                return selected
            app.read_python_version_file = requested_version
        else:
            app.read_python_version_file = read_version
    scripts = {}
    path = Path(checkout) / 'pyproject.toml'
    if path.is_file():
        try:
            scripts = tomllib.loads(path.read_text(encoding='utf-8-sig')).get('project', {}).get('scripts', {})
        except (OSError, ValueError, AttributeError):
            scripts = {}
    if not isinstance(scripts, dict):
        scripts = {}
    names = {name.lower(): name for name, target in scripts.items()
             if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', name)
             and isinstance(target, str) and ':' in target and name.lower() not in (safe | {'git'})}
    app.SAFE_COMMANDS = safe | {'git'} | names.keys()

    if validate_plan is not None:
        def validate(plan):
            # Explicit Agent plans are authorized commands, not beginner route
            # guesses. Keep syntax/danger checks without a product-name allowlist.
            valid, reason = validate_plan(plan, require_known_command=False)
            if not valid or plan.action != 'DEPLOY':
                return valid, reason
            for step in plan.steps:
                if app.command_head(step.cmd) in {'git', 'git.exe'}:
                    valid, reason = _validate_git_step(app, step)
                    if not valid:
                        return valid, reason
            return True, ''

        app.validate_plan = validate

    def command(cmd, python):
        python = runtime.interpreter(python)
        head = app.command_head(cmd)
        if head in {'python', 'python.exe', 'python3', 'py', 'pip', 'pip.exe', 'pip3'}:
            first = app.clean_cli_token(app.split_command(cmd)[0])
            if '/' in first or '\\' in first or Path(first).is_absolute():
                raise ValueError('Use python or pip with python_version instead of an explicit interpreter path')
            if head == 'py' and len(app.split_command(cmd)) > 1 and re.fullmatch(r'-\d+(?:\.\d+)?', app.clean_cli_token(app.split_command(cmd)[1])):
                raise ValueError('Use python_version instead of a py launcher version flag')
            resolved = adapt(cmd, python)
            # A venv must not recreate the executable currently running inside
            # it (Windows locks that file). Bootstrap with its base Python,
            # preserving interpreter version and all requested venv arguments.
            if python and isinstance(resolved, list) and '-m' in resolved:
                module_index = resolved.index('-m')
                if resolved[module_index + 1:module_index + 2] == ['venv']:
                    cfg = Path(python).parent.parent / 'pyvenv.cfg'
                    if cfg.is_file():
                        config = dict(line.split('=', 1) for line in cfg.read_text(encoding='utf-8').splitlines() if '=' in line)
                        home = next((value.strip() for key, value in config.items() if key.strip() == 'home'), '')
                        base = Path(home) / ('python.exe' if os.name == 'nt' else Path(python).name)
                        if home and base.is_file():
                            resolved[0] = str(base)
            return resolved
        parts = [app.clean_cli_token(part) for part in app.split_command(cmd)]
        if head in names:
            if not python:
                raise ValueError('Project CLI requires its job virtual environment')
            executable = Path(python).parent / (names[head] + ('.exe' if os.name == 'nt' else ''))
            if not executable.is_file():
                raise ValueError(f'Project CLI {head} is not installed in the job environment; add a source installation step')
            resolved = str(executable)
        else:
            # Resolve every executable uniformly, including dependency-provided
            # CLIs and explicit paths. Never silently interpret missing programs
            # as shell source. Relative paths are relative to the checkout.
            executable = Path(parts[0])
            explicit = executable.is_absolute() or '/' in parts[0] or '\\' in parts[0]
            if explicit:
                candidate = executable if executable.is_absolute() else Path(checkout) / executable
                resolved = str(candidate.resolve()) if candidate.is_file() else None
            else:
                resolved = runtime.find_executable(parts[0])
            if not resolved:
                raise ValueError(f'Executable not found: {parts[0]}. Install it in the job environment or use an explicit path. For shell builtins/cmdlets, call the installed shell explicitly (for example powershell -NoProfile -File task.ps1).')
        if os.name == 'nt' and Path(resolved).suffix.lower() in {'.cmd', '.bat'}:
            return [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c',
                    app.windows_cmd_payload([resolved, *parts[1:]])]
        return [resolved, *parts[1:]]

    app.adapt_command = command
    if succeeded is not None:
        def command_succeeded(result, step=None):
            if step is not None and app.command_head(step.cmd) in {'robocopy', 'robocopy.exe'}:
                # Robocopy documents 0..7 as success/status bits; preserve the
                # actual code in evidence rather than rewriting it to zero.
                return not result.timed_out and 0 <= result.returncode < 8
            return succeeded(result, step)
        app.command_succeeded = command_succeeded
    app.plan_needs_python = lambda plan: bool(python_version) or needs_python(plan) or any(app.command_head(step.cmd) in names for step in plan.steps)
    app.build_process_env = runtime.environment
    return sorted(names)
