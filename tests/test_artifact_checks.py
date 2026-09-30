import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import artifact_checks as checks


class ArtifactChecksTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def put(self, name, text):
        (self.root / name).write_text(text, encoding='utf-8', newline='')

    def evaluate(self, kind, path, **fields):
        return checks.evaluate_checks(self.root, [{'type': kind, 'path': path, **fields}])[0]

    def test_bom_quoted_newline_where_order_and_counts(self):
        self.put('rows.csv', '\ufeffid,note,amount\r\nA,"one\ntwo",0.1\r\nB,other,0.2\r\nA,third,-0.3\r\n')
        rows = [{'id': 'A', 'note': 'one\ntwo', 'amount': '0.1'}, {'id': 'B', 'note': 'other', 'amount': '0.2'}, {'id': 'A', 'note': 'third', 'amount': '-0.3'}]
        requests = [
            {'type': 'csv_count', 'path': 'rows.csv', 'where': {'id': 'A'}, 'expected': 2},
            {'type': 'csv_counts', 'path': 'rows.csv', 'column': 'id', 'expected': {'A': 2, 'B': 1}},
            {'type': 'csv_sum', 'path': 'rows.csv', 'column': 'amount', 'expected': '0'},
            {'type': 'csv_rows', 'path': 'rows.csv', 'expected': rows},
        ]
        with patch.object(checks, '_text', wraps=checks._text) as read:
            result = checks.evaluate_checks(self.root, requests)
        self.assertTrue(all(r['passed'] for r in result))
        self.assertEqual(read.call_count, 1)
        self.assertEqual(result[3]['actual'], {'rows': 3, 'mismatched_rows': 0, 'extra_rows': 0, 'missing_rows': 0})
        self.assertNotIn('one', json.dumps(result))
        wrong = self.evaluate('csv_rows', 'rows.csv', expected=list(reversed(rows)))
        self.assertFalse(wrong['passed'])
        self.assertEqual(wrong['actual']['mismatched_rows'], 2)

    def test_invalid_headers_rows_and_quoting_fail(self):
        for text in ['', 'x,x\n1,2\n', ',x\n1,2\n', 'a,b\n1\n', 'a,b\n1,2,3\n', 'a,b\n"unterminated,2\n', 'a,b\n\n']:
            with self.subTest(text=text):
                self.put('bad.csv', text)
                self.assertFalse(self.evaluate('csv_count', 'bad.csv', expected=1)['passed'])

    def test_missing_column_and_incomplete_rows_fail(self):
        self.put('rows.csv', 'a,b\n1,2\n')
        self.assertFalse(self.evaluate('csv_sum', 'rows.csv', column='c', expected='0')['passed'])
        self.assertFalse(self.evaluate('csv_count', 'rows.csv', where={'c': 'x'}, expected=0)['passed'])
        self.assertFalse(self.evaluate('csv_rows', 'rows.csv', expected=[{'a': '1'}])['passed'])

    def test_decimal_exact_arithmetic_and_nonfinite_failure(self):
        self.put('n.csv', 'n\n123456789012345678901234567890.1\n0.2\n-0.3\n')
        result = self.evaluate('csv_sum', 'n.csv', column='n', expected='123456789012345678901234567890')
        self.assertTrue(result['passed'])
        for value in ['NaN', 'Infinity', '-Infinity', 'oops']:
            self.put('n.csv', 'n\n' + value + '\n')
            self.assertFalse(self.evaluate('csv_sum', 'n.csv', column='n', expected=0)['passed'])
            with self.assertRaises(ValueError):
                checks.validate_checks([{'type': 'csv_sum', 'path': 'n.csv', 'column': 'n', 'expected': value}])

    def test_json_pointer_structures_and_strict_value_types(self):
        self.put('v.json', json.dumps({'a/b': {'~': [1, True, {'x': [None, 'yes']}]}}))
        self.assertTrue(self.evaluate('json_value', 'v.json', pointer='/a~1b/~0/2', expected={'x': [None, 'yes']})['passed'])
        self.assertFalse(self.evaluate('json_value', 'v.json', pointer='/a~1b/~0/1', expected=1)['passed'])
        self.assertFalse(self.evaluate('json_value', 'v.json', pointer='/missing', expected=None)['passed'])
        for pointer in ['no-slash', '/a~2b', '/a~1b/~0/-1', '/a~1b/~0/01']:
            with self.subTest(pointer=pointer), self.assertRaises((ValueError, KeyError)):
                checks.json_pointer({'a/b': {'~': [1]}}, pointer)

    def test_nonfinite_json_and_invalid_utf8_fail(self):
        for value in ['NaN', '1e1000']:
            self.put('bad.json', '{"x":' + value + '}')
            self.assertFalse(self.evaluate('json_value', 'bad.json', pointer='/x', expected=None)['passed'])
        self.put('bad.json', '{"x":1,"x":2}')
        self.assertFalse(self.evaluate('json_value', 'bad.json', pointer='/x', expected=2)['passed'])
        (self.root / 'bad.csv').write_bytes(b'a\n\xff\n')
        self.assertFalse(self.evaluate('csv_count', 'bad.csv', expected=1)['passed'])

    def test_scope_credentials_and_symlink_escape(self):
        for path in ['../outside.csv', '.env', 'nested/.env.secret', str(self.root / 'absolute.json'), '.git/config']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                checks.validate_checks([{'type': 'file_exists', 'path': path}])
        with tempfile.TemporaryDirectory() as elsewhere:
            outside = Path(elsewhere) / 'v.json'
            outside.write_text('{}', encoding='utf-8')
            try:
                (self.root / 'alias.json').symlink_to(outside)
            except OSError:
                return
            self.assertFalse(self.evaluate('json_value', 'alias.json', expected={})['passed'])

    def test_missing_file_and_directory_are_not_files(self):
        self.assertFalse(self.evaluate('file_exists', 'missing.txt')['passed'])
        (self.root / 'folder').mkdir()
        self.assertFalse(self.evaluate('file_exists', 'folder')['passed'])
        self.assertFalse(self.evaluate('file_contains', 'missing.txt', expected='x')['passed'])

    def test_snapshots_preserve_order_and_ignore_csv_json_serialization(self):
        self.put('rows.csv', 'a,b\r\n1,"x"\r\n')
        self.put('v.json', '{"a":1,"b":[true]}')
        self.put('note.txt', 'hello\n')
        before = checks.snapshot_artifacts(self.root, ['rows.csv', 'v.json', 'note.txt'])
        self.put('rows.csv', '\ufeffa,b\n1,x\n')
        self.put('v.json', '{"b": [true], "a": 1}')
        self.assertEqual(before, checks.snapshot_artifacts(self.root, ['rows.csv', 'v.json', 'note.txt']))
        self.assertFalse(checks.equal_values({'x': [True]}, {'x': [1]}))
        self.assertEqual(checks.validate_snapshot_paths(self.root, ['future.csv']), ['future.csv'])
        with self.assertRaises(ValueError):
            checks.snapshot_artifacts(self.root, ['rows.csv', 'rows.csv'])

    def test_validation_rejects_types_empty_and_unknown_fields(self):
        bad = [[], {}, [{'type': 'csv_count', 'path': 'x', 'expected': True}],
               [{'type': 'csv_count', 'path': 'x', 'expected': 1, 'where': {'a': 1}}],
               [{'type': 'csv_rows', 'path': 'x', 'expected': [{'a': 1}]}],
               [{'type': 'json_value', 'path': 'x', 'expected': (1,)}],
               [{'type': 'file_exists', 'path': 'x', 'arbitrary': 1}]]
        for supplied in bad:
            with self.subTest(supplied=supplied), self.assertRaises(ValueError):
                checks.validate_checks(supplied)

    def test_real_count_misuses_return_local_repairs_without_guessing(self):
        self.put('rows.csv', 'id,note\nA,__private_alpha__\nA,__private_beta__\nB,other\n')
        rows = [{'id':'A','note':'__private_alpha__'}, {'id':'A','note':'__private_beta__'}]
        misuses = [
            ({'id':'filtered','type':'csv_counts','path':'rows.csv','where':{'id':'A'},'expected':2},
             ['check[1]', 'id=filtered', 'type=csv_value_counts', 'field column', 'csv_row_count', 'omit column']),
            ({'type':'csv_count','path':'rows.csv','column':'id','where':{'id':'A'},'expected':2},
             ['check[1]', 'id=check-1', 'type=csv_row_count', 'field column', 'allowed:', 'omit column']),
            ({'type':'csv_rows','path':'rows.csv','column':'id','where':{'id':'A'},'expected':rows},
             ['check[1]', 'type=csv_rows', 'field column', 'allowed:', 'omit column']),
        ]
        for supplied, fragments in misuses:
            with self.subTest(kind=supplied['type']):
                with self.assertRaises(ValueError) as error:
                    checks.validate_checks([supplied])
                message = str(error.exception)
                for fragment in fragments:
                    self.assertIn(fragment, message)
                self.assertNotIn('__private_alpha__', message)
                self.assertNotIn('__private_beta__', message)
        correct = [
            {'type':'csv_row_count','path':'rows.csv','where':{'id':'A'},'expected':2},
            {'type':'csv_value_counts','path':'rows.csv','column':'id','expected':{'A':2,'B':1}},
            {'type':'csv_rows','path':'rows.csv','where':{'id':'A'},'expected':rows},
        ]
        self.assertTrue(all(item['passed'] for item in checks.evaluate_checks(self.root, correct)))
        legacy = [dict(correct[0],type='csv_count'),dict(correct[1],type='csv_counts')]
        self.assertEqual([c['type'] for c in checks.validate_checks(legacy)],['csv_row_count','csv_value_counts'])
        self.assertTrue(all(item['passed'] for item in checks.evaluate_checks(self.root,legacy)))
        self.assertEqual(legacy[0]['type'],'csv_count')
        with self.assertRaises(ValueError) as error:
            checks.validate_checks([dict(correct[0],unexpected='do-not-echo-this-value')])
        self.assertIn('field unexpected',str(error.exception))
        self.assertIn('allowed:',str(error.exception))
        self.assertNotIn('do-not-echo-this-value',str(error.exception))

    def test_large_results_are_compact_and_content_limit_is_enforced(self):
        value = {'x': 'private-' * 1000}
        self.put('v.json', json.dumps(value))
        result = self.evaluate('json_value', 'v.json', expected=value)
        self.assertTrue(result['passed'])
        self.assertLess(len(json.dumps(result)), 400)
        self.assertNotIn('private', json.dumps(result))
        with patch.object(checks, 'MAX_ARTIFACT_BYTES', 3):
            self.assertFalse(self.evaluate('json_value', 'v.json', expected=value)['passed'])

    def test_cache_is_per_call_and_detects_changed_read(self):
        self.put('v.json', '{"x":1}')
        same = [{'type': 'json_value', 'path': 'v.json', 'pointer': '/x', 'expected': 1} for _ in range(128)]
        with patch.object(checks, '_text', wraps=checks._text) as read:
            self.assertTrue(all(r['passed'] for r in checks.evaluate_checks(self.root, same)))
            self.assertEqual(read.call_count, 1)
        self.put('v.json', '{"x":2}')
        self.assertFalse(checks.evaluate_checks(self.root, same)[0]['passed'])
        real_stat = Path.stat
        calls = 0
        def modified_stat(path, *args, **kwargs):
            nonlocal calls
            stat = real_stat(path, *args, **kwargs)
            if path == self.root / 'v.json':
                calls += 1
                if calls == 2:
                    from types import SimpleNamespace
                    return SimpleNamespace(st_size=stat.st_size + 1, st_mtime_ns=stat.st_mtime_ns, st_ctime_ns=stat.st_ctime_ns, st_ino=stat.st_ino)
            return stat
        with patch.object(Path, 'stat', modified_stat):
            with self.assertRaisesRegex(ValueError, 'changed'):
                checks._text(self.root / 'v.json')

    def test_total_read_row_and_cell_guards_preserve_files(self):
        self.put('a.csv', 'a,b\n1,2\n3,4\n')
        self.put('b.txt', '1234567890')
        original = (self.root / 'a.csv').read_bytes()
        with patch.object(checks, 'MAX_CSV_ROWS', 1):
            self.assertFalse(self.evaluate('csv_count', 'a.csv', expected=2)['passed'])
        with patch.object(checks, 'MAX_CSV_CELLS', 3):
            self.assertFalse(self.evaluate('csv_count', 'a.csv', expected=2)['passed'])
        with patch.object(checks, 'MAX_TOTAL_READ_BYTES', len(original) + 1):
            result = checks.evaluate_checks(self.root, [
                {'type': 'csv_count', 'path': 'a.csv', 'expected': 2},
                {'type': 'file_contains', 'path': 'b.txt', 'expected': '1'}])
            self.assertTrue(result[0]['passed'])
            self.assertFalse(result[1]['passed'])
            with self.assertRaisesRegex(ValueError, 'budget'):
                checks.snapshot_artifacts(self.root, ['a.csv', 'b.txt'])
        self.assertEqual((self.root / 'a.csv').read_bytes(), original)


if __name__ == '__main__':
    unittest.main()
