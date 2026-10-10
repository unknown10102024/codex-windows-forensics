import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys

sys.dont_write_bytecode = True


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description='Reproduce the bundled OOXML attributions without changing documents.')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--compare', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    if args.output.exists():
        parser.error('Output already exists.')
    attribution_root = root.parent / 'ooxml_attribution'
    with (root / 'attribution_checksums.csv').open(newline='') as stream:
        for row in csv.DictReader(stream):
            path = attribution_root / row['path']
            if sha(path) != row['sha256']:
                raise ValueError('Attribution tool checksum mismatch: ' + row['path'])
    sys.path.insert(0, str(root.parent))
    from ooxml_attribution.attribution import attribute_document
    rules = json.loads((attribution_root / 'rules_v1.json').read_text())
    with (root / 'documents.csv').open(newline='') as stream:
        rows = list(csv.DictReader(stream))
    results = []
    with args.output.open('x', encoding='utf-8', newline='\n') as stream:
        for item in rows:
            relative = Path(item['path'])
            path = root / relative
            if relative.is_absolute() or root not in path.resolve().parents:
                raise ValueError('Invalid document path.')
            before = sha(path)
            if before != item['sha256'] or path.stat().st_size != int(item['bytes']):
                raise ValueError('Document checksum mismatch: ' + item['path'])
            if item['format'] not in ('DOCX', 'PPTX', 'XLSX'):
                continue
            result = attribute_document(path, rules)
            if sha(path) != before:
                raise ValueError('Document changed: ' + item['path'])
            result['path'] = item['path']
            if 'reason' in result:
                result['reason'] = result['reason'].replace(str(root), '<dataset>')
            stream.write(json.dumps(result, ensure_ascii=True, separators=(',', ':')) + '\n')
            stream.flush()
            results.append(result)
    summary = {'documents_verified': len(rows), 'ooxml_attributions': len(results),
               'non_parsed': sum(row['status'] != 'parsed' for row in results)}
    if args.compare:
        expected = [json.loads(line) for line in args.compare.read_text().splitlines() if line]
        expected_by_path = {row['path']: row for row in expected}
        actual_by_path = {row['path']: row for row in results}
        differences = sorted(path for path in expected_by_path.keys() | actual_by_path.keys()
                             if expected_by_path.get(path) != actual_by_path.get(path))
        summary['differences'] = differences
        summary['equal'] = not differences and len(expected) == len(results)
    print(json.dumps(summary, sort_keys=True))
    return 1 if summary['non_parsed'] or summary.get('equal') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())
