"""Small MCP stdio transport for RepoWayfinder Agent.

Each input and output line is one JSON-RPC message.  The service owns repository
work and job state; this module only handles protocol and argument validation.
"""

from __future__ import annotations

from contextlib import redirect_stdout
import json
import math
import re
import sys
from typing import Any, TextIO

from agent_service import (MAX_COMMANDS, MAX_COMMAND_CHARS, MAX_CHECKS, MAX_EXPECTED_CHARS,
                           MAX_FILES, MAX_FILE_PATH_CHARS, MAX_INPUT_BYTES, MAX_REQUEST_ID_CHARS,
                           utf8_bytes)


LATEST_PROTOCOL = "2025-06-18"
SUPPORTED_PROTOCOLS = {"2024-11-05", "2025-03-26", LATEST_PROTOCOL}
SERVER_INFO = {"name": "repowayfinder-agent", "version": "0.5.0"}


def _object(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required), "additionalProperties": False}


def _string(**constraints: Any) -> dict[str, Any]:
    return {"type": "string", **constraints}


_PLAN_STEP = _object(
    {"type": {"type": "string", "enum": ["exec", "shell"]},
     "cmd": _string(minLength=1, maxLength=MAX_COMMAND_CHARS), "purpose": _string(),
     "timeout": {"type": "integer", "minimum": 1, "maximum": 600}},
    ("type", "cmd", "purpose", "timeout"),
)
_PLAN = _object(
    {"action": {"type": "string", "enum": ["DEPLOY"]},
     "steps": {"type": "array", "items": _PLAN_STEP, "minItems": 1, "maxItems": MAX_COMMANDS}, "reason": _string()},
    ("action", "steps", "reason"),
)
_CHECK = _object(
    {"type": {"type": "string", "enum": ["file_exists", "file_contains", "stdout_contains", "json_value"]},
     "path": _string(), "expected": {"type": ["string", "boolean", "number", "null"], "maxLength": MAX_EXPECTED_CHARS},
     "pointer": _string(pattern="^(?:/.*)?$"),
     "freshness": {"type": "string", "enum": ["fresh", "preserved"], "default": "fresh",
                   "description": "fresh requires current-run output. preserved explicitly keeps an earlier verified artifact in this job, including across input changes; requires identical SHA-256 and retains its original producing attempt. Use for prior snapshots, not results that must be recomputed."}},
    ("type",),
)
_CHECKS = {"type": "array", "items": _CHECK, "maxItems": MAX_CHECKS,
           "description": "Assertions on current output; explicitly mark retained prior artifacts freshness=preserved. Unverified old files cannot pass. contains expected text must be nonempty. Content reads are limited to 20 MiB per file."}
_FILES = {"type": "array", "maxItems": MAX_FILES, "items": _object(
    {"path": _string(minLength=1, maxLength=MAX_FILE_PATH_CHARS),
     "content": _string(maxLength=MAX_INPUT_BYTES)}, ("path", "content")),
    "description": "New UTF-8 task files, relative paths; combined content <=256 KiB. Existing files are never overwritten."}
_COMMANDS = {"type": "array", "items": _string(minLength=1, maxLength=MAX_COMMAND_CHARS),
             "minItems": 1, "maxItems": MAX_COMMANDS}
_EDITS = {"type": "array", "maxItems": MAX_FILES, "items": _object(
    {"path": _string(minLength=1, maxLength=MAX_FILE_PATH_CHARS),
     "old": _string(minLength=1, maxLength=MAX_INPUT_BYTES), "new": _string(maxLength=MAX_INPUT_BYTES)},
    ("path", "old", "new")), "description": "Exact replacements in existing untracked task files. old must occur once. Combined old+new <=256 KiB; entire batch is validated before writing."}
_JOB = _object({"job_id": _string(minLength=1)}, ("job_id",))
_PYTHON_VERSION = _string(pattern=r"^3\.(?:0|[1-9][0-9]*)$",
                          description="Optional job Python major.minor version. Must agree with repository .python-version; fixed across recovery. Use python in commands, not an explicit interpreter path.")
_PREPARE_ARGS = _object({"repository": _string(minLength=1),
                        "revision": _string(pattern="^[0-9a-fA-F]{40}$"),
                        "plan": _PLAN, "checks": _CHECKS,
                        "python_version": _PYTHON_VERSION,
                        "request_id": _string(minLength=1, maxLength=MAX_REQUEST_ID_CHARS)}, ("repository",))
