from __future__ import annotations

import unittest

from jsonc_config import entry_text, insert, loads, remove


class JsoncConfigTests(unittest.TestCase):
    def test_insert_and_remove_preserve_other_comments_and_trailing_commas(self):
        source = '''{
  // Keep this setting and comment.
  "model": "example", 
  "mcp": {
    "existing": {"command": ["node", "a//b"],}, // keep this server
  },
}
'''
        entry = {"type": "local", "command": ["C:\\Python\\python.exe", "-u", "D:\\repo\\agent.py"]}
        updated = insert(source, "mcp", entry)
        self.assertEqual(loads(updated)["mcp"]["repo_wayfinder"], entry)
        self.assertIn('"repo_wayfinder": {', entry_text(updated, "mcp"))
        self.assertIn('"model": "example",', updated)
        self.assertIn("// Keep this setting and comment.", updated)
        self.assertIn('"existing": {"command": ["node", "a//b"],}, // keep this server', updated)
        removed = remove(updated, "mcp")
        self.assertEqual(loads(removed), loads(source))
        self.assertIn("// Keep this setting and comment.", removed)
        self.assertIn("// keep this server", removed)

    def test_add_section_to_empty_and_nonempty_json(self):
        entry = {"type": "stdio", "command": "C:/Python/python.exe", "args": ["serve"]}
        for source in ("{}", "{\n}\n", '{"foo": 1}', '{\n  "foo": 1 // keep\n}\n'):
            with self.subTest(source=source):
                updated = insert(source, "servers", entry)
                self.assertEqual(loads(updated)["servers"]["repo_wayfinder"], entry)
                removed = remove(updated, "servers")
                self.assertEqual(loads(removed).get("foo"), loads(source).get("foo"))

    def test_remove_first_middle_last_and_only_member(self):
        sources = (
            '{"servers":{"repo_wayfinder":{},"other":1}}',
            '{"servers":{"first":1,"repo_wayfinder":{},"last":2}}',
            '{"servers":{"first":1,"repo_wayfinder":{}}}',
            '{"servers":{"repo_wayfinder":{},}}',
        )
        for source in sources:
            with self.subTest(source=source):
                result = remove(source, "servers")
                self.assertNotIn("repo_wayfinder", loads(result)["servers"])

    def test_duplicate_and_malformed_config_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            loads('{"mcp": {}, "mcp": {}}')
        with self.assertRaises(ValueError):
            insert('{"mcp": /* unterminated', "mcp", {})
        with self.assertRaisesRegex(ValueError, "Same-name"):
            insert('{"mcp":{"repo_wayfinder":{}}}', "mcp", {})
        with self.assertRaisesRegex(ValueError, "must be an object"):
            insert('{"mcp":[]}', "mcp", {})
        with self.assertRaisesRegex(ValueError, "entry is missing"):
            entry_text('{"mcp":{}}', "mcp")

    def test_entry_text_detects_format_or_comment_change(self):
        source = '{"servers":{"repo_wayfinder": {"command":"python"}, // note\n"other":{}}}'
        changed = source.replace('"command":"python"', '"command" : "python"')
        self.assertNotEqual(entry_text(source, "servers"), entry_text(changed, "servers"))

    def test_nested_v2_section_preserves_existing_servers(self):
        source = '{"mcp": {"servers": {"existing": {"type":"local", "command":["echo"]},},},}'
        entry = {"type": "local", "command": ["C:/Python/python.exe", "serve"]}
        path = ("mcp", "servers")
        updated = insert(source, path, entry)
        self.assertEqual(loads(updated)["mcp"]["servers"]["repo_wayfinder"], entry)
        self.assertEqual(loads(updated)["mcp"]["servers"]["existing"],
                         loads(source)["mcp"]["servers"]["existing"])
        self.assertIn('"repo_wayfinder": {', entry_text(updated, path))
        self.assertEqual(loads(remove(updated, path)), loads(source))
        self.assertEqual(loads(insert("{}", path, entry))["mcp"]["servers"]["repo_wayfinder"], entry)


if __name__ == "__main__":
    unittest.main()
