"""Focused DSH profile discovery and owned YAML patch tests."""
from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import dsh_adapter


_BUNDLE = Path(os.environ["DSH_TEST_BUNDLE"]) if os.environ.get("DSH_TEST_BUNDLE") else None
_NODE_MODULES = _BUNDLE / "app" / "resources" / "app" / "node_modules" if _BUNDLE else None


class DshAdapterTests(unittest.TestCase):
    def setUp(self):
        if _NODE_MODULES is None or not (_NODE_MODULES / "node" / "bin" / "node.exe").is_file():
            self.skipTest("set DSH_TEST_BUNDLE to a validated DSH installation")
        self.node = _NODE_MODULES / "node" / "bin" / "node.exe"
        self.yaml = _NODE_MODULES / "yaml"

    def _parser(self):
        return patch("dsh_adapter._node_yaml", return_value=(self.node, self.yaml))

    def test_nonempty_patch_keeps_other_entries_and_comments_byte_for_byte(self):
        source = ('# existing preface\n- id: another-plugin # keep inline comment\n'
                  '  config: {expr: !!js "1+2"}\n# existing tail\n')
        expected = dsh_adapter._entry("C:/Python/python.exe", ["-u", "C:/agent.py", "serve"], "C:/work")
        with self._parser():
            added = dsh_adapter.render_add(Path("profile.yml"), source,
                                           expected["command"], expected["args"], expected["cwd"])
            self.assertTrue(added.startswith(source))
            self.assertEqual(dsh_adapter.current_entry(Path("profile.yml"), added), expected)
            receipt = {**expected, "_owned_block": dsh_adapter.owned_block(added)}
            self.assertEqual(dsh_adapter.render_remove(Path("profile.yml"), added, receipt), source)
            with self.assertRaisesRegex(ValueError, "changed"):
                dsh_adapter.render_remove(Path("profile.yml"),
                                          added.replace("failOnStartupError: true", "failOnStartupError: false"),
                                          receipt)
            with self.assertRaisesRegex(ValueError, "already exists"):
                dsh_adapter.render_add(Path("profile.yml"), added,
                                       expected["command"], expected["args"], expected["cwd"])

    def test_empty_profile_round_trip_and_modified_ownership_block_rejected(self):
        source = "# user heading\r\n  []\r\n"
        expected = dsh_adapter._entry("C:/Python/python.exe", ["C:/agent.py", "serve"], "C:/work")
        with self._parser():
            added = dsh_adapter.render_add(Path("profile.yml"), source,
                                           expected["command"], expected["args"], expected["cwd"])
            self.assertEqual(dsh_adapter.render_remove(Path("profile.yml"), added, expected), source)
            with self.assertRaisesRegex(ValueError, "changed"):
                dsh_adapter.render_remove(Path("profile.yml"), added.replace("C:/work", "C:/else"), expected)

    def test_current_entry_rejects_same_id_owned_by_another_plugin(self):
        with self._parser():
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                dsh_adapter.current_entry(Path("profile.yml"),
                                          "- insert:\n    - id: repo-wayfinder-mcp\n      name: unrelated\n      config: {}\n")

    def test_existing_id_and_invalid_yaml_fail_closed(self):
        with self._parser():
            with self.assertRaisesRegex(ValueError, "already exists"):
                dsh_adapter.render_add(Path("profile.yml"), "- insert:\n    - id: repo-wayfinder-mcp\n", "a", [], "b")
            with self.assertRaisesRegex(ValueError, "valid top-level"):
                dsh_adapter.render_add(Path("profile.yml"), "broken: [", "a", [], "b")


class DshDiscoveryTests(unittest.TestCase):
    def test_explicit_home_and_project_scope(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            web = root / "profiles" / "web"
            web.mkdir(parents=True)
            (web / "package.json").write_text(json.dumps({"dsh": {"profile": {
                "bundles": ["@deepseek-ai/dsh-base", "@deepseek-ai/dsh-web-app"]}}}), encoding="utf-8")
            self.assertEqual(dsh_adapter.discover("user", root, root, root), web / "cordis.patch.yml")
            self.assertIsNone(dsh_adapter.discover("project", root, root, root))
            self.assertEqual(dsh_adapter.candidate_homes("project", root, root), [])
            with self.assertRaisesRegex(ValueError, "not a DSH"):
                dsh_adapter.discover("user", root, root, root / "missing")


if __name__ == "__main__":
    unittest.main()
