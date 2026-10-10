from collections import Counter, defaultdict, deque
import posixpath
from .features import key
from .material import ContextBuilder, CT_NS, REL_NS, OD_REL, STRICT_REL, WORKBOOK_TYPES, package_target, relationship_source, REL_KINDS
MAIN = {'DOCX': 'word/document.xml', 'XLSX': 'xl/workbook.xml', 'PPTX': 'ppt/presentation.xml'}
MAIN_TYPES = {'DOCX': frozenset({'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml', 'application/vnd.openxmlformats-officedocument.wordprocessingml.template.main+xml', 'application/vnd.ms-word.document.macroenabled.main+xml', 'application/vnd.ms-word.template.macroenabledtemplate.main+xml'}), 'XLSX': WORKBOOK_TYPES, 'PPTX': frozenset({'application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml', 'application/vnd.openxmlformats-officedocument.presentationml.template.main+xml', 'application/vnd.openxmlformats-officedocument.presentationml.slideshow.main+xml', 'application/vnd.ms-powerpoint.presentation.macroenabled.main+xml', 'application/vnd.ms-powerpoint.template.macroenabled.main+xml', 'application/vnd.ms-powerpoint.slideshow.macroenabled.main+xml'})}
CANONICAL_KINDS = {'docProps/core.xml': 'core_properties', 'docProps/app.xml': 'extended_properties', 'docProps/custom.xml': 'custom_properties', 'word/styles.xml': 'word_styles', 'word/stylesWithEffects.xml': 'stylesWithEffects', 'word/settings.xml': 'settings', 'word/webSettings.xml': 'webSettings', 'word/fontTable.xml': 'fontTable', 'word/numbering.xml': 'numbering', 'word/theme/theme1.xml': 'theme', 'xl/theme/theme1.xml': 'theme', 'xl/styles.xml': 'workbook_styles', 'ppt/theme/theme1.xml': 'theme', 'ppt/presProps.xml': 'presProps', 'ppt/viewProps.xml': 'viewProps', 'ppt/tableStyles.xml': 'tableStyles', 'ppt/slideLayouts/slideLayout1.xml': 'slideLayout', 'ppt/slideMasters/slideMaster1.xml': 'slideMaster'}

def relationship_part(source):
    if not source:
        return '_rels/.rels'
    directory, name = posixpath.split(source)
    return posixpath.join(directory, '_rels', name + '.rels')

def _default_reader(z):
    from .. import scanner
    total = 0
    cache = {}

    def read(name):
        nonlocal total
        if name in cache:
            return cache[name]
        i = z.getinfo(name)
        if i.file_size > scanner.MAX_PART:
            raise ValueError('part size limit')
        data = bytearray()
        with z.open(i) as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b''):
                total += len(chunk)
                if len(data) + len(chunk) > scanner.MAX_PART:
                    raise ValueError('part size limit')
                if total > scanner.MAX_TOTAL:
                    raise ValueError('selected parts total size limit')
                data.extend(chunk)
        cache[name] = bytes(data)
        return cache[name]
    return read

