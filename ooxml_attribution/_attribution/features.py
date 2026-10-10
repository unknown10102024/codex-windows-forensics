import json, re
from functools import lru_cache

@lru_cache(None)
def key(*v):
    return json.dumps(v, ensure_ascii=False, separators=(',', ':'))

@lru_cache(None)
def parts(k):
    return tuple(json.loads(k))

@lru_cache(None)
def shape(k):
    layer, part, node, field = parts(k)
    m = re.search('([^/{}\\[\\]]+)\\[\\d+\\]$', node)
    return (layer, part, node, field, m[1] if m else node, field.rsplit('}', 1)[-1])

def normalized(inv):
    facts = dict(inv['facts'])
    for k, v in list(facts.items()):
        layer, part, node, field = parts(k)
        if layer == 'zip' and field == 'extra':
            facts[key('zip_structure', part, node, 'extra_ids')] = [x['id'] for x in v]
            for x in v:
                for a in ('signature', 'zero_padding'):
                    if a in x:
                        facts[key('zip_structure', part, node, x['id'] + '.' + a)] = x[a]
    return {**inv, 'facts': facts}

def state(inv, k):
    if k in inv['facts']:
        v = inv['facts'][k]
        return {'state': 'empty' if v == '' else 'value', 'value': v}
    layer, part, node, field = parts(k)
    if layer == 'zip_structure':
        if inv['status'] == 'error':
            return {'state': 'unreadable', 'value': None}
        return {'state': 'field_absent' if key('part', part, '', 'present') in inv['facts'] else 'part_absent', 'value': None}
    if inv['status'] == 'error':
        return {'state': 'unreadable', 'value': None}
    if layer in ('part', 'xml', 'xml_document', 'zip'):
        if key('part', part, '', 'present') not in inv['facts']:
            s = 'part_absent'
        elif part in inv['part_errors'] or inv['xml_status'].get(part) == 'unreadable':
            s = 'unreadable'
        elif layer == 'xml' and key('xml', part, node, 'present') not in inv['facts']:
            s = 'element_absent'
        elif layer == 'xml' and field.startswith('@'):
            s = 'attribute_absent'
        else:
            s = 'field_absent'
    else:
        s = 'field_absent'
    return {'state': s, 'value': None}
