"""Conservative DSH Desktop web-profile MCP registration helpers.

The public render functions never write files. DSH's bundled YAML parser is
used for validation; only a marked text block is inserted or removed so user
comments and unrelated settings keep their exact spelling.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess


ROW_ID = "repo-wayfinder-mcp"
SERVER_NAME = "repo_wayfinder"
BEGIN = "# RepoWayfinder Agent MCP begin"
END = "# RepoWayfinder Agent MCP end"
_YAML_SCAN = r"""
const fs = require('node:fs');
const YAML = require(process.argv[1]);
const doc = YAML.parseDocument(fs.readFileSync(0, 'utf8'), {
  keepSourceTokens: true,
  customTags: [{ tag: 'tag:yaml.org,2002:js', resolve: text => text }]
});
if (doc.errors.length || !YAML.isSeq(doc.contents)) {
  console.error('DSH patch must be a valid top-level YAML sequence'); process.exit(2);
}
const entries = doc.toJS();
const rows = [];
for (const item of entries) {
  if (!item || typeof item !== 'object' || Array.isArray(item)) continue;
  if (typeof item.id === 'string') rows.push(item);
  if (Array.isArray(item.insert)) for (const row of item.insert) {
    if (row && typeof row === 'object' && typeof row.id === 'string') rows.push(row);
  }
}
process.stdout.write(JSON.stringify(rows));
"""


def _profile(home: Path) -> Path:
    return home / "profiles" / "web" / "cordis.patch.yml"


def _valid_home(home: Path) -> bool:
    manifest = home / "profiles" / "web" / "package.json"
    if not manifest.is_file():
        return False
    try:
        profile = json.loads(manifest.read_text(encoding="utf-8-sig"))
        return "@deepseek-ai/dsh-web-app" in profile["dsh"]["profile"]["bundles"]
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _home_from_bundle(bundle: Path) -> Path | None:
    if not (bundle / "app" / "DSH Desktop.exe").is_file():
        return None
    portable = bundle / "data" / "Roaming" / "dsh-desktop" / "harness"
    return portable if _valid_home(portable) else None


def _windows_executables() -> list[Path]:
    """Read process and Start Menu evidence without launching DSH."""
    if os.name != "nt":
        return []
    script = r'''
$paths = @()
Get-CimInstance Win32_Process -Filter "name = 'DSH Desktop.exe'" -ErrorAction SilentlyContinue |
  ForEach-Object { if ($_.ExecutablePath) { $paths += $_.ExecutablePath } }
$shell = New-Object -ComObject WScript.Shell
$roots = @("$env:APPDATA\Microsoft\Windows\Start Menu\Programs", "$env:ProgramData\Microsoft\Windows\Start Menu\Programs", "$env:USERPROFILE\Desktop")
foreach ($root in $roots) {
  if (Test-Path -LiteralPath $root) {
    Get-ChildItem -LiteralPath $root -Recurse -Filter '*.lnk' -File -ErrorAction SilentlyContinue |
      Where-Object { $_.Name -match 'DSH|DeepSeek' } |
      ForEach-Object { $target = $shell.CreateShortcut($_.FullName).TargetPath; if ($target) { $paths += $target } }
  }
}
$paths | Select-Object -Unique
'''
    try:
        result = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                                capture_output=True, text=True, timeout=12, check=True)
        return [Path(line.strip()) for line in result.stdout.splitlines() if line.strip()]
    except (OSError, subprocess.SubprocessError):
        return []


def discover(scope: str, directory: Path, home: Path,
             explicit_home: Path | None = None) -> Path | None:
    """Find the active web profile; DSH has no project-scoped MCP profile."""
    if scope != "user":
        return None
    if explicit_home is not None:
        candidate = Path(explicit_home).expanduser()
        if not _valid_home(candidate):
            raise ValueError("--dsh-home is not a DSH web-profile harness home")
        return _profile(candidate)
    unique = candidate_homes(scope, directory, home)
    if len(unique) > 1:
        raise ValueError("Multiple DSH web profiles found; select one with --dsh-home")
    return _profile(unique[0]) if unique else None


def candidate_homes(scope: str, directory: Path, home: Path) -> list[Path]:
    """Return validated, unique harness homes for interactive disambiguation."""
    if scope != "user":
        return []
    candidates: list[Path] = []
    environment = os.environ.get("DSH_HOME")
    if environment:
        candidates.append(Path(environment).expanduser())
    if Path(home) == Path.home():
        roaming = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        candidates.append(roaming / "dsh-desktop" / "harness")
        for exe in _windows_executables():
            if exe.name.lower() == "dsh desktop.exe" and exe.parent.name.lower() == "app":
                found = _home_from_bundle(exe.parent.parent)
                if found:
                    candidates.append(found)
    else:
        candidates.append(Path(home) / "AppData" / "Roaming" / "dsh-desktop" / "harness")
    # A checked-out agent commonly sits beside a portable DSH distribution.
    # Keep this narrow: inspect only direct sibling release bundles.
    for parent in (Path(directory).parent, Path(directory).parent.parent):
        if parent.is_dir():
            for exe in parent.glob("*/dist/*/app/DSH Desktop.exe"):
                found = _home_from_bundle(exe.parent.parent)
                if found:
                    candidates.append(found)
    return list(dict.fromkeys(path.resolve() for path in candidates if _valid_home(path)))


def _node_yaml(patch_path: Path) -> tuple[Path, Path]:
    for ancestor in patch_path.parents:
        node_modules = ancestor / "app" / "resources" / "app" / "node_modules"
        node = node_modules / "node" / "bin" / "node.exe"
        yaml = node_modules / "yaml"
        if node.is_file() and (yaml / "package.json").is_file():
            return node, yaml
    # Normal desktop installs have a separate user data path. Resolve the
    # installed executable from the live process or Start Menu shortcut.
    for exe in _windows_executables():
        if exe.name.lower() == "dsh desktop.exe":
            node_modules = exe.parent / "resources" / "app" / "node_modules"
            node = node_modules / "node" / "bin" / "node.exe"
            yaml = node_modules / "yaml"
            if node.is_file() and (yaml / "package.json").is_file():
                return node, yaml
    raise ValueError("DSH bundled Node/YAML parser not found")


def _rows(patch_path: Path, source: str) -> list[dict]:
    if not source.strip():
        return []
    node, yaml = _node_yaml(patch_path)
    try:
        result = subprocess.run([str(node), "-e", _YAML_SCAN, str(yaml)],
                                input=source.encode("utf-8"), capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError("Could not parse DSH profile patch") from exc
    if result.returncode:
        raise ValueError("DSH profile patch is not a valid top-level YAML sequence")
    return json.loads(result.stdout.decode("utf-8"))


def _entry(command: str, args: list[str], cwd: str) -> dict:
    return {"transport": "stdio", "serverName": SERVER_NAME, "command": command,
            "args": args, "env": {}, "cwd": cwd, "failOnStartupError": True}


def current_entry(patch_path: Path, source: str) -> dict | None:
    """Return this MCP row's config, rejecting duplicate ids/namespaces."""
    matches = [row for row in _rows(Path(patch_path), source)
               if row.get("id") == ROW_ID or (isinstance(row.get("config"), dict)
               and row["config"].get("serverName") == SERVER_NAME)]
    if not matches:
        return None
    if (len(matches) != 1 or matches[0].get("id") != ROW_ID or
            matches[0].get("name") != "@deepseek-ai/dsh-mcp-client" or
            not isinstance(matches[0].get("config"), dict)):
        raise ValueError("Conflicting DSH MCP row id or serverName")
    return matches[0]["config"]