def _metadata(z, read):
    from lxml import etree as ET
    from .. import scanner
    entries = z.infolist()
    names = {i.filename: i for i in entries}
    if len(entries) != len(names):
        raise ValueError('duplicate ZIP names')
    if len(entries) > scanner.MAX_ENTRIES:
        raise ValueError('ZIP entry count limit')
    builder = ContextBuilder()
    builder.present.update(names)
    errors = defaultdict(list)
    relations = []
    types = []
    for name in names:
        isct = name == '[Content_Types].xml'
        if not isct and relationship_source(name) is None:
            continue
        try:
            if names[name].file_size > scanner.MAX_PART:
                raise ValueError('part size limit')
            raw = read(name)
            if b'<!doctype' in raw.replace(b'\x00', b'').lower():
                raise ValueError('DTD not supported')
            tree = ET.fromstring(raw, ET.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, recover=False, huge_tree=False))
            expected = '{' + (CT_NS if isct else REL_NS) + '}' + ('Types' if isct else 'Relationships')
            if tree.tag != expected:
                raise ValueError('unexpected metadata root')
            builder.metadata_roots.add(name)
            counts = Counter()
            ids = Counter()
            for ordinal, e in enumerate(tree, 1):
                if not isinstance(e.tag, str):
                    continue
                counts[e.tag] += 1
                node = '/' + tree.tag + '[1]/' + e.tag + f'[{counts[e.tag]}]'
                for field, value in e.attrib.items():
                    builder.add(key('xml', name, node, '@' + field), value)
                if isct:
                    if e.tag not in ('{' + CT_NS + '}Override', '{' + CT_NS + '}Default'):
                        errors[name].append('unexpected Content Types child ' + node)
                        continue
                    attr = dict(e.attrib)
                    attr.update(node=node, ordinal=ordinal)
                    types.append(attr)
                    if not e.get('ContentType') or (e.tag.endswith('Override') and package_target('', e.get('PartName')) is None) or (e.tag.endswith('Default') and (not e.get('Extension'))):
                        errors[name].append('invalid Content Types row ' + node)
                elif e.tag == '{' + REL_NS + '}Relationship':
                    rid = e.get('Id')
                    mode = e.get('TargetMode', 'Internal')
                    typ = e.get('Type')
                    if rid:
                        ids[rid] += 1
                    source = relationship_source(name)
                    target = package_target(source, e.get('Target')) if mode == 'Internal' else None
                    row = {'source': source, 'relationship_part': name, 'id': rid, 'type': typ, 'target': e.get('Target'), 'target_mode': mode, 'resolved_target': target, 'node': node, 'ordinal': ordinal}
                    relations.append(row)
                    if not rid or not typ or mode not in ('Internal', 'External') or (mode == 'Internal' and target is None):
                        errors[name].append('invalid relationship ' + node)
                else:
                    errors[name].append('unexpected relationships child ' + node)
            for rid, count in ids.items():
                if count > 1:
                    errors[name].append('duplicate relationship Id ' + rid)
            builder.xml_status[name] = 'parsed'
        except Exception as ex:
            builder.xml_status[name] = 'unreadable'
            errors[name].append(str(ex))
    context = builder.finish()
    return (names, context, relations, types, errors)

def _main(fmt, names, context, relations, types, errors):
    if fmt not in MAIN:
        raise ValueError('unsupported OPC document format')
    if '_rels/.rels' not in names or errors['_rels/.rels']:
        raise ValueError('OPC root relationships missing or unreadable/ambiguous')
    if '[Content_Types].xml' not in names or errors['[Content_Types].xml']:
        raise ValueError('OPC Content Types missing or unreadable/ambiguous')
    main = [r for r in relations if r['source'] == '' and r['type'] in (OD_REL + 'officeDocument', STRICT_REL + 'officeDocument')]
    if len(main) != 1:
        raise ValueError('OPC main officeDocument relationship is not unique')
    row = main[0]
    target = row['resolved_target']
    if row['target_mode'] != 'Internal' or target is None or target not in names:
        raise ValueError('OPC main part is external, invalid or absent')
    overrides = [t for t in types if 'PartName' in t and package_target('', t['PartName']) == target]
    declarations = overrides or [t for t in types if 'Extension' in t and t['Extension'].lower() == target.rsplit('.', 1)[-1].lower()]
    if len(declarations) != 1 or declarations[0].get('ContentType', '').lower() not in MAIN_TYPES[fmt]:
        raise ValueError('OPC main Content Type is missing, duplicate or incompatible')
    return (target, {'relationship': dict(row), 'content_type': dict(declarations[0])})

