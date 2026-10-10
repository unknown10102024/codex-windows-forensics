from .features import normalized, state
from .material import context_from_facts, material_unknown

def decide(inventory, rules):
    inventory = normalized(inventory)
    context = inventory.get('material_context')
    if context is None:
        context = context_from_facts(inventory['facts'], inventory.get('xml_status'))
    supported = {}
    memo = {}
    for family in rules['families']:
        if family['format'] != inventory['format'] or not family['when']:
            continue
        matched = True
        for atom in family['when']:
            key = atom['key']
            if key not in memo:
                memo[key] = state(inventory, key)
            value = memo[key]
            unavailable = value.get('state') == 'unreadable' or (family['role'] == 'packaging' and value.get('state') == 'part_absent')
            if family['role'] == 'material':
                unavailable = material_unknown(key, value, context, atom['state'])
            if unavailable or value != atom['state']:
                matched = False
        if matched:
            supported.setdefault(family['role'], set()).add(family['target'])
    material = supported.get('material', set())
    return {'material': sorted(material), 'packaging': sorted(supported.get('packaging', set())), 'office_trace': sorted(supported.get('office_trace', set()) & material)}
