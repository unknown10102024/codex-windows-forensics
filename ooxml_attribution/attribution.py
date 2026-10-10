from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True

if __package__:
    from ._attribution.core import attribute_document as _attribute_document
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from ooxml_attribution._attribution.core import attribute_document as _attribute_document

FORMATS = {'.docx': 'DOCX', '.pptx': 'PPTX', '.xlsx': 'XLSX'}
LIST_FIELDS = ('template_lineage', 'last_packager')
FIELDS = ('path', 'format', 'status', *LIST_FIELDS, 'office_resave', 'reason')
LABELS = {
    'office': 'Office',
    'python_docx': 'python-docx',
    'artifact_tool': 'artifact-tool',
    'artifact_native': 'artifact-tool',
    'python': 'Python zipfile',
    'jszip': 'JSZip',
}


def office_resave(result):
    if result['status'] == 'error':
        return 'unknown'
    if result['office_trace']:
        return 'yes'
    if any(value in ('python_docx', 'artifact_tool') for value in result['material']):
        return 'no'
    return 'not_applicable'


def attribute_document(path, rules=None):
    try:
        result = _attribute_document(Path(path), rules)
    except Exception as error:
        return error_row(path, error)
    row = {key: result[key] for key in ('path', 'format', 'status')}
    for field, role in zip(LIST_FIELDS, ('material', 'packaging')):
        row[field] = [LABELS[value] for value in result[role]] or ['unknown']
    row['office_resave'] = office_resave(result)
    reasons = result.get('reasons', [])
    if reasons:
        row['reason'] = '; '.join(str(value) for value in reasons)
    return row


def error_row(path, error):
    return {
        'path': str(Path(path).absolute()),
        'format': FORMATS.get(Path(path).suffix.lower(), 'unknown'),
        'status': 'error',
        **{field: ['unknown'] for field in LIST_FIELDS},
        'office_resave': 'unknown',
        'reason': type(error).__name__ + ': ' + str(error),
    }


def rows_for_input(path, rules, excluded=()):
    excluded = {Path(value).resolve() for value in excluded}
    if not path.is_dir():
        yield attribute_document(path, rules)
        return
    failures = []
    for directory, directories, filenames in os.walk(path, followlinks=False, onerror=failures.append):
        directories.sort()
        for name in sorted(filenames):
            candidate = Path(directory) / name
            if candidate.suffix.lower() not in FORMATS:
                continue
            try:
                if candidate.resolve() in excluded:
                    continue
            except (OSError, RuntimeError) as error:
                yield error_row(candidate, error)
                continue
            yield attribute_document(candidate, rules)
    for error in failures:
        yield error_row(error.filename or path, error)


def main(argv=None):
    parser = argparse.ArgumentParser(description='Attribute three processing-path roles from OOXML files.')
    parser.add_argument('input', type=Path, help='An OOXML file or a folder to scan recursively')
    parser.add_argument('--out', type=Path, help='Write JSON Lines to a new file instead of stdout')
    parser.add_argument('--csv', type=Path, help='Also write a summary to a new CSV file')
    args = parser.parse_args(argv)
    if not args.input.is_dir() and args.input.suffix.lower() not in FORMATS:
        parser.error('Input must be a .docx, .pptx, .xlsx file or a folder')
    outputs = [value for value in (args.out, args.csv) if value is not None]
    if len({value.resolve() for value in outputs}) != len(outputs):
        parser.error('Output files must have different paths')
    for path in outputs:
        if path.exists():
            parser.error('Output already exists: ' + str(path))
    handles = []
    try:
        rules = json.loads((Path(__file__).resolve().parent / 'rules_v1.json').read_text(encoding='utf-8'))
        output = sys.stdout
        if args.out:
            output = args.out.open('x', encoding='utf-8', newline='\n')
            handles.append(output)
        writer = None
        if args.csv:
            stream = args.csv.open('x', encoding='utf-8', newline='')
            handles.append(stream)
            writer = csv.DictWriter(stream, fieldnames=FIELDS, lineterminator='\n')
            writer.writeheader()
        failed = False
        for row in rows_for_input(args.input, rules, outputs):
            output.write(json.dumps(row, ensure_ascii=True, separators=(',', ':')) + '\n')
            output.flush()
            if writer is not None:
                flat = dict(row)
                for field in LIST_FIELDS:
                    flat[field] = json.dumps(row[field], ensure_ascii=True, separators=(',', ':'))
                writer.writerow(flat)
            failed = failed or row['status'] != 'parsed'
        return 1 if failed else 0
    except (OSError, ValueError, KeyError) as error:
        parser.exit(2, type(error).__name__ + ': ' + str(error) + '\n')
    finally:
        for handle in handles:
            handle.close()


if __name__ == '__main__':
    raise SystemExit(main())