def resolve_package(z, fmt, selected_parts=None, *, read_part=None):
    read = read_part or _default_reader(z)
    names, context, relations, types, errors = _metadata(z, read)
    main, main_evidence = _main(fmt, names, context, relations, types, errors)
    edges = defaultdict(list)
    reverse = defaultdict(list)
    for r in relations:
        if r['target_mode'] == 'Internal' and r['resolved_target'] in names and (r['source'] in names | {'': None}):
            edges[r['source']].append(r)
            reverse[r['resolved_target']].append(r)
    reachable = {''}
    q = deque([''])
    while q:
        for r in edges[q.popleft()]:
            if r['resolved_target'] not in reachable:
                reachable.add(r['resolved_target'])
                q.append(r['resolved_target'])
    aliases = {MAIN[fmt]: main}
    alias_evidence = {MAIN[fmt]: {'physical_part': main, **main_evidence}}
    canonical_rel = relationship_part(MAIN[fmt])
    physical_rel = relationship_part(main)
    if physical_rel in names:
        aliases[canonical_rel] = physical_rel
    chosen = set(selected_parts or context['parts'])
    unresolved = {}
    for part in sorted(chosen):
        if part in aliases or part in names:
            continue
        kind = CANONICAL_KINDS.get(part)
        if not kind:
            continue
        targets = {r['resolved_target'] for r in relations if r['source'] in reachable and r['target_mode'] == 'Internal' and (r['resolved_target'] in names) and (kind in REL_KINDS.get(r['type'], ())) and (context['parts'].get(r['resolved_target'], {}).get('kind') == kind) and (context['parts'].get(r['resolved_target'], {}).get('status') == 'typed')}
        if len(targets) == 1:
            physical = next(iter(targets))
            aliases[part] = physical
            alias_evidence[part] = {'physical_part': physical, 'relationships': [dict(r) for r in reverse[physical]], 'content_type': context['parts'][physical]['content_type']}
        elif len(targets) > 1:
            unresolved[part] = 'multiple reachable physical parts of required OPC kind'
    scoped = {}
    paths = {}
    for canonical in chosen | set(aliases):
        physical = aliases.get(canonical, canonical)
        ancestors = {physical}
        queue = deque([physical])
        relparts = set()
        while queue:
            for r in reverse[queue.popleft()]:
                if r['source'] not in reachable:
                    continue
                relparts.add(r['relationship_part'])
                if r['source'] not in ancestors:
                    ancestors.add(r['source'])
                    queue.append(r['source'])
        messages = list(errors['[Content_Types].xml'])
        if '' not in ancestors:
            messages.extend(context['metadata_errors'])
            for rp, errs in errors.items():
                messages.extend((rp + ': ' + e for e in errs))
            kind = context['parts'].get(physical, {}).get('kind')
            for r in relations:
                if r['source'] in reachable and kind and (kind in REL_KINDS.get(r['type'], ())) and (r['target_mode'] != 'Internal' or r['resolved_target'] is None):
                    messages.append(r['relationship_part'] + ': invalid or external relationship for selected part kind')
        for rp in sorted(relparts):
            messages.extend((rp + ': ' + e for e in errors[rp]))
        declarations = [t for t in types if 'PartName' in t and package_target('', t['PartName']) == physical]
        if len(declarations) > 1:
            messages.append('duplicate Content Types declarations for selected part')
        if canonical in unresolved:
            messages.append(unresolved[canonical])
        scoped[canonical] = sorted(set(messages))
        paths[canonical] = {'physical_part': physical, 'relationship_parts': sorted(relparts)}
        if canonical != physical and physical in context['parts']:
            context['parts'][canonical] = context['parts'][physical]
    context.update(relationship_scoped=True, part_metadata_errors=scoped, part_relationship_paths=paths, metadata_part_errors={k: v for k, v in errors.items() if v})
    return {'main_part': main, 'part_aliases': aliases, 'alias_evidence': alias_evidence, 'unresolved_aliases': unresolved, 'context': context}
