"""Read-only, bounded artifact assertions shared by host and job adapters."""
import csv
import io
import json
import math
import re
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

from agent_service import scoped_file

MAX_ARTIFACT_BYTES = 20 * 1024 * 1024
MAX_TOTAL_READ_BYTES = 20 * 1024 * 1024
MAX_CSV_ROWS = 50_000
MAX_CSV_CELLS = 250_000
MAX_CHECKS = 128
MAX_EXPECTED_BYTES = 64 * 1024
ALIASES = {'csv_count': 'csv_row_count', 'csv_counts': 'csv_value_counts'}
KINDS = {'file_exists', 'file_contains', 'json_value', 'csv_row_count',
         'csv_sum', 'csv_value_counts', 'csv_rows'}


def json_pointer(value, pointer=''):
    """Resolve RFC 6901 pointers; list indices are nonnegative decimal indices."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith('/')):
        raise ValueError('JSON pointer must be empty or begin with /')
    for token in pointer.split('/')[1:]:
        if re.search(r'~(?:[^01]|$)', token):
            raise ValueError('Invalid JSON pointer escape')
        token = token.replace('~1', '/').replace('~0', '~')
        if isinstance(value, list):
            if not re.fullmatch(r'0|[1-9][0-9]*', token):
                raise ValueError('JSON array index must be a nonnegative integer')
            value = value[int(token)]
        elif isinstance(value, dict):
            value = value[token]
        else:
            raise ValueError('JSON pointer traverses a scalar')
    return value


def _decimal(value):
    if type(value) not in (str, int, float) or isinstance(value, str) and len(value) > 200:
        raise ValueError('Decimal requires a finite number or decimal string')
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError('Invalid decimal number') from None
    if not number.is_finite() or abs(number.adjusted()) > 1000 or abs(number.as_tuple().exponent) > 1000 or len(number.as_tuple().digits) > 1000:
        raise ValueError('Decimal value is nonfinite or too large')
    return number


def _json_size(value):
    try:
        def check_types(item):
            if type(item) in (dict, list):
                if isinstance(item, dict) and any(not isinstance(k, str) for k in item):
                    raise ValueError('JSON object keys must be strings')
                for child in item.values() if isinstance(item, dict) else item:
                    check_types(child)
            elif type(item) not in (str, int, float, bool, type(None)):
                raise ValueError('Expected value must be JSON data')
        check_types(value)
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ValueError('Expected value must be finite JSON data') from None
    if len(encoded) > MAX_EXPECTED_BYTES:
        raise ValueError('Expected value exceeds 64 KiB')


def _error_context(index, check):
    name = check.get('id', f'check-{index + 1}') if isinstance(check, dict) else 'invalid'
    name = name[:100] if isinstance(name, str) else 'invalid'
    kind = check.get('type', 'missing') if isinstance(check, dict) else 'invalid'
    kind = kind[:100] if isinstance(kind, str) else 'invalid'
    return f'check[{index + 1}] id={name} type={kind}'


def validate_checks(checks):
    """Normalize legacy CSV aliases; reject ambiguity with actionable errors."""
    if not isinstance(checks, list) or not 1 <= len(checks) <= MAX_CHECKS:
        raise ValueError(f'checks requires 1 to {MAX_CHECKS} assertions')
    normalized, ids = [], set()
    for index, supplied in enumerate(checks):
        check = dict(supplied) if isinstance(supplied, dict) else {}
        if isinstance(check.get('type'), str):
            check['type'] = ALIASES.get(check['type'], check['type'])
        check.setdefault('id', f'check-{index + 1}')
        context = _error_context(index, check)
        def fail(field, fix):
            raise ValueError(f'{context}: field {field}: {fix}')
        if not isinstance(supplied, dict) or not isinstance(check.get('type'), str) or check['type'] not in KINDS:
            fail('type', 'choose one of ' + ', '.join(sorted(KINDS)))
        kind = check['type']
        allowed = {'id', 'type', 'path', 'expected'}
        if kind == 'json_value':
            allowed.add('pointer')
        if kind.startswith('csv_'):
            allowed.add('where')
        if kind in {'csv_sum', 'csv_value_counts'}:
            allowed.add('column')
        unknown = set(check) - allowed
        if unknown:
            fields = ', '.join(sorted(str(name) for name in unknown))
            fix = 'remove unknown fields; allowed: ' + ', '.join(sorted(allowed))
            if 'column' in unknown and kind in {'csv_row_count', 'csv_rows'}:
                fix += f'; omit column for {kind}; where filters complete rows'
            fail(fields, fix)
        if not isinstance(check['id'], str) or not 1 <= len(check['id']) <= 100 or check['id'] in ids:
            fail('id', 'provide a unique nonempty string up to 100 characters')
        ids.add(check['id'])
        if not isinstance(check.get('path'), str) or len(check['path']) > 1000:
            fail('path', 'provide a relative artifact path up to 1000 characters')
        try:
            scoped_file(Path.cwd() / 'artifact-validation-root', check['path'])
        except ValueError:
            fail('path', 'use a relative path inside the task directory, outside credentials and Git metadata')
        if kind == 'file_exists':
            if 'expected' in check and check['expected'] is not True:
                fail('expected', 'omit expected or set it to true')
            check['expected'] = True
        elif 'expected' not in check:
            fail('expected', 'supply the task-specific expected result')
        try:
            _json_size(check['expected'])
        except ValueError as exc:
            fail('expected', str(exc) + '; supply bounded finite JSON data')
        if kind == 'file_contains' and (not isinstance(check['expected'], str) or not check['expected']):
            fail('expected', 'provide nonempty text to find')
        if kind == 'json_value':
            check.setdefault('pointer', '')
            pointer = check['pointer']
            if not isinstance(pointer, str) or len(pointer) > 1000 or (pointer and not pointer.startswith('/')) or re.search(r'~(?:[^01]|$)', pointer):
                fail('pointer', 'use an empty root pointer or /-prefixed RFC 6901 pointer with ~0 and ~1 escapes')
        if kind.startswith('csv_'):
            where = check.get('where', {})
            if not isinstance(where, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in where.items()):
                fail('where', 'provide an object of exact string column/value filters')
            try:
                _json_size(where)
            except ValueError as exc:
                fail('where', str(exc) + '; supply bounded string filters')
            check['where'] = dict(where)
        if kind in {'csv_sum', 'csv_value_counts'} and (not isinstance(check.get('column'), str) or not check['column'] or len(check['column']) > 1000):
            fix = 'supply a nonempty CSV column name up to 1000 characters'
            if kind == 'csv_value_counts' and type(check['expected']) is int:
                fix += '; for the row count after where filters, use csv_row_count with integer expected and omit column'
            elif kind == 'csv_value_counts':
                fix += '; expected must be an object mapping column values to counts'
            fail('column', fix)
        expected = check['expected']
        if kind == 'csv_row_count' and (type(expected) is not int or expected < 0):
            fail('expected', 'provide a nonnegative integer row count after where filters')
        if kind == 'csv_sum':
            try:
                check['expected'] = str(_decimal(expected))
            except ValueError:
                fail('expected', 'provide a finite decimal number or decimal string')
        if kind == 'csv_value_counts' and (not isinstance(expected, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in expected.items())):
            fail('expected', 'provide an object mapping column values to nonnegative integer counts; for filtered row count use csv_row_count and omit column')
        if kind == 'csv_rows' and (not isinstance(expected, list) or any(not isinstance(r, dict) or any(not isinstance(k, str) or not k or not isinstance(v, str) for k, v in r.items()) for r in expected)):
            fail('expected', 'provide an ordered list of complete rows with string column/value pairs')
        normalized.append(check)
    return normalized


def _text(path, budget=None):
    with path.open('rb') as stream:
        before = path.stat()
        if budget is not None and before.st_size > budget[0]:
            raise ValueError('Artifact checks exceed total read budget')
        raw = stream.read(MAX_ARTIFACT_BYTES + 1)
        after = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino):
        raise ValueError('Artifact changed while being read')
    if len(raw) > MAX_ARTIFACT_BYTES:
        raise ValueError('Artifact exceeds 20 MiB content limit')
    if budget is not None:
        if len(raw) > budget[0]:
            raise ValueError('Artifact checks exceed total read budget')
        budget[0] -= len(raw)
    return raw.decode('utf-8-sig')


def _csv(text):
    try:
        reader = csv.reader(io.StringIO(text, newline=''), strict=True)
        header = next(reader, None)
        if not header or any(not name.strip() for name in header) or len(set(header)) != len(header):
            raise ValueError('CSV header is missing, empty or duplicated')
        rows = []
        cells = len(header)
        for values in reader:
            if len(values) != len(header):
                raise ValueError(f'CSV row at line {reader.line_num} has incorrect field count')
            cells += len(values)
            if len(rows) >= MAX_CSV_ROWS or cells > MAX_CSV_CELLS:
                raise ValueError('CSV exceeds row or cell limit; artifact preserved')
            rows.append(dict(zip(header, values)))
        return header, rows
    except csv.Error:
        raise ValueError('Malformed CSV quoting or oversized field') from None


def _load(path, budget=None):
    text = _text(path, budget)
    if path.suffix.lower() == '.csv':
        header, rows = _csv(text)
        return {'header': header, 'rows': rows}
    if path.suffix.lower() == '.json':
        return load_json(text)
    return text


def load_json(text):
    """Parse finite JSON without silently overwriting duplicate object keys."""
    def finite_float(value):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError('Nonfinite JSON number')
        return number
    def reject_constant(_):
        raise ValueError('Nonfinite JSON number')
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON object key')
            result[key] = value
        return result
    return json.loads(text, parse_float=finite_float, parse_constant=reject_constant,
                      object_pairs_hook=unique_object)


def validate_snapshot_paths(directory, paths):
    """Check snapshot scope before execution; artifacts need not yet exist."""
    if not isinstance(paths, list) or not 1 <= len(paths) <= MAX_CHECKS or any(not isinstance(p, str) or len(p) > 1000 for p in paths):
        raise ValueError(f'paths requires 1 to {MAX_CHECKS} unique relative paths')
    for path in paths:
        scoped_file(directory, path)
    if len(set(paths)) != len(paths):
        raise ValueError('Snapshot paths must be unique')
    return paths


def snapshot_artifacts(directory, paths):
    """Return parsed CSV/JSON or UTF-8 text values, without modifying artifacts."""
    validate_snapshot_paths(directory, paths)
    budget = [MAX_TOTAL_READ_BYTES]
    return {path: _load(scoped_file(directory, path), budget) for path in paths}


def _compact(value):
    if len(json.dumps(value, ensure_ascii=False, default=str)) <= 320:
        return value
    if isinstance(value, (dict, list, str)):
        return {'kind': type(value).__name__, 'size': len(value)}
    return {'kind': type(value).__name__}


def _equal(actual, expected):
    if type(actual) is not type(expected):
        return False
    if isinstance(actual, dict):
        return actual.keys() == expected.keys() and all(_equal(actual[k], expected[k]) for k in actual)
    if isinstance(actual, list):
        return len(actual) == len(expected) and all(_equal(a, e) for a, e in zip(actual, expected))
    return actual == expected


def equal_values(actual, expected):
    """Type-strict structural equality shared with HTTP and rerun adapters."""
    return _equal(actual, expected)


def evaluate_checks(directory, checks):
    """Evaluate only caller-specified assertions; return bounded result summaries."""
    results, cache, budget = [], {}, [MAX_TOTAL_READ_BYTES]
    for index, check in enumerate(validate_checks(checks)):
        kind, name, expected = check['type'], check['path'], check['expected']
        item = {'id': check['id'], 'type': kind, 'path': name, 'passed': False,
                'actual': None, 'expected': _compact(expected)}
        try:
            path = scoped_file(directory, name)
            cache_path = str(path)
            if kind == 'file_exists':
                actual = path.is_file()
            elif kind == 'file_contains':
                if ('text', cache_path) not in cache:
                    cache['text', cache_path] = _text(path, budget)
                actual = expected in cache['text', cache_path]
                item['expected'] = {'contains': _compact(expected)}
                expected = True
            elif kind == 'json_value':
                if ('json', cache_path) not in cache:
                    cache['json', cache_path] = load_json(_text(path, budget))
                actual = json_pointer(cache['json', cache_path], check['pointer'])
            else:
                if ('csv', cache_path) not in cache:
                    cache['csv', cache_path] = _csv(_text(path, budget))
                header, rows = cache['csv', cache_path]
                required = set(check['where']) | ({check['column']} if 'column' in check else set())
                if not required <= set(header):
                    missing = required - set(header)
                    raise ValueError('field column/where: missing CSV columns ' + ', '.join(sorted(missing))
                                     + '; use existing header names')
                selected = [r for r in rows if all(r[k] == v for k, v in check['where'].items())]
                if kind == 'csv_row_count':
                    actual = len(selected)
                elif kind == 'csv_sum':
                    numbers = [_decimal(r[check['column']]) for r in selected]
                    # Avoid Decimal's default 28-digit rounding even for mixed exponents.
                    with localcontext() as context:
                        context.prec = 2100 + len(str(len(numbers)))
                        actual = sum(numbers, Decimal(0))
                    item['actual'], item['expected'] = str(actual), str(_decimal(expected))
                    item['passed'] = actual == _decimal(expected)
                    results.append(item)
                    continue
                elif kind == 'csv_value_counts':
                    actual = {}
                    for row in selected:
                        key = row[check['column']]
                        actual[key] = actual.get(key, 0) + 1
                else:
                    if any(set(row) != set(header) for row in expected):
                        raise ValueError('field expected: include every CSV column exactly in each complete row')
                    mismatches = sum(a != e for a, e in zip(selected, expected))
                    item['actual'] = {'rows': len(selected), 'mismatched_rows': mismatches,
                                      'extra_rows': max(0, len(selected) - len(expected)),
                                      'missing_rows': max(0, len(expected) - len(selected))}
                    item['expected'] = {'rows': len(expected)}
                    item['passed'] = selected == expected
                    results.append(item)
                    continue
            item['actual'] = _compact(actual)
            item['passed'] = _equal(actual, expected)
            if not item['passed']:
                item['reason'] = 'Artifact assertion differs from expected'
        except (OSError, UnicodeError, ValueError, TypeError, KeyError, IndexError, RecursionError) as exc:
            if isinstance(exc, OSError):
                reason = 'field path: artifact missing or unreadable; produce the artifact or correct its relative path'
            elif isinstance(exc, (KeyError, IndexError)):
                reason = 'field pointer: target absent; use an existing object key or array index'
            elif isinstance(exc, UnicodeError):
                reason = 'field path: artifact is not UTF-8; write a valid UTF-8 artifact'
            else:
                reason = str(exc) or 'Invalid artifact structure'
                if not reason.startswith('field '):
                    if kind == 'csv_sum':
                        reason = 'field column: ' + reason + '; use finite decimal entries in the selected column'
                    elif kind == 'json_value':
                        reason = 'field path/pointer: ' + reason + '; provide finite JSON and a valid pointer target'
                    else:
                        reason = 'field path: ' + reason + '; provide a valid artifact within the supported format and limits'
            item['reason'] = _error_context(index, check) + ': ' + reason[:200]
        results.append(item)
    return results