def _block(entry: dict, empty_line: str | None, separator: bool, eol: str) -> str:
    quote = lambda value: json.dumps(value, ensure_ascii=False)
    original = empty_line.encode("utf-8").hex() if empty_line is not None else "none"
    lines = [f"{BEGIN} emptyline={original} sep={int(separator)}", "- insert:", f"    - id: {ROW_ID}",
             "      name: '@deepseek-ai/dsh-mcp-client'", "      config:",
             "        transport: stdio", f"        serverName: {SERVER_NAME}",
             f"        command: {quote(entry['command'])}",
             f"        args: {quote(entry['args'])}", "        env: {}",
             f"        cwd: {quote(entry['cwd'])}", "        failOnStartupError: true", END]
    return eol.join(lines) + eol


def render_add(patch_path: Path, source: str, command: str,
               args: list[str], cwd: str) -> str:
    """Render a single owned MCP insert, preserving all unrelated source text."""
    patch_path = Path(patch_path)
    existing = _rows(patch_path, source)
    if any(row.get("id") == ROW_ID or (isinstance(row.get("config"), dict)
           and row["config"].get("serverName") == SERVER_NAME)
           for row in existing):
        raise ValueError("RepoWayfinder DSH MCP row already exists")
    if BEGIN in source or END in source:
        raise ValueError("RepoWayfinder ownership marker already exists")
    was_empty = not existing and bool(re.fullmatch(r"(?s)(?:\s|#[^\n]*\n)*\[\]\s*", source))
    entry = _entry(command, args, cwd)
    eol = "\r\n" if "\r\n" in source else "\n"
    if was_empty:
        match = re.search(r"(?m)^[ \t]*\[\][ \t]*(?:\r?\n|$)", source)
        if match is None:
            raise ValueError("Could not locate empty DSH YAML sequence")
        original_line = match.group(0)
        block = _block(entry, original_line, False, eol)
        if not original_line.endswith("\n"):
            block = block.rstrip("\r\n")
        result = source[:match.start()] + block + source[match.end():]
    else:
        separator = bool(source and not source.endswith("\n"))
        block = _block(entry, None, separator, eol)
        result = source + (eol if separator else "") + block
    if not any(row.get("id") == ROW_ID and row.get("config") == entry
               for row in _rows(patch_path, result)):
        raise ValueError("DSH MCP patch did not compose as expected")
    return result


