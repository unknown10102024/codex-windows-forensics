#!/usr/bin/env python3
"""Reproduce fixed metadata-name and ZIP-header baselines on released documents."""
import argparse
import csv
import hashlib
import json
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

MAX_FILE = 256 * 1024 * 1024
MAX_ENTRIES = 20_000
MAX_XML = 8 * 1024 * 1024
ROLES = ('template_lineage', 'last_packager')
PART_FIELDS = {'docProps/app.xml': ('Application',),
               'docProps/core.xml': ('creator', 'lastModifiedBy')}
COHORTS = {
    'creation_modification_creation': 'creation_modification',
    'creation_modification_modification': 'creation_modification',
    'human_original': 'creation_modification',
    'human_modification': 'creation_modification',
    'human_resave': 'creation_modification',
    'office_processed': 'outside_office',
    'new_human_created': 'outside_office',
    'm365_blank': 'outside_office',
    'risotto_creation': 'risotto',
    'risotto_modification': 'risotto',
}
TRUTH_NAMES = {
    'Microsoft Word': 'Office', 'Microsoft Excel': 'Office',
    'Microsoft PowerPoint': 'Office', 'Office': 'Office',
    '@oai/artifact-tool': 'artifact-tool', 'artifact-tool': 'artifact-tool',
    'python-docx': 'python-docx', 'Python zipfile': 'Python zipfile', 'JSZip': 'JSZip',
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def normalize(value):
    return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()


def metadata_values(archive, info, names):
    if info.flag_bits & 1 or info.file_size > MAX_XML:
        raise ValueError('Encrypted or oversized metadata part')
    with archive.open(info) as source:
        data = source.read(MAX_XML + 1)
    if len(data) > MAX_XML:
        raise ValueError('Oversized inflated metadata part')
    declarations = data.replace(b'\x00', b'').lower()
    if b'<!doctype' in declarations or b'<!entity' in declarations:
        raise ValueError('XML declarations are not supported')
    root = ET.fromstring(data)
    values = []
    for child in root:
        if isinstance(child.tag, str) and child.tag.rsplit('}', 1)[-1] in names:
            values.append(child.text or '')
    return values


def predictions(path, file_format, rules):
    """Inspect only the named property parts and one central-directory entry."""
    patterns = [(name, re.compile(re.escape(normalize(alias)) + rules['B1']['version_suffix_regex']))
                for name, aliases in rules['B1']['aliases'].items() for alias in aliases]
    result = {'B1': {'status': 'parsed', 'candidates': []},
              'B2': {'status': 'parsed', 'candidates': []}}
    try:
        if path.stat().st_size > MAX_FILE:
            raise ValueError('Input exceeds 256 MiB')
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ENTRIES:
                raise ValueError('Input exceeds 20000 ZIP entries')
            by_name = {}
            for info in entries:
                by_name.setdefault(info.filename, []).append(info)
            try:
                values = []
                for part, names in PART_FIELDS.items():
                    infos = by_name.get(part, [])
                    if len(infos) > 1:
                        raise ValueError('Duplicate metadata part')
                    if infos:
                        values.extend(metadata_values(archive, infos[0], names))
                result['B1']['candidates'] = sorted({name for value in values for name, regex in patterns
                                                     if regex.fullmatch(normalize(value))})
            except (ValueError, ET.ParseError, zipfile.BadZipFile, RuntimeError, OSError):
                result['B1']['status'] = 'inspection_failure'
            infos = by_name.get('[Content_Types].xml', [])
            if len(infos) != 1:
                result['B2']['status'] = 'inspection_failure'
            else:
                info = infos[0]
                values = {'flags': info.flag_bits, 'create_system': info.create_system,
                          'external_attr': info.external_attr, 'extract_version': info.extract_version}
                result['B2']['candidates'] = sorted({rule['target'] for rule in rules['B2']['conditions']
                    if rule['format'] == file_format and values[rule['field']] == rule['value']})
    except (ValueError, zipfile.BadZipFile, RuntimeError, OSError):
        result = {method: {'status': 'inspection_failure', 'candidates': []} for method in result}
    return result


def ground_truth(row, role):
    names = [row['first_authoring_tool']] if role == 'template_lineage' else json.loads(row['last_saver'])
    if len(names) != 1:
        raise ValueError('Expected a single recorded truth label or an explicit unknown')
    if names[0] in {'unknown', 'unresolved'}:
        return None
    return TRUTH_NAMES[names[0]]


def category(candidates, status, truth):
    if status != 'parsed':
        return 'inspection_failure'
    if truth is None:
        return 'truth_unknown'
    if not candidates:
        return 'unresolved'
    if len(candidates) == 1:
        return 'singleton_correct' if candidates[0] == truth else 'singleton_wrong'
    return 'multiple_contains_truth' if truth in candidates else 'multiple_excludes_truth'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, default=Path(__file__).resolve().parents[1] / 'dataset')
    parser.add_argument('--output', type=Path, default=Path(__file__).parent / 'results')
    args = parser.parse_args()
    rules_path = Path(__file__).with_name('rules.json')
    rules = json.loads(rules_path.read_text(encoding='utf-8'))
    labels_path = args.dataset / 'labels' / 'processing_truth.csv'
    label_hash = sha256(labels_path)
    with labels_path.open(encoding='utf-8-sig', newline='') as source:
        labels = list(csv.DictReader(source))
    selected = [row for row in labels if row['cohort'] in COHORTS]
    if len(selected) != 343 or len({row['path'] for row in selected}) != 343:
        raise ValueError('The published comparison requires 343 distinct document paths')
    records = []
    for row in selected:
        path = args.dataset / row['path']
        if sha256(path) != row['sha256']:
            raise ValueError('Document checksum differs from the label manifest')
        prediction = predictions(path, row['format'], rules)
        if sha256(path) != row['sha256']:
            raise ValueError('Document changed during attribution')
        for method in ('B1', 'B2'):
            for role in ROLES:
                candidates = [] if method == 'B2' and role == 'template_lineage' else prediction[method]['candidates']
                status = prediction[method]['status']
                truth = ground_truth(row, role)
                records.append({'document_id': row['document_id'], 'path': row['path'], 'cohort': row['cohort'],
                    'group': COHORTS[row['cohort']], 'format': row['format'], 'sha256': row['sha256'],
                    'method': method, 'role': role, 'truth': truth, 'candidates': candidates,
                    'status': status, 'category': category(candidates, status, truth)})
    summaries = []
    for group in ('all', 'creation_modification', 'outside_office', 'risotto'):
        for method in ('B1', 'B2'):
            for role in ROLES:
                rows = [r for r in records if r['method'] == method and r['role'] == role and
                        (group == 'all' or r['group'] == group)]
                known = [r for r in rows if r['truth'] is not None]
                counts = Counter(r['category'] for r in known)
                summaries.append({'group': group, 'method': method, 'role': role, 'documents': len(rows),
                    'truth_known': len(known), 'truth_unknown': len(rows) - len(known),
                    **{name: counts[name] for name in ('singleton_correct', 'multiple_contains_truth',
                       'singleton_wrong', 'multiple_excludes_truth', 'unresolved', 'inspection_failure')},
                    'wrong': counts['singleton_wrong'] + counts['multiple_excludes_truth']})
    if sha256(labels_path) != label_hash:
        raise ValueError('Labels changed during attribution')
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output / 'baseline_rows.jsonl').open('w', encoding='utf-8') as target:
        for record in records:
            target.write(json.dumps(record, ensure_ascii=False) + '\n')
    with (args.output / 'baseline_summary.csv').open('w', encoding='utf-8', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=list(summaries[0]))
        writer.writeheader()
        writer.writerows(summaries)
    receipt = {'documents': len(selected), 'label_manifest_sha256': label_hash,
               'rules_sha256': sha256(rules_path), 'script_sha256': sha256(Path(__file__)),
               'original_document_checksums_verified': len(selected),
               'summary': [row for row in summaries if row['group'] == 'all']}
    (args.output / 'reproduction.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
