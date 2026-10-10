import json
from functools import lru_cache
from pathlib import Path
from .. import scanner
from .infer import decide
from .projection import project, stream_sha
from . import projection
RULES_PATH = Path(__file__).resolve().parents[1] / 'rules_v1.json'
FORMATS = frozenset({'DOCX', 'PPTX', 'XLSX'})
ROLES = frozenset({'material', 'packaging', 'office_trace'})

@lru_cache(maxsize=1)
def load_rules():
    with RULES_PATH.open(encoding='utf-8') as stream:
        rules = json.load(stream)
    validate_rules(rules)
    return rules

def validate_rules(rules):
    if rules.get('name') != 'rules_v1' or not isinstance(rules.get('families'), list):
        raise ValueError('unsupported rules format')
    for family in rules['families']:
        if family['role'] not in ROLES or family['format'] not in FORMATS or family['when_mode'] != 'AND':
            raise ValueError('unsupported rule family')
    policy = rules.get('packaging_consistency')
    expected = {'policy', 'enabled', 'reference_zlib_runtime'}
    if not isinstance(policy, dict) or set(policy) != expected or policy['policy'] != 'artifact_native_deflate6_v1' or (type(policy['enabled']) is not bool) or (policy['reference_zlib_runtime'] != '1.3'):
        raise ValueError('unsupported packaging consistency policy')

def apply_packaging_consistency(result, inventory, policy):
    if not policy['enabled'] or 'artifact_native' not in result['packaging']:
        return
    if inventory.get('status') != 'parsed' or inventory.get('part_errors'):
        observation = {'status': 'partial_projection', 'all_deflate_level6': None}
    elif not inventory.get('path'):
        observation = {'status': 'source_unavailable', 'all_deflate_level6': None}
    else:
        observation = projection.observe_deflate_level6(Path(inventory['path']), inventory['sha256'], reference_zlib_runtime=policy['reference_zlib_runtime'])
    consistent = observation['all_deflate_level6']
    if consistent is False:
        return
    if consistent is None:
        result['reasons'].append('Packaging consistency check not applied: ' + observation['status'] + '; existing candidates retained')
        return
    assert consistent is True and observation['status'] == 'ok' and (observation['deflate_count'] > 0)
    result['packaging'] = [value for value in result['packaging'] if value != 'artifact_native']
    result['reasons'].append('Packaging support withheld: all ' + str(observation['deflate_count']) + ' DEFLATE members exactly match raw zlib level 6; no replacement writer inferred')

def attribute_document(path, rules=None):
    path = Path(path)
    result = {'path': str(path.resolve()), 'format': path.suffix.lstrip('.').upper(), 'status': 'error', 'material': [], 'packaging': [], 'office_trace': [], 'reasons': []}
    try:
        if result['format'] not in FORMATS:
            raise ValueError('Only DOCX, PPTX and XLSX are supported')
        if path.stat().st_size > scanner.MAX_FILE:
            raise ValueError('input size limit exceeded')
        if rules is None:
            rules = load_rules()
        else:
            validate_rules(rules)
        inventory, _ = project(path, stream_sha(path), result['format'], rules)
        inventory['path'] = str(path.resolve())
        result.update(decide(inventory, rules))
        result['status'] = inventory['status']
        result['reasons'] = [error.get('part', '') + ': ' + error['error'] for error in inventory['errors']]
        apply_packaging_consistency(result, inventory, rules['packaging_consistency'])
    except Exception as exc:
        result.update(status='error', material=[], packaging=[], office_trace=[], reasons=[type(exc).__name__ + ': ' + str(exc)])
    return result
