"""Minimal lossless JSONC edits for one MCP server entry.

Only the requested member and, when needed, a neighboring comma are changed.
Comments and formatting elsewhere remain byte-for-byte intact.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any


@dataclass(frozen=True)
class _Token:
    kind: str
    value: Any
    start: int
    end: int


@dataclass(frozen=True)
class _Member:
    key: str
    start: int
    end: int
    comma: _Token | None
    value: "_Object | None"


@dataclass(frozen=True)
class _Object:
    opening: _Token
    closing: _Token
    members: tuple[_Member, ...]

    def member(self, key: str) -> _Member | None:
        return next((item for item in self.members if item.key == key), None)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _lex(source: str) -> tuple[list[_Token], str]:
    tokens: list[_Token] = []
    cleaned = list(source)
    i = 0
    while i < len(source):
        c = source[i]
        if c.isspace():
            i += 1
            continue
        if source.startswith("//", i):
            end = source.find("\n", i + 2)
            end = len(source) if end < 0 else end
            for j in range(i, end):
                cleaned[j] = " "
            i = end
            continue
        if source.startswith("/*", i):
            end = source.find("*/", i + 2)
            if end < 0:
                raise ValueError("Unterminated JSONC block comment")
            end += 2
            for j in range(i, end):
                if cleaned[j] not in "\r\n":
                    cleaned[j] = " "
            i = end
            continue
        if c in "{}[]:,":
            tokens.append(_Token(c, c, i, i + 1))
            i += 1
            continue
        if c == '"':
            start = i
            i += 1
            escaped = False
            while i < len(source):
                if escaped:
                    escaped = False
                elif source[i] == "\\":
                    escaped = True
                elif source[i] == '"':
                    i += 1
                    break
                i += 1
            else:
                raise ValueError("Unterminated JSON string")
            tokens.append(_Token("string", json.loads(source[start:i]), start, i))
            continue
        start = i
        while i < len(source) and not source[i].isspace() and source[i] not in '{}[]:,"':
            if source.startswith("//", i) or source.startswith("/*", i):
                break
            i += 1
        if i == start:
            raise ValueError(f"Unexpected JSONC character at offset {i}")
        tokens.append(_Token("literal", source[start:i], start, i))
    # JSONC permits trailing commas; mask only those immediately before a close.
    for position, token in enumerate(tokens[:-1]):
        if token.kind == "," and tokens[position + 1].kind in ("}", "]"):
            cleaned[token.start] = " "
    return tokens, "".join(cleaned)


def loads(source: str) -> dict[str, Any]:
    """Parse JSON or JSONC, rejecting duplicate keys and non-object roots."""
    _, clean = _lex(source or "{}")
    result = json.loads(clean, object_pairs_hook=_unique_object)
    if not isinstance(result, dict):
        raise ValueError("Configuration root must be an object")
    return result


class _Parser:
    def __init__(self, tokens: list[_Token]):
        self.tokens = tokens
        self.i = 0

    def take(self, kind: str) -> _Token:
        token = self.tokens[self.i]
        if token.kind != kind:
            raise ValueError(f"Expected {kind} at offset {token.start}")
        self.i += 1
        return token

    def value(self) -> _Object | None:
        token = self.tokens[self.i]
        if token.kind == "{":
            return self.object()
        if token.kind == "[":
            self.take("[")
            while self.tokens[self.i].kind != "]":
                self.value()
                if self.tokens[self.i].kind == ",":
                    self.i += 1
                else:
                    break
            self.take("]")
            return None
        if token.kind not in ("string", "literal"):
            raise ValueError(f"Unexpected JSONC token at offset {token.start}")
        self.i += 1
        return None

    def object(self) -> _Object:
        opening = self.take("{")
        members: list[_Member] = []
        while self.tokens[self.i].kind != "}":
            key = self.take("string")
            self.take(":")
            value = self.value()
            end = self.tokens[self.i - 1].end
            comma = None
            if self.tokens[self.i].kind == ",":
                comma = self.take(",")
            members.append(_Member(key.value, key.start, end, comma, value))
            if comma is None:
                break
        closing = self.take("}")
        return _Object(opening, closing, tuple(members))


def _tree(source: str) -> _Object:
    tokens, clean = _lex(source)
    json.loads(clean, object_pairs_hook=_unique_object)
    if not tokens or tokens[0].kind != "{":
        raise ValueError("Configuration root must be an object")
    parser = _Parser(tokens)
    root = parser.object()
    if parser.i != len(tokens):
        raise ValueError("Unexpected content after configuration root")
    return root


def _indent(source: str, obj: _Object) -> tuple[str, str, str]:
    newline = "\r\n" if "\r\n" in source else "\n"
    line_start = source.rfind("\n", 0, obj.opening.start) + 1
    before = source[line_start:obj.opening.start]
    base = before if before.isspace() or not before else ""
    if obj.members:
        first = obj.members[0]
        line_start = source.rfind("\n", 0, first.start) + 1
        candidate = source[line_start:first.start]
        child = candidate if candidate.isspace() and len(candidate) > len(base) else base + "  "
    else:
        child = base + "  "
    return newline, base, child


def _add_member(source: str, obj: _Object, key: str, value: Any) -> str:
    newline, base, child = _indent(source, obj)
    encoded = json.dumps(value, ensure_ascii=False, indent=2)
    encoded = encoded.replace("\n", newline + child)
    item = json.dumps(key, ensure_ascii=False) + ": " + encoded
    close_line = source.rfind("\n", 0, obj.closing.start) + 1
    multiline_close = close_line > obj.opening.start and source[close_line:obj.closing.start].strip() == ""
    at = close_line if multiline_close else obj.closing.start
    prefix = child + item + newline if multiline_close else newline + child + item + newline + base
    result = source[:at] + prefix + source[at:]
    if obj.members and obj.members[-1].comma is None:
        # Put the separator before trailing comments so they stay untouched.
        last_end = obj.members[-1].end
        result = result[:last_end] + "," + result[last_end:]
    return result


def _path(section: str | tuple[str, ...]) -> tuple[str, ...]:
    parts = (section,) if isinstance(section, str) else section
    if not parts or any(not isinstance(part, str) or not part for part in parts):
        raise ValueError("MCP server section path is invalid")
    return parts


def _section(root: _Object, parts: tuple[str, ...]) -> _Object | None:
    current = root
    for key in parts:
        member = current.member(key)
        if member is None:
            return None
        if member.value is None:
            raise ValueError(f"{key} must be an object")
        current = member.value
    return current


def insert(source: str, section: str | tuple[str, ...], entry: dict[str, Any]) -> str:
    """Insert section.repo_wayfinder without rewriting any other JSONC text."""
    source = source or "{}"
    root = _tree(source)
    parts = _path(section)
    current = root
    for index, key in enumerate(parts):
        member = current.member(key)
        if member is None:
            nested: dict[str, Any] = {"repo_wayfinder": entry}
            for rest in reversed(parts[index + 1:]):
                nested = {rest: nested}
            return _add_member(source, current, key, nested)
        if member.value is None:
            raise ValueError(f"{key} must be an object")
        current = member.value
    if current.member("repo_wayfinder") is not None:
        raise ValueError("Same-name server already exists")
    return _add_member(source, current, "repo_wayfinder", entry)


def remove(source: str, section: str | tuple[str, ...]) -> str:
    """Remove section.repo_wayfinder, retaining all other members and comments."""
    root = _tree(source)
    parent = _section(root, _path(section))
    if parent is None:
        raise ValueError("MCP server section is missing or malformed")
    members = parent.members
    index = next((i for i, item in enumerate(members) if item.key == "repo_wayfinder"), None)
    if index is None:
        raise ValueError("repo_wayfinder entry is missing")
    item = members[index]
    edits: list[tuple[int, int]] = [(item.start, item.end)]
    if item.comma is not None:
        edits.append((item.comma.start, item.comma.end))
    if index == len(members) - 1 and index > 0 and members[index - 1].comma is not None:
        edits.append((members[index - 1].comma.start, members[index - 1].comma.end))
    for start, end in sorted(edits, reverse=True):
        source = source[:start] + source[end:]
    loads(source)
    return source


def entry_text(source: str, section: str | tuple[str, ...]) -> str:
    """Return the exact owned member text, including any comments inside it."""
    root = _tree(source)
    parent = _section(root, _path(section))
    if parent is None:
        raise ValueError("MCP server section is missing or malformed")
    item = parent.member("repo_wayfinder")
    if item is None:
        raise ValueError("repo_wayfinder entry is missing")
    return source[item.start:item.end]
