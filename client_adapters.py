"""Read-only client locations and stdio schemas for RepoWayfinder registration.

The caller owns parsing, merge, backup, atomic writes and registration receipts.
In particular, ``jsonc`` entries must be edited without erasing unrelated comments.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Callable

from jsonc_config import loads as load_jsonc


def _roaming(home: Path) -> Path:
    # An injected home must not accidentally resolve to the real user's config.
    if home != Path.home():
        return home / "AppData" / "Roaming"
    return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")


def _local(home: Path) -> Path:
    if home != Path.home():
        return home / "AppData" / "Local"
    return Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local")


def _one_existing(paths: tuple[Path, ...], default: Path) -> Path:
    existing = [path for path in paths if path.is_file()]
    if len(existing) > 1:
        raise ValueError("Multiple client config variants exist; select one manually")
    return existing[0] if existing else default


def _path_gemini(scope: str, directory: Path, home: Path) -> Path:
    if scope == "project":
        return directory / ".gemini" / "settings.json"
    base = Path(os.environ["GEMINI_CLI_HOME"]).expanduser() if os.environ.get("GEMINI_CLI_HOME") else home
    return base / ".gemini" / "settings.json"


def _path_opencode(scope: str, directory: Path, home: Path) -> Path:
    if scope == "project":
        direct = (directory / "opencode.json", directory / "opencode.jsonc")
        nested = (directory / ".opencode" / "opencode.json", directory / ".opencode" / "opencode.jsonc")
        direct_existing = [p for p in direct if p.is_file()]
        nested_existing = [p for p in nested if p.is_file()]
        if direct_existing and nested_existing:
            raise ValueError("Multiple OpenCode project config locations exist; select one manually")
        return _one_existing(nested if nested_existing else direct, direct[0])
    else:
        custom = os.environ.get("OPENCODE_CONFIG")
        if custom:
            return Path(custom).expanduser()
        base = home / ".config" / "opencode"
    return _one_existing((base / "opencode.json", base / "opencode.jsonc"), base / "opencode.json")


def _opencode_cli_v2() -> bool:
    cli = shutil.which("opencode") or shutil.which("opencode.exe")
    if cli:
        try:
            result = subprocess.run([cli, "--version"], capture_output=True, text=True,
                                    timeout=3, check=False)
            version = (result.stdout or result.stderr).strip()
            if result.returncode == 0 and re.search(r"(?:^|\s)v?2\.\d", version, re.I):
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    return False


def _opencode_section_path(path: Path) -> tuple[str, ...]:
    data: dict = {}
    if path.is_file():
        data = load_jsonc(path.read_text(encoding="utf-8-sig"))
    mcp = data.get("mcp", {})
    if not isinstance(mcp, dict):
        raise ValueError("OpenCode mcp section must be an object")
    config_v2 = isinstance(mcp.get("servers"), dict) or "/v2/" in str(data.get("$schema", ""))
    cli_v2 = _opencode_cli_v2()
    if cli_v2 and mcp and not config_v2:
        raise ValueError("OpenCode v2 is installed but this config uses v1 MCP layout; migrate it first")
    return ("mcp", "servers") if config_v2 or cli_v2 else ("mcp",)


def _path_vscode(scope: str, directory: Path, home: Path) -> Path:
    if scope == "project":
        return directory / ".vscode" / "mcp.json"
    return _roaming(home) / "Code" / "User" / "mcp.json"


def _path_windsurf(scope: str, directory: Path, home: Path) -> Path:
    if scope != "user":
        raise ValueError("Windsurf Cascade has no documented project MCP configuration")
    return home / ".codeium" / "windsurf" / "mcp_config.json"


def _path_claude_desktop(scope: str, directory: Path, home: Path) -> Path:
    if scope != "user":
        raise ValueError("Claude Desktop has no documented project MCP configuration")
    return _roaming(home) / "Claude" / "claude_desktop_config.json"


def _installed(path: Path, home: Path, executables: tuple[str, ...], locations: tuple[Path, ...]) -> bool:
    return path.is_file() or any(shutil.which(name) for name in executables) or any(p.is_file() for p in locations)


def _detect_gemini(path: Path, home: Path) -> bool:
    return _installed(path, home, ("gemini", "gemini.cmd"), ())


def _detect_opencode(path: Path, home: Path) -> bool:
    local = _local(home)
    return _installed(path, home, ("opencode", "opencode.exe"), (
        local / "Programs" / "OpenCode" / "OpenCode.exe",
        local / "Programs" / "opencode" / "opencode.exe",
    ))


def _detect_vscode(path: Path, home: Path) -> bool:
    local = _local(home)
    return _installed(path, home, ("code", "code.cmd", "Code.exe"), (
        local / "Programs" / "Microsoft VS Code" / "Code.exe",
        Path(os.environ.get("ProgramFiles") or "C:/Program Files") / "Microsoft VS Code" / "Code.exe",
        _roaming(home) / "Code" / "User" / "settings.json",
    ))


def _detect_windsurf(path: Path, home: Path) -> bool:
    # The current Devin Desktop default agent uses a different config. Only
    # advertise this adapter with evidence of the legacy Windsurf/Cascade app.
    local = _local(home)
    return path.is_file() or any(p.is_file() for p in (
        local / "Programs" / "Windsurf" / "Windsurf.exe",
        local / "Programs" / "windsurf" / "Windsurf.exe",
        Path(os.environ.get("ProgramFiles") or "C:/Program Files") / "Windsurf" / "Windsurf.exe",
    ))


def _detect_claude_desktop(path: Path, home: Path) -> bool:
    local = _local(home)
    return _installed(path, home, (), (
        local / "Programs" / "Claude" / "Claude.exe",
        local / "Claude" / "Claude.exe",
        _roaming(home) / "Claude" / "Preferences",
    ))


def _argv(python: Path, root: Path, workspace: Path) -> tuple[str, list[str]]:
    return str(python.absolute()), ["-u", str((root / "agent.py").absolute()), "--workspace", str(workspace.absolute()), "serve"]


def _entry_standard(python: Path, root: Path, workspace: Path) -> dict:
    command, args = _argv(python, root, workspace)
    return {"command": command, "args": args}


def _entry_opencode(python: Path, root: Path, workspace: Path) -> dict:
    command, args = _argv(python, root, workspace)
    return {"type": "local", "command": [command, *args]}


def _entry_vscode(python: Path, root: Path, workspace: Path) -> dict:
    return {"type": "stdio", **_entry_standard(python, root, workspace)}


@dataclass(frozen=True)
class ClientAdapter:
    kind: str
    section_path: tuple[str, ...]
    config_path: Callable[[str, Path, Path], Path]
    detect: Callable[[Path, Path], bool]
    entry: Callable[[Path, Path, Path], dict]
    documentation: str
    note: str = ""
    section_resolver: Callable[[Path], tuple[str, ...]] | None = None

    def section_path_for(self, path: Path) -> tuple[str, ...]:
        return self.section_resolver(path) if self.section_resolver else self.section_path


ADAPTERS: dict[str, ClientAdapter] = {
    "gemini": ClientAdapter("json", ("mcpServers",), _path_gemini, _detect_gemini, _entry_standard,
        "https://github.com/google-gemini/gemini-cli/blob/main/docs/tools/mcp-server.md"),
    "opencode": ClientAdapter("jsonc", ("mcp",), _path_opencode, _detect_opencode, _entry_opencode,
        "https://opencode.ai/docs/mcp-servers", "Supports OpenCode v1 and v2 MCP layouts; OPENCODE_CONFIG may redirect the user config.",
        _opencode_section_path),
    "vscode": ClientAdapter("jsonc", ("servers",), _path_vscode, _detect_vscode, _entry_vscode,
        "https://code.visualstudio.com/docs/agents/reference/mcp-configuration", "User path addresses the default VS Code profile."),
    "windsurf": ClientAdapter("json", ("mcpServers",), _path_windsurf, _detect_windsurf, _entry_standard,
        "https://code.visualstudio.com/docs/agents/reference/mcp-configuration",
        "Legacy Windsurf/Cascade only; current Devin Desktop defaults to Devin Local with another configuration."),
    "claude_desktop": ClientAdapter("json", ("mcpServers",), _path_claude_desktop, _detect_claude_desktop,
        _entry_standard, "https://modelcontextprotocol.io/docs/2026-07-28/develop/connect-local-servers"),
}