_RUN_ARGS = _object({"repository": _string(minLength=1),
                     "files": _FILES,
                     "commands": _COMMANDS,
                     "revision": _string(pattern="^[0-9a-fA-F]{40}$"),
                     "checks": _CHECKS,
                     "python_version": _PYTHON_VERSION,
                     "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "default": 300},
                     "request_id": _string(minLength=1, maxLength=MAX_REQUEST_ID_CHARS)}, ("repository", "commands"))


TOOLS: tuple[dict[str, Any], ...] = (
    {"name": "rw_search", "description": "Search public GitHub repositories and return candidates; no target code runs.",
     "inputSchema": _object({"query": _string(minLength=1, maxLength=300), "limit": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5}}, ("query",)),
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True}},
    {"name": "rw_prepare", "description": "Fetch and analyze a repository, then create a proposed job. Target code does not run.",
     "inputSchema": _PREPARE_ARGS,
     "annotations": {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}},
    {"name": "rw_run", "description": "Fetch source, stage new UTF-8 files, execute commands from the repository root and check results. Submit config/documents directly in files; use small scripts only for necessary glue. Python/pip steps or an explicit python_version create/reuse the job virtual environment; skip venv bootstrap/activation. Inside scripts use sys.executable for Python children and -m pip, not bare python; resolve other child CLIs with shutil.which. Shell-only plans without python_version do not create a Python venv. python/pip and project-declared CLIs and installed executables work. Use an explicit shell for builtins, not unquoted pipes/redirects. Check paths are relative to the repository; checks inspect this run's output; freshness=preserved explicitly retains an earlier verified artifact with identical digest, never unverified inputs. Waits up to 50 seconds; if running use rw_status. On failure reuse the job with rw_replan edits/commands and execute=true, do not recreate it. No nested model call; long-running servers are not kept alive.",
     "inputSchema": _RUN_ARGS,
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}},
    {"name": "rw_execute", "description": "Run a prepared job's approved plan. A successful command alone does not verify the task.",
     "inputSchema": _JOB,
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}},
    {"name": "rw_replan", "description": "Repair or continue a stopped job, reusing its checkout and installed environment. Provide exact edits and/or new files; commands replaces only the steps to run (omit to reuse the saved plan). Set execute=true to validate and execute in this call, waiting up to 50 seconds. Otherwise stops prepared for rw_execute. checks replace current assertions when supplied; omitted checks are reused. Checks default to current-run output. freshness=preserved explicitly retains earlier verified artifacts with identical digest and original producing attempt. Prior plans, changed file contents and run results remain in history. Use commands or legacy plan, never both.",
     "inputSchema": _object({"job_id": _string(minLength=1), "plan": _PLAN,
                             "commands": _COMMANDS, "files": _FILES, "edits": _EDITS,
                             "checks": _CHECKS, "execute": {"type": "boolean", "default": False},
                             "request_id": _string(minLength=1, maxLength=MAX_REQUEST_ID_CHARS),
                             "timeout": {"type": "integer", "minimum": 1, "maximum": 600, "default": 300}}, ("job_id",)),
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}},
    {"name": "rw_status", "description": "Read job state; use wait_seconds up to 50 (typically 30) instead of busy polling. Waiting or blocked is not success.",
     "inputSchema": _object({"job_id": _string(minLength=1),
                             "wait_seconds": {"type": "number", "minimum": 0, "maximum": 50, "default": 0}}, ("job_id",)),
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "rw_resume", "description": "Resume a waiting or interrupted job when its prerequisites are ready.",
     "inputSchema": _JOB,
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}},
    {"name": "rw_logs", "description": "Read a bounded excerpt of a job's diagnostic log.",
     "inputSchema": _object({"job_id": _string(minLength=1),
                             "max_chars": {"type": "integer", "minimum": 1, "maximum": 6000, "default": 2000}}, ("job_id",)),
     "annotations": {"readOnlyHint": True, "destructiveHint": False, "openWorldHint": False}},
    {"name": "rw_cancel", "description": "Request cancellation of a prepared or running job.",
     "inputSchema": _JOB,
     "annotations": {"readOnlyHint": False, "destructiveHint": True, "openWorldHint": True}},
)
_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _type_matches(value: Any, kind: str) -> bool:
    return {
        "object": lambda: isinstance(value, dict),
        "array": lambda: isinstance(value, list),
        "string": lambda: isinstance(value, str),
        "integer": lambda: isinstance(value, int) and not isinstance(value, bool),
        "number": lambda: isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": lambda: isinstance(value, bool),
        "null": lambda: value is None,
    }[kind]()