def render_remove(patch_path: Path, source: str, expected_entry: dict) -> str:
    """Remove only the exact owned block whose config matches the receipt."""
    patch_path = Path(patch_path)
    match = _owned_match(source)
    expected_block = expected_entry.get("_owned_block")
    if expected_block is not None and match.group(0) != expected_block:
        raise ValueError("Owned DSH MCP block changed; preserving user configuration")
    config = {key: value for key, value in expected_entry.items() if key != "_owned_block"}
    owned = _rows(patch_path, match.group(0))
    if len(owned) != 1 or owned[0].get("id") != ROW_ID or owned[0].get("config") != config:
        raise ValueError("Owned DSH MCP block changed; preserving user configuration")
    original = match.group(1)
    replacement = bytes.fromhex(original).decode("utf-8") if original != "none" else ""
    prefix = source[:match.start()]
    if match.group(2) == "1":
        ending = "\r\n" if prefix.endswith("\r\n") else "\n"
        if not prefix.endswith(ending):
            raise ValueError("Owned DSH MCP separator changed")
        prefix = prefix[:-len(ending)]
    result = prefix + replacement + source[match.end():]
    if any(row.get("id") == ROW_ID for row in _rows(patch_path, result)):
        raise ValueError("Another DSH MCP row remains; preserving user configuration")
    return result


def _owned_match(source: str) -> re.Match[str]:
    if source.count(BEGIN) != 1 or source.count(END) != 1:
        raise ValueError("Owned DSH MCP block is missing or ambiguous")
    match = re.search(r"(?m)^" + re.escape(BEGIN) +
                      r" emptyline=(none|[0-9a-f]+) sep=([01])\r?\n(?:(?!^" + re.escape(END) +
                      r").*\r?\n)*^" + re.escape(END) + r"\r?\n?", source, re.M)
    if match is None:
        raise ValueError("Owned DSH MCP block is missing or ambiguous")
    return match


def owned_block(source: str) -> str:
    """Return exactly the managed YAML block to place in a removal receipt."""
    return _owned_match(source).group(0)
