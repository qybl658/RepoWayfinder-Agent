"""Focused contract tests for the read-only client adapter metadata."""
from __future__ import annotations

from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from client_adapters import ADAPTERS


class ClientAdapterTests(unittest.TestCase):
    def test_paths_scopes_and_environment_overrides(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            home, project = base / "home", base / "project"
            home.mkdir()
            project.mkdir()
            with patch.dict("os.environ", {"GEMINI_CLI_HOME": str(base / "gemini-home"),
                                           "OPENCODE_CONFIG": str(base / "custom-opencode.jsonc")}, clear=True):
                self.assertEqual(ADAPTERS["gemini"].config_path("user", project, home),
                                 base / "gemini-home" / ".gemini" / "settings.json")
                self.assertEqual(ADAPTERS["gemini"].config_path("project", project, home),
                                 project / ".gemini" / "settings.json")
                self.assertEqual(ADAPTERS["opencode"].config_path("user", project, home),
                                 base / "custom-opencode.jsonc")
                self.assertEqual(ADAPTERS["opencode"].config_path("project", project, home),
                                 project / "opencode.json")
            self.assertEqual(ADAPTERS["vscode"].config_path("project", project, home),
                             project / ".vscode" / "mcp.json")
            self.assertEqual(ADAPTERS["vscode"].config_path("user", project, home),
                             home / "AppData" / "Roaming" / "Code" / "User" / "mcp.json")
            self.assertEqual(ADAPTERS["windsurf"].config_path("user", project, home),
                             home / ".codeium" / "windsurf" / "mcp_config.json")
            self.assertEqual(ADAPTERS["claude_desktop"].config_path("user", project, home),
                             home / "AppData" / "Roaming" / "Claude" / "claude_desktop_config.json")
            for client in ("windsurf", "claude_desktop"):
                with self.subTest(client=client), self.assertRaisesRegex(ValueError, "no documented project"):
                    ADAPTERS[client].config_path("project", project, home)

    def test_opencode_uses_existing_jsonc_without_overwriting_another_variant(self):
        with TemporaryDirectory() as temporary, patch.dict("os.environ", {}, clear=True):
            base = Path(temporary)
            home, project = base / "home", base / "project"
            home.mkdir()
            project.mkdir()
            (project / "opencode.jsonc").write_text("{/* existing */}", encoding="utf-8")
            self.assertEqual(ADAPTERS["opencode"].config_path("project", project, home),
                             project / "opencode.jsonc")
            (project / "opencode.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Multiple client config"):
                ADAPTERS["opencode"].config_path("project", project, home)

    def test_entries_are_absolute_argv_without_privilege_or_account_changes(self):
        with TemporaryDirectory() as temporary:
            base = Path(temporary)
            python, root, workspace = base / "Python" / "python.exe", base / "repo", base / "workspace"
            command = str(python.absolute())
            args = ["-u", str((root / "agent.py").absolute()), "--workspace", str(workspace.absolute()), "serve"]
            for name in ("gemini", "windsurf", "claude_desktop"):
                with self.subTest(client=name):
                    self.assertEqual(ADAPTERS[name].entry(python, root, workspace),
                                     {"command": command, "args": args})
            self.assertEqual(ADAPTERS["vscode"].entry(python, root, workspace),
                             {"type": "stdio", "command": command, "args": args})
            self.assertEqual(ADAPTERS["opencode"].entry(python, root, workspace),
                             {"type": "local", "command": [command, *args]})

    def test_detection_requires_client_evidence_and_keeps_legacy_windsurf_separate(self):
        with TemporaryDirectory() as temporary, patch("client_adapters.shutil.which", return_value=None):
            home = Path(temporary) / "home"
            home.mkdir()
            for name, adapter in ADAPTERS.items():
                with self.subTest(client=name):
                    path = adapter.config_path("user", home, home)
                    self.assertFalse(adapter.detect(path, home))
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("{}", encoding="utf-8")
                    self.assertTrue(adapter.detect(path, home))
                    path.unlink()
            # A Devin Desktop configuration is not evidence of legacy Cascade.
            devin = home / "AppData" / "Roaming" / "devin" / "mcp_config.json"
            devin.parent.mkdir(parents=True)
            devin.write_text("{}", encoding="utf-8")
            windsurf = ADAPTERS["windsurf"]
            self.assertFalse(windsurf.detect(windsurf.config_path("user", home, home), home))

    def test_opencode_v2_selects_nested_mcp_section_and_rejects_mixed_layout(self):
        with TemporaryDirectory() as temporary, patch.dict("os.environ", {}, clear=True):
            base = Path(temporary)
            home, project = base / "home", base / "project"
            home.mkdir()
            project.mkdir()
            (project / "opencode.jsonc").write_text(
                '{"mcp":{"servers":{"existing":{"type":"local","command":["echo"]}}}}',
                encoding="utf-8")
            with patch("client_adapters.shutil.which", return_value=None):
                path = ADAPTERS["opencode"].config_path("project", project, home)
                self.assertEqual(ADAPTERS["opencode"].section_path_for(path), ("mcp", "servers"))
            (project / "opencode.jsonc").unlink()
            with patch("client_adapters.shutil.which", return_value="C:/bin/opencode.exe"), patch(
                    "client_adapters.subprocess.run", return_value=subprocess.CompletedProcess(
                        args=[], returncode=0, stdout="OpenCode 2.0.1", stderr="")):
                path = ADAPTERS["opencode"].config_path("user", project, home)
                self.assertEqual(ADAPTERS["opencode"].section_path_for(path), ("mcp", "servers"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('{"mcp":{"legacy":{"type":"local","command":["echo"]}}}', encoding="utf-8")
                with self.assertRaisesRegex(ValueError, "migrate it first"):
                    ADAPTERS["opencode"].section_path_for(path)


if __name__ == "__main__":
    unittest.main()