def _validate(value: Any, schema: dict[str, Any], location: str = "arguments") -> None:
    kinds = schema["type"]
    if isinstance(kinds, str):
        kinds = [kinds]
    if not any(_type_matches(value, kind) for kind in kinds):
        raise ValueError(f"{location} must be {', '.join(kinds)}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{location} must be one of {schema['enum']}")
    if isinstance(value, dict):
        props = schema.get("properties", {})
        missing = [key for key in schema.get("required", ()) if key not in value]
        if missing:
            raise ValueError(f"{location}.{missing[0]} is required")
        if schema.get("additionalProperties") is False:
            extra = value.keys() - props.keys()
            if extra:
                raise ValueError(f"{location}.{sorted(extra)[0]} is not allowed")
        for key, item in value.items():
            if key in props:
                _validate(item, props[key], f"{location}.{key}")
    elif isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            raise ValueError(f"{location} has too few items")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"{location} has too many items")
        for index, item in enumerate(value):
            _validate(item, schema["items"], f"{location}[{index}]")
    elif isinstance(value, str):
        utf8_bytes(value, location)
        if len(value) < schema.get("minLength", 0):
            raise ValueError(f"{location} is too short")
        if len(value) > schema.get("maxLength", float('inf')):
            raise ValueError(f"{location} exceeds {schema['maxLength']} characters")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            raise ValueError(f"{location} has an invalid format")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError(f"{location} must be finite")
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"{location} is below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"{location} is above {schema['maximum']}")


def _error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _result(request_id: Any, value: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": value}


def _tool_result(value: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=False, allow_nan=False)}],
            "isError": is_error}


def _handle(message: Any, service: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return _error(None, -32600, "Invalid Request")
    request_id = message.get("id")
    if message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str) or isinstance(request_id, (dict, list, bool)):
        return _error(request_id if isinstance(request_id, (str, int, float)) else None, -32600, "Invalid Request")
    if "id" not in message:
        return None  # JSON-RPC notifications never receive a response.
    method = message["method"]
    params = message.get("params", {})
    if not isinstance(params, dict):
        return _error(request_id, -32602, "Invalid params")
    if method == "initialize":
        proposed = params.get("protocolVersion")
        if not isinstance(proposed, str):
            return _error(request_id, -32602, "protocolVersion must be a string")
        version = proposed if proposed in SUPPORTED_PROTOCOLS else LATEST_PROTOCOL
        return _result(request_id, {"protocolVersion": version, "capabilities": {"tools": {}}, "serverInfo": SERVER_INFO})
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        if not isinstance(name, str):
            return _error(request_id, -32602, "Tool name must be a string")
        if name not in _TOOLS_BY_NAME:
            return _result(request_id, _tool_result({"error": f"Unknown tool: {name}"}, is_error=True))
        arguments = params.get("arguments", {})
        try:
            _validate(arguments, _TOOLS_BY_NAME[name]["inputSchema"])
        except ValueError as exc:
            return _error(request_id, -32602, str(exc))
        try:
            # Libraries used by the service may print; stdout belongs solely to MCP.
            with redirect_stdout(sys.stderr):
                value = service.dispatch(name, arguments)
            if not isinstance(value, dict):
                raise TypeError("Service result must be an object")
            return _result(request_id, _tool_result(value, is_error=value.get("isError") is True))
        except Exception as exc:
            return _result(request_id, _tool_result({"error": type(exc).__name__, "message": str(exc)[:2000]}, is_error=True))
    return _error(request_id, -32601, "Method not found")


def serve(service: Any, input_stream: TextIO | None = None, output_stream: TextIO | None = None) -> None:
    """Serve sequential MCP requests until EOF; write protocol JSON only to stdout."""
    # MCP stdio is UTF-8 on every OS. Windows' inherited ANSI stdin codec can
    # corrupt valid CJK/emoji into surrogate escapes before JSON validation.
    # Configure the real transport here; StringIO unit fixtures cannot catch it.
    if input_stream is None and hasattr(sys.stdin, 'reconfigure'):
        sys.stdin.reconfigure(encoding='utf-8', errors='strict')
    if output_stream is None and hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8', errors='strict')
    source = input_stream if input_stream is not None else sys.stdin
    target = output_stream if output_stream is not None else sys.stdout
    for line in source:
        try:
            message = json.loads(line, parse_constant=lambda token: (_ for _ in ()).throw(ValueError('Nonstandard JSON number')))
        except (json.JSONDecodeError, ValueError):
            response = _error(None, -32700, "Parse error")
        else:
            response = _handle(message, service)
        if response is not None:
            target.write(json.dumps(response, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n")
            target.flush()
