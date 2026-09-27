"""Focused registration safety checks using temporary client settings only."""
from __future__ import annotations

import json
import io
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch

import configure_clients as cc
import dsh_adapter
import jsonc_config


class ConfigureClientsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="Repo Wayfinder clients ")
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.root = self.base / "agent with spaces"
        self.root.mkdir()
        self.home = self.base / "home"
        self.home.mkdir()
        self.python = self.root / ".venv" / "Scripts" / "python.exe"
        self.detect = patch.object(cc, "detect_client", return_value=True)
        self.runtime = patch.object(cc, "choose_python", return_value=self.python)
        self.detect.start()
        self.runtime.start()
        self.addCleanup(self.detect.stop)
        self.addCleanup(self.runtime.stop)

    def run_config(self, **kwargs):
        return cc.configure(root=self.root, home=self.home, **kwargs)

    def test_toml_merge_backup_idempotency_and_uninstall(self):
        path = self.home / ".grok" / "config.toml"
        path.parent.mkdir()
        original = '# retained comment\nmodel = "user-model"\n[mcp_servers.another]\ncommand = "other"\n'
        path.write_text(original, encoding="utf-8")
        result = self.run_config(clients=("grok",))
        self.assertEqual(result[0]["status"], "configured")
        self.assertEqual(Path(result[0]["backup"]).read_text(encoding="utf-8"), original)
        text = path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original))
        parsed = tomllib.loads(text)
        self.assertEqual(parsed["model"], "user-model")
        self.assertEqual(parsed["mcp_servers"]["another"]["command"], "other")
        entry = parsed["mcp_servers"]["repo_wayfinder"]
        self.assertEqual(entry["command"], str(self.python))
        self.assertIn("agent with spaces", entry["args"][1])
        self.assertEqual(self.run_config(clients=("grok",))[0]["status"], "unchanged")
        self.assertEqual(path.read_text(encoding="utf-8"), text)
        self.assertEqual(self.run_config(clients=("grok",), uninstall=True)[0]["status"], "removed")
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_conflict_and_changed_entry_are_preserved(self):
        path = self.home / ".codex" / "config.toml"
        path.parent.mkdir()
        source = '[mcp_servers.repo_wayfinder]\ncommand = "someone-else"\n'
        path.write_text(source, encoding="utf-8")
        self.assertEqual(self.run_config(clients=("codex",))[0]["status"], "conflict")
        self.assertEqual(path.read_text(encoding="utf-8"), source)
        path.unlink()
        self.assertEqual(self.run_config(clients=("codex",))[0]["status"], "configured")
        edited = path.read_text(encoding="utf-8").replace("tool_timeout_sec = 75", "tool_timeout_sec = 70")
        path.write_text(edited, encoding="utf-8")
        self.assertEqual(self.run_config(clients=("codex",), uninstall=True)[0]["status"], "conflict")
        self.assertEqual(path.read_text(encoding="utf-8"), edited)

    def test_cursor_preserves_other_settings_and_rejects_duplicate_keys(self):
        path = self.home / ".cursor" / "mcp.json"
        path.parent.mkdir()
        original = '{"other": {"keep": 42}, "mcpServers": {"another": {"command": "other"}}}'
        path.write_text(original, encoding="utf-8")
        self.assertEqual(self.run_config(clients=("cursor",))[0]["status"], "configured")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["other"]["keep"], 42)
        self.assertEqual(data["mcpServers"]["another"]["command"], "other")
        self.assertNotIn("cwd", data["mcpServers"]["repo_wayfinder"])
        self.assertEqual(self.run_config(clients=("cursor",), uninstall=True)[0]["status"], "removed")
        self.assertEqual(json.loads(path.read_text(encoding="utf-8")), json.loads(original))
        broken = '{"mcpServers": {}, "mcpServers": {}}'
        path.write_text(broken, encoding="utf-8")
        self.assertEqual(self.run_config(clients=("cursor",))[0]["status"], "error")
        self.assertEqual(path.read_text(encoding="utf-8"), broken)

    def test_claude_user_json_and_project_path(self):
        path = self.home / ".claude.json"
        original = '{"theme": "dark", "mcpServers": {"other": {"type": "stdio", "command": "other"}}}'
        path.write_text(original, encoding="utf-8")
        self.assertEqual(self.run_config(clients=("claude",))[0]["status"], "configured")
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["theme"], "dark")
        self.assertEqual(data["mcpServers"]["other"]["command"], "other")
        entry = data["mcpServers"]["repo_wayfinder"]
        self.assertEqual(entry["type"], "stdio")
        self.assertNotIn("cwd", entry)
        project = self.base / "project"
        self.assertEqual(cc.config_path("claude", "project", project, self.home), project / ".mcp.json")
        self.assertEqual(self.run_config(clients=("claude",), uninstall=True)[0]["status"], "removed")

    def test_claude_config_override_is_not_guessed(self):
        with patch.dict(cc.os.environ, {"CLAUDE_CONFIG_DIR": str(self.base / "custom") }):
            result = self.run_config(clients=("claude",))
        self.assertEqual(result[0]["status"], "error")
        self.assertFalse((self.home / ".claude.json").exists())

    def test_mainstream_json_entries_round_trip_without_touching_neighbors(self):
        for client in ("gemini", "windsurf", "claude_desktop"):
            with self.subTest(client=client):
                path = cc.config_path(client, "user", self.root, self.home)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"other": 7, "mcpServers": {"existing": {"command": "keep"}}}', encoding="utf-8")
                self.assertEqual(self.run_config(clients=(client,))[0]["status"], "configured")
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["other"], 7)
                self.assertEqual(data["mcpServers"]["existing"]["command"], "keep")
                self.assertEqual(data["mcpServers"]["repo_wayfinder"]["command"], str(self.python))
                self.assertEqual(self.run_config(clients=(client,), uninstall=True)[0]["status"], "removed")

    def test_jsonc_clients_keep_comments_and_trailing_commas(self):
        for client, section in (("opencode", "mcp"), ("vscode", "servers")):
            with self.subTest(client=client):
                path = cc.config_path(client, "user", self.root, self.home)
                path.parent.mkdir(parents=True, exist_ok=True)
                original = ('// keep top\n{\n  // keep setting\n  "other": 7,\n'
                            f'  "{section}": {{\n    "existing": {{"command": "keep",}},\n  }},\n}}\n')
                path.write_text(original, encoding="utf-8")
                self.assertEqual(self.run_config(clients=(client,))[0]["status"], "configured")
                updated = path.read_text(encoding="utf-8")
                self.assertIn("// keep top", updated)
                self.assertIn("// keep setting", updated)
                data = jsonc_config.loads(updated)
                self.assertEqual(data["other"], 7)
                self.assertEqual(data[section]["existing"]["command"], "keep")
                self.assertIn("repo_wayfinder", data[section])
                self.assertEqual(self.run_config(clients=(client,), uninstall=True)[0]["status"], "removed")
                self.assertIn("// keep top", path.read_text(encoding="utf-8"))

    def test_opencode_v2_nested_mcp_section(self):
        path = cc.config_path("opencode", "user", self.root, self.home)
        path.parent.mkdir(parents=True, exist_ok=True)
        source = '{"mcp": {"servers": {"other": {"type": "local", "command": ["other"]}}}}'
        path.write_text(source, encoding="utf-8")
        self.assertEqual(self.run_config(clients=("opencode",))[0]["status"], "configured")
        data = jsonc_config.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["mcp"]["servers"]["other"]["command"], ["other"])
        self.assertIn("repo_wayfinder", data["mcp"]["servers"])
        self.assertEqual(self.run_config(clients=("opencode",), uninstall=True)[0]["status"], "removed")

    def test_damaged_toml_and_dry_run_leave_files_untouched(self):
        path = self.home / ".grok" / "config.toml"
        path.parent.mkdir()
        path.write_text("bad = [", encoding="utf-8")
        self.assertEqual(self.run_config(clients=("grok",))[0]["status"], "error")
        self.assertEqual(path.read_text(encoding="utf-8"), "bad = [")
        path.write_text("", encoding="utf-8")
        self.assertEqual(self.run_config(clients=("grok",), dry_run=True)[0]["status"], "would_configure")
        self.assertEqual(path.read_text(encoding="utf-8"), "")
        self.assertFalse(cc._manifest_path(self.root).exists())

    def test_bad_registration_record_is_reported_without_writing(self):
        receipt = cc._manifest_path(self.root)
        receipt.parent.mkdir()
        receipt.write_text("{bad", encoding="utf-8")
        result = self.run_config(clients=("codex",))
        self.assertEqual(result[0]["status"], "error")
        self.assertFalse((self.home / ".codex" / "config.toml").exists())

    def test_concurrent_change_is_preserved(self):
        path = self.home / ".grok" / "config.toml"
        path.parent.mkdir()
        path.write_text("# first\n", encoding="utf-8")
        original_write = cc._atomic_write

        def changed_before_write(target, content, **kwargs):
            if target == path:
                path.write_text("# changed\n", encoding="utf-8")
            return original_write(target, content, **kwargs)

        with patch.object(cc, "_atomic_write", side_effect=changed_before_write):
            result = self.run_config(clients=("grok",))
        self.assertEqual(result[0]["status"], "error")
        self.assertEqual(path.read_text(encoding="utf-8"), "# changed\n")

    def test_failed_receipt_reports_partial_write(self):
        original_write = cc._atomic_write

        def fail_receipt(path, content, **kwargs):
            if path == cc._manifest_path(self.root):
                raise OSError("disk full")
            return original_write(path, content, **kwargs)

        with patch.object(cc, "_atomic_write", side_effect=fail_receipt):
            result = self.run_config(clients=("grok",))
        self.assertEqual(result[0]["status"], "partial")
        self.assertIn("registration record update failed", result[0]["reason"])
        self.assertTrue((self.home / ".grok" / "config.toml").exists())

    def test_undetected_client_is_not_configured(self):
        with patch.object(cc, "detect_client", return_value=False):
            result = self.run_config(clients=("cursor",))
        self.assertEqual(result[0]["status"], "not_found")
        self.assertFalse((self.home / ".cursor" / "mcp.json").exists())

    def test_interpreter_prefers_usable_project_venv(self):
        self.runtime.stop()
        self.python.parent.mkdir(parents=True)
        self.python.touch()
        with patch.object(cc.subprocess, "run") as run:
            run.return_value.returncode = 0
            selected = cc.choose_python(self.root)
        self.assertEqual(selected, self.python)
        self.assertEqual(run.call_args.args[0][0], str(self.python))

    def test_dsh_nonempty_profile_round_trip_with_owned_block(self):
        bundle_path = os.environ.get("DSH_TEST_BUNDLE")
        if not bundle_path:
            self.skipTest("set DSH_TEST_BUNDLE to a validated DSH installation")
        bundle = Path(bundle_path)
        modules = bundle / "app" / "resources" / "app" / "node_modules"
        node = modules / "node" / "bin" / "node.exe"
        if not node.is_file():
            self.skipTest("local DSH parser unavailable")
        harness = self.base / "harness"
        web = harness / "profiles" / "web"
        web.mkdir(parents=True)
        (web / "package.json").write_text(json.dumps({"dsh": {"profile": {
            "bundles": ["@deepseek-ai/dsh-web-app"]}}}), encoding="utf-8")
        path = web / "cordis.patch.yml"
        source = "# keep\n- id: other\n  config: {x: 1}\n"
        path.write_text(source, encoding="utf-8")
        with patch.object(dsh_adapter, "_node_yaml", return_value=(node, modules / "yaml")):
            first = self.run_config(clients=("dsh",), dsh_home=harness)[0]
            self.assertEqual(first["status"], "configured", first)
            self.assertTrue(path.read_text(encoding="utf-8").startswith(source))
            self.assertEqual(self.run_config(clients=("dsh",), dsh_home=harness)[0]["status"], "unchanged")
            self.assertEqual(self.run_config(clients=("dsh",), dsh_home=harness, uninstall=True)[0]["status"], "removed")
        self.assertEqual(path.read_text(encoding="utf-8"), source)

    def test_noninteractive_default_requires_selection(self):
        output = io.StringIO()
        with patch.object(cc, "ROOT", self.root), patch.object(Path, "home", return_value=self.home), \
             patch.object(cc.sys, "stdin", io.StringIO()), patch.object(cc.sys, "stdout", output):
            code = cc.main(["--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "selection_required")
        self.assertFalse((self.home / ".grok" / "config.toml").exists())

    def test_explicit_selection_does_not_prompt(self):
        output = io.StringIO()
        with patch.object(cc, "ROOT", self.root), patch.object(Path, "home", return_value=self.home), \
             patch.object(cc.sys, "stdin", io.StringIO()), patch.object(cc.sys, "stdout", output):
            code = cc.main(["--clients", "grok", "--json"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())["results"][0]["status"], "configured")

    def test_multiple_dsh_profiles_require_instance_selection(self):
        homes = [self.base / "DSH A", self.base / "DSH B"]
        output = io.StringIO()
        with patch.object(cc, "ROOT", self.root), patch.object(Path, "home", return_value=self.home), \
             patch.object(dsh_adapter, "candidate_homes", return_value=homes), \
             patch.object(cc.sys, "stdin", io.StringIO()), patch.object(cc.sys, "stdout", output):
            code = cc.main(["--clients", "dsh", "--json"])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["status"], "selection_required")
        self.assertEqual(result["candidates"], [str(item) for item in homes])
        self.assertFalse(cc._manifest_path(self.root).exists())

    def test_dry_run_lists_ambiguous_dsh_without_writing(self):
        homes = [self.base / "DSH A", self.base / "DSH B"]
        output = io.StringIO()
        with patch.object(cc, "ROOT", self.root), patch.object(Path, "home", return_value=self.home), \
             patch.object(dsh_adapter, "candidate_homes", return_value=homes), \
             patch.object(cc.sys, "stdin", io.StringIO()), patch.object(cc.sys, "stdout", output):
            code = cc.main(["--dry-run", "--json"])
        payload = json.loads(output.getvalue())
        dsh = next(item for item in payload["detected"] if item["client"] == "dsh")
        self.assertEqual(dsh["candidates"], [str(item) for item in homes])
        self.assertEqual(code, 0)
        self.assertFalse(cc._manifest_path(self.root).exists())


if __name__ == "__main__":
    unittest.main()
