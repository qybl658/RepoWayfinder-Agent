"""Register this checkout with installed MCP clients without changing account settings."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
from typing import Any

from client_adapters import ADAPTERS


ROOT = Path(__file__).resolve().parent
CLIENTS = ("grok", "codex", "cursor", "dsh", "claude", *ADAPTERS)
DISPLAY = {"grok": "Grok", "codex": "Codex", "cursor": "Cursor", "dsh": "DSH",
           "claude": "Claude Code", "gemini": "Gemini CLI", "opencode": "OpenCode",
           "vscode": "VS Code", "windsurf": "Windsurf", "claude_desktop": "Claude Desktop"}
SERVER = "repo_wayfinder"
BEGIN = "# BEGIN RepoWayfinder Agent registration\n"
END = "# END RepoWayfinder Agent registration\n"


def config_path(client: str, scope: str, directory: Path, home: Path, *, dsh_home: Path | None = None) -> Path:
    if client == "dsh":
        import dsh_adapter
        found = dsh_adapter.discover(scope, directory, home, explicit_home=dsh_home)
        if found is None:
            raise FileNotFoundError("DSH web profile not found for this scope; use --dsh-home for a portable harness")
        return found
    if client in ADAPTERS:
        return ADAPTERS[client].config_path(scope, directory, home)
    base = directory if scope == "project" else home
    if client == "grok":
        return base / ".grok" / "config.toml"
    if client == "codex":
        codex_home = Path(os.environ["CODEX_HOME"]).expanduser().resolve() if scope == "user" and os.environ.get("CODEX_HOME") else base / ".codex"
        return codex_home / "config.toml"
    if client == "cursor":
        return base / ".cursor" / "mcp.json"
    return base / (".mcp.json" if scope == "project" else ".claude.json")


def detect_client(client: str, path: Path, home: Path | None = None) -> bool:
    if client == "dsh":
        return path.is_file()
    if client in ADAPTERS:
        return ADAPTERS[client].detect(path, home or Path.home())
    if path.is_file() or shutil.which(client) is not None:
        return True
    if client == "cursor" and os.name == "nt":
        locations = [Path(os.environ[name]) / suffix for name, suffix in (
            ("LOCALAPPDATA", "Programs/Cursor/Cursor.exe"),
            ("LOCALAPPDATA", "Programs/cursor/Cursor.exe"),
            ("ProgramFiles", "Cursor/Cursor.exe"),
        ) if os.environ.get(name)]
        return any(location.is_file() for location in locations)
    if client == "claude":
        local_bin = (home or Path.home()) / ".local" / "bin"
        return any((local_bin / name).is_file() for name in ("claude", "claude.exe"))
    return False


def choose_python(root: Path) -> Path:
    candidates = [root / ".venv" / "Scripts" / "python.exe", root / ".venv" / "bin" / "python", root / "venv" / "Scripts" / "python.exe", root / "venv" / "bin" / "python", Path(sys.executable)]
    seen: set[Path] = set()
    for candidate in candidates:
        # Keep a venv's python symlink intact; resolving it may select the base interpreter.
        candidate = candidate.absolute()
        if candidate in seen or not candidate.is_file():
            continue
        seen.add(candidate)
        try:
            check = subprocess.run(
                [str(candidate), "-c", "import sys; assert sys.version_info >= (3, 11); import requests, dotenv, agent_service, mcp_server"],
                cwd=root, capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if check.returncode == 0:
            return candidate
    raise RuntimeError("No usable Python with RepoWayfinder dependencies found; install requirements.txt in this checkout's .venv")


def server_entry(client: str, python: Path, root: Path, workspace: Path) -> dict[str, Any]:
    if client == "dsh":
        import dsh_adapter
        return dsh_adapter._entry(str(python), ["-u", str(root / "agent.py"), "--workspace", str(workspace), "serve"], str(root))
    if client in ADAPTERS:
        return ADAPTERS[client].entry(python, root, workspace)
    entry: dict[str, Any] = {
        "command": str(python),
        "args": ["-u", str(root / "agent.py"), "--workspace", str(workspace), "serve"],
    }
    if client != "cursor":
        if client != "claude":
            entry.update(cwd=str(root), enabled=True, startup_timeout_sec=30, tool_timeout_sec=75)
    if client == "claude":
        entry = {"type": "stdio", **entry}
    return entry


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _section_path(client: str, path: Path | None = None) -> tuple[str, ...]:
    if client in ADAPTERS:
        return ADAPTERS[client].section_path_for(path) if path is not None else ADAPTERS[client].section_path
    return ("mcpServers",) if client in ("cursor", "claude") else ("mcp_servers",)


def _section(data: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    current: Any = data
    for key in keys:
        current = current.get(key, {}) if isinstance(current, dict) else None
        if not isinstance(current, dict):
            raise ValueError("MCP server section must be an object/table")
    return current


def load_config(client: str, source: str, path: Path | None = None) -> dict[str, Any]:
    if client in ADAPTERS and ADAPTERS[client].kind == "jsonc":
        import jsonc_config
        data = jsonc_config.loads(source or "{}")
    elif client in ("cursor", "claude") or client in ADAPTERS:
        data = json.loads(source or "{}", object_pairs_hook=_unique_object)
    else:
        data = tomllib.loads(source)
    if not isinstance(data, dict):
        raise ValueError("Configuration root must be an object/table")
    _section(data, _section_path(client, path))
    return data


def _toml_entry(entry: dict[str, Any]) -> str:
    # JSON strings and arrays are valid TOML basic strings and arrays here.
    lines = [f"[mcp_servers.{SERVER}]"]
    for key, value in entry.items():
        rendered = "true" if value is True else json.dumps(value, ensure_ascii=False)
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines) + "\n"


def _manifest_path(root: Path) -> Path:
    return root / ".agent-data" / "client-registrations.json"


def _load_manifest(root: Path) -> dict[str, Any]:
    path = _manifest_path(root)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    if not isinstance(data, dict):
        raise ValueError("Registration record is malformed")
    if any(not isinstance(key, str) or not isinstance(value, dict) for key, value in data.items()):
        raise ValueError("Registration record contains a malformed entry")
    return data


def _atomic_write(path: Path, content: str, *, expected: bytes | None, backup: bool = True) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    old = path.read_bytes() if path.exists() else None
    if old != expected:
        raise RuntimeError("Configuration changed since it was read; retry after reviewing it")
    backup_path = None
    if old is not None and backup:
        backup_path = path.with_name(path.name + ".backup-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"))
        shutil.copy2(path, backup_path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + "-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        current = path.read_bytes() if path.exists() else None
        if current != expected:
            raise RuntimeError("Configuration changed while registering; retry after reviewing it")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return str(backup_path) if backup_path else None


def _result(client: str, path: Path, status: str, **extra: Any) -> dict[str, Any]:
    if client in ADAPTERS and ADAPTERS[client].note:
        extra.setdefault("note", ADAPTERS[client].note)
    return {"client": client, "path": str(path), "status": status, **extra}


def discover(clients: tuple[str, ...], scope: str, directory: Path, home: Path, *, uninstall: bool = False,
             root: Path | None = None, dsh_home: Path | None = None) -> list[dict[str, Any]]:
    root = (root or ROOT).resolve()
    owned = _load_manifest(root) if uninstall else {}
    found = []
    for client in clients:
        try:
            path = config_path(client, scope, directory, home, dsh_home=dsh_home)
        except FileNotFoundError as exc:
            found.append(_result(client, directory if scope == "project" else home, "not_found", reason=str(exc)))
            continue
        except ValueError as exc:
            if client == "dsh" and "Multiple DSH" in str(exc):
                import dsh_adapter
                homes = dsh_adapter.candidate_homes(scope, directory, home)
                found.append(_result(client, home, "detected", requires_instance=True,
                                     candidates=[str(item) for item in homes], reason=str(exc)))
            else:
                status = "unsupported_scope" if "no documented" in str(exc).lower() else "error"
                found.append(_result(client, directory if scope == "project" else home,
                                     status, reason=str(exc)))
            continue
        if client == "claude" and scope == "user" and os.environ.get("CLAUDE_CONFIG_DIR"):
            status = "unsupported_override"
        elif uninstall:
            status = "detected" if str(path) in owned else "not_owned"
        else:
            status = "detected" if detect_client(client, path, home) else "not_found"
        found.append(_result(client, path, status))
    return found


def configure(
    *, clients: tuple[str, ...] = CLIENTS, scope: str = "user", directory: Path | None = None,
    workspace: Path | None = None, dry_run: bool = False, uninstall: bool = False,
    root: Path | None = None, home: Path | None = None, dsh_home: Path | None = None,
) -> list[dict[str, Any]]:
    root = (root or ROOT).resolve()
    directory = (directory or root).resolve()
    home = (home or Path.home()).resolve()
    workspace = (workspace or root / ".agent-data").resolve()
    receipt_path = _manifest_path(root)
    try:
        receipt_expected = receipt_path.read_bytes() if receipt_path.exists() else None
        manifest = _load_manifest(root)
    except (OSError, ValueError) as exc:
        return [_result(client, directory if scope == "project" else home, "error",
                        reason=f"Registration record cannot be read: {exc}") for client in clients]
    results: list[dict[str, Any]] = []
    runtime: Path | None = None
    for client in clients:
        try:
            path = config_path(client, scope, directory, home, dsh_home=dsh_home)
        except FileNotFoundError as exc:
            results.append(_result(client, directory if scope == "project" else home, "not_found", reason=str(exc)))
            continue
        except ValueError as exc:
            results.append(_result(client, directory if scope == "project" else home, "error", reason=str(exc)))
            continue
        if client == "claude" and scope == "user" and os.environ.get("CLAUDE_CONFIG_DIR"):
            results.append(_result(client, path, "error", reason="CLAUDE_CONFIG_DIR is set; user settings location is not established. Use --scope project or configure Claude manually"))
            continue
        if not uninstall and not detect_client(client, path, home):
            results.append(_result(client, path, "not_found", reason="No CLI or existing client configuration found"))
            continue
        if uninstall and str(path) not in manifest:
            results.append(_result(client, path, "not_owned", reason="No registration record for this checkout"))
            continue
        try:
            original_bytes = path.read_bytes() if path.exists() else None
            source = original_bytes.decode("utf-8-sig") if original_bytes is not None else ""
            if client == "dsh":
                import dsh_adapter
                current = dsh_adapter.current_entry(path, source)
                if uninstall:
                    record = manifest[str(path)]
                    if current != record.get("entry"):
                        results.append(_result(client, path, "conflict", reason="Registered DSH MCP row has changed"))
                        continue
                    updated = dsh_adapter.render_remove(path, source,
                                                        {**record["entry"], "_owned_block": record.get("owned_block")})
                    updated_manifest = dict(manifest)
                    del updated_manifest[str(path)]
                    status = "removed"
                else:
                    if runtime is None:
                        runtime = choose_python(root)
                    entry = server_entry(client, runtime, root, workspace)
                    if current == entry:
                        results.append(_result(client, path, "unchanged"))
                        continue
                    if current is not None:
                        results.append(_result(client, path, "conflict", reason="Same-name DSH MCP row differs"))
                        continue
                    updated = dsh_adapter.render_add(path, source, entry["command"], entry["args"], entry["cwd"])
                    updated_manifest = dict(manifest)
                    updated_manifest[str(path)] = {"client": client, "entry": entry,
                                                   "owned_block": dsh_adapter.owned_block(updated)}
                    status = "configured"
                if dry_run:
                    results.append(_result(client, path, "would_remove" if uninstall else "would_configure"))
                    continue
                backup = _atomic_write(path, updated, expected=original_bytes)
                manifest = updated_manifest
                try:
                    receipt_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
                    _atomic_write(receipt_path, receipt_text, expected=receipt_expected, backup=False)
                    receipt_expected = receipt_text.encode("utf-8")
                except (OSError, RuntimeError) as exc:
                    results.append(_result(client, path, "partial", backup=backup,
                                           reason=f"DSH profile changed but registration record update failed: {exc}; restore backup or review row manually"))
                    break
                results.append(_result(client, path, status, backup=backup))
                continue
            data = load_config(client, source, path)
            keys = _section_path(client, path)
            key = keys if client in ADAPTERS and ADAPTERS[client].kind == "jsonc" else keys[0]
            current = _section(data, keys).get(SERVER)
            if uninstall:
                record = manifest[str(path)]
                if current != record.get("entry"):
                    results.append(_result(client, path, "conflict", reason="Registered entry has changed; remove it manually after review"))
                    continue
                if client in ("cursor", "claude") or client in ADAPTERS:
                    import jsonc_config
                    if record.get("entry_text") != jsonc_config.entry_text(source, key):
                        results.append(_result(client, path, "conflict", reason="Registered entry text has changed; remove it manually after review"))
                        continue
                if client in ADAPTERS and ADAPTERS[client].kind == "jsonc":
                    import jsonc_config
                    updated = jsonc_config.remove(source, key)
                elif client in ("cursor", "claude") or client in ADAPTERS:
                    del data[key][SERVER]
                    if not data[key]:
                        del data[key]
                    updated = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
                else:
                    block = record.get("block", "")
                    if not block or source.count(block) != 1:
                        results.append(_result(client, path, "conflict", reason="Registered TOML block has changed; remove it manually after review"))
                        continue
                    updated = source.replace(block, "", 1)
                load_config(client, updated, path)
                if not dry_run:
                    backup = _atomic_write(path, updated, expected=original_bytes)
                    del manifest[str(path)]
                    try:
                        receipt_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
                        _atomic_write(receipt_path, receipt_text, expected=receipt_expected, backup=False)
                        receipt_expected = receipt_text.encode("utf-8")
                    except (OSError, RuntimeError) as exc:
                        results.append(_result(client, path, "partial", backup=backup,
                                               reason=f"Server removed but registration record update failed: {exc}; restore from backup or review the record manually"))
                        break
                else:
                    backup = None
                results.append(_result(client, path, "would_remove" if dry_run else "removed", backup=backup))
                continue
            if runtime is None:
                runtime = choose_python(root)
            entry = server_entry(client, runtime, root, workspace)
            if current == entry:
                results.append(_result(client, path, "unchanged"))
                continue
            if current is not None:
                results.append(_result(client, path, "conflict", reason="Same-name server differs; existing entry was preserved"))
                continue
            if client in ADAPTERS and ADAPTERS[client].kind == "jsonc":
                import jsonc_config
                updated = jsonc_config.insert(source, key, entry)
                block = None
            elif client in ("cursor", "claude") or client in ADAPTERS:
                data.setdefault(key, {})[SERVER] = entry
                updated = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
                block = None
            else:
                block = ("\n" if source and not source.endswith("\n") else "") + "\n" + BEGIN + _toml_entry(entry) + END
                updated = source + block
            load_config(client, updated, path)
            if not dry_run:
                backup = _atomic_write(path, updated, expected=original_bytes)
                record = {"client": client, "entry": entry, "block": block}
                if client in ("cursor", "claude") or client in ADAPTERS:
                    import jsonc_config
                    record["entry_text"] = jsonc_config.entry_text(updated, key)
                manifest[str(path)] = record
                try:
                    receipt_text = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
                    _atomic_write(receipt_path, receipt_text, expected=receipt_expected, backup=False)
                    receipt_expected = receipt_text.encode("utf-8")
                except (OSError, RuntimeError) as exc:
                    results.append(_result(client, path, "partial", backup=backup,
                                           reason=f"Server configured but registration record update failed: {exc}; restore from backup or remove this server entry manually"))
                    break
            else:
                backup = None
            results.append(_result(client, path, "would_configure" if dry_run else "configured", backup=backup))
        except (OSError, RuntimeError, ValueError, tomllib.TOMLDecodeError) as exc:
            results.append(_result(client, path, "error", reason=str(exc)))
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", nargs="+", choices=CLIENTS)
    parser.add_argument("--scope", choices=("user", "project"), default="user")
    parser.add_argument("--directory", type=Path, help="Project directory for --scope project")
    parser.add_argument("--workspace", type=Path, help="RepoWayfinder task data directory")
    parser.add_argument("--dsh-home", type=Path, help="Explicit DSH web-profile harness home")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--uninstall", action="store_true", help="Remove only unchanged entries created by this script")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    selected = tuple(dict.fromkeys(args.clients)) if args.clients else CLIENTS
    discovery = []
    if not args.clients:
        try:
            discovery = discover(CLIENTS, args.scope, (args.directory or ROOT).resolve(), Path.home().resolve(),
                                 uninstall=args.uninstall, dsh_home=args.dsh_home)
        except (OSError, ValueError) as exc:
            output = {"status": "error", "reason": f"Cannot inspect registration record: {exc}"}
            print(json.dumps(output, ensure_ascii=False) if args.json else output["reason"])
            return 1
        choices = [item for item in discovery if item["status"] == "detected"]
        if args.dry_run:
            selected = tuple(item["client"] for item in choices if not item.get("requires_instance"))
        elif len(choices) > 1:
            if not sys.stdin.isatty():
                output = {"status": "selection_required", "scope": args.scope, "detected": discovery,
                          "reason": "Specify --clients with one or more detected clients"}
                print(json.dumps(output, ensure_ascii=False) if args.json else
                      "发现多个客户端。请用 --clients 指定要接入的客户端：" + ", ".join(item["client"] for item in choices))
                return 2
            print("选择要" + ("移除接入" if args.uninstall else "接入") +
                  f"的客户端（{'项目' if args.scope == 'project' else '用户'}配置）：", file=sys.stderr)
            for index, item in enumerate(choices, 1):
                print(f"  {index}. {DISPLAY[item['client']]} — {item['path']}", file=sys.stderr)
            print("输入编号，如 1,3；all 全选；q 退出：", end="", file=sys.stderr, flush=True)
            answer = sys.stdin.readline().strip().lower()
            if answer in ("", "q", "quit"):
                output = {"status": "selection_cancelled", "detected": discovery}
                print(json.dumps(output, ensure_ascii=False) if args.json else "未修改配置。")
                return 0
            if answer == "all":
                selected = tuple(item["client"] for item in choices)
            else:
                try:
                    numbers = [int(part.strip()) for part in answer.split(",")]
                    if not numbers or any(number < 1 or number > len(choices) for number in numbers):
                        raise ValueError("Selection number is out of range")
                    selected = tuple(dict.fromkeys(choices[number - 1]["client"] for number in numbers))
                except ValueError:
                    output = {"status": "selection_invalid", "detected": discovery}
                    print(json.dumps(output, ensure_ascii=False) if args.json else "编号无效，未修改配置。")
                    return 2
        elif not args.dry_run:
            selected = tuple(item["client"] for item in choices)
            if not selected:
                output = {"status": "no_clients_found", "scope": args.scope, "detected": discovery}
                print(json.dumps(output, ensure_ascii=False) if args.json else "此范围内未发现可接入的客户端。")
                return 1
    if "dsh" in selected and args.dsh_home is None and not args.dry_run:
        import dsh_adapter
        homes = dsh_adapter.candidate_homes(args.scope, (args.directory or ROOT).resolve(), Path.home().resolve())
        if len(homes) > 1:
            if not sys.stdin.isatty():
                output = {"status": "selection_required", "client": "dsh", "candidates": [str(item) for item in homes],
                          "reason": "Choose a DSH instance with --dsh-home"}
                print(json.dumps(output, ensure_ascii=False) if args.json else
                      "发现多个 DSH 配置。请用 --dsh-home 指定其中一个：\n" + "\n".join(str(item) for item in homes))
                return 2
            print("选择要接入的 DSH 配置：", file=sys.stderr)
            for index, candidate in enumerate(homes, 1):
                print(f"  {index}. {candidate}", file=sys.stderr)
            print("输入一个编号，或 q 退出：", end="", file=sys.stderr, flush=True)
            answer = sys.stdin.readline().strip().lower()
            if answer in ("", "q", "quit"):
                output = {"status": "selection_cancelled", "client": "dsh", "candidates": [str(item) for item in homes]}
                print(json.dumps(output, ensure_ascii=False) if args.json else "未修改配置。")
                return 0
            try:
                number = int(answer)
                args.dsh_home = homes[number - 1] if 1 <= number <= len(homes) else None
            except ValueError:
                args.dsh_home = None
            if args.dsh_home is None:
                output = {"status": "selection_invalid", "client": "dsh", "candidates": [str(item) for item in homes]}
                print(json.dumps(output, ensure_ascii=False) if args.json else "DSH 编号无效，未修改配置。")
                return 2
    results = configure(clients=selected, scope=args.scope, directory=args.directory,
                        workspace=args.workspace, dry_run=args.dry_run, uninstall=args.uninstall,
                        dsh_home=args.dsh_home)
    if args.json:
        print(json.dumps({"scope": args.scope, "detected": discovery, "results": results}, ensure_ascii=False))
    else:
        if discovery and args.dry_run:
            for item in discovery:
                print(f"{item['client']}: {item['status']} — {item['path']}")
                if item.get("candidates"):
                    for candidate in item["candidates"]:
                        print(f"  DSH profile: {candidate}")
        for item in results:
            print(f"{item['client']}: {item['status']} — {item['path']}" + (f" ({item['reason']})" if "reason" in item else ""))
    return 1 if any(item["status"] in ("conflict", "error", "partial") for item in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
