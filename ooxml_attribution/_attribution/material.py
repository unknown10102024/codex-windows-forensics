import posixpath, re
from collections import defaultdict
from urllib.parse import unquote, urlsplit
from .features import parts
VERSION = 'part_scope_v1'
CT_NS = 'http://schemas.openxmlformats.org/package/2006/content-types'
REL_NS = 'http://schemas.openxmlformats.org/package/2006/relationships'
OD_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
STRICT_REL = 'http://purl.oclc.org/ooxml/officeDocument/relationships/'
SHEET_NAMESPACES = frozenset({'http://schemas.openxmlformats.org/spreadsheetml/2006/main', 'http://purl.oclc.org/ooxml/spreadsheetml/main'})
WORKBOOK_CHILDREN = frozenset({'workbookPr', 'calcPr', 'fileVersion', 'webPublishing'})
MASTER_SCOPE_MODE = 'all_fields'
MASTER_KINDS = frozenset({'slideMaster', 'slideLayout', 'notesMaster', 'handoutMaster'})
PART_TYPES = (('word_styles', 'application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml', OD_REL + 'styles'), ('stylesWithEffects', 'application/vnd.ms-word.stylesWithEffects+xml', 'http://schemas.microsoft.com/office/2007/relationships/stylesWithEffects'), ('settings', 'application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml', OD_REL + 'settings'), ('webSettings', 'application/vnd.openxmlformats-officedocument.wordprocessingml.webSettings+xml', OD_REL + 'webSettings'), ('fontTable', 'application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml', OD_REL + 'fontTable'), ('numbering', 'application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml', OD_REL + 'numbering'), ('theme', 'application/vnd.openxmlformats-officedocument.theme+xml', OD_REL + 'theme'), ('core_properties', 'application/vnd.openxmlformats-package.core-properties+xml', REL_NS + '/metadata/core-properties'), ('extended_properties', 'application/vnd.openxmlformats-officedocument.extended-properties+xml', OD_REL + 'extended-properties'), ('custom_properties', 'application/vnd.openxmlformats-officedocument.custom-properties+xml', OD_REL + 'custom-properties'), ('customXml_properties', 'application/vnd.openxmlformats-officedocument.customXmlProperties+xml', OD_REL + 'customXmlProps'), ('presProps', 'application/vnd.openxmlformats-officedocument.presentationml.presProps+xml', OD_REL + 'presProps'), ('viewProps', 'application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml', OD_REL + 'viewProps'), ('tableStyles', 'application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml', OD_REL + 'tableStyles'), ('slideMaster', 'application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml', OD_REL + 'slideMaster'), ('slideLayout', 'application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml', OD_REL + 'slideLayout'), ('notesMaster', 'application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml', OD_REL + 'notesMaster'), ('handoutMaster', 'application/vnd.openxmlformats-officedocument.presentationml.handoutMaster+xml', OD_REL + 'handoutMaster'), ('workbook_styles', 'application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml', OD_REL + 'styles'))
WORKBOOK_TYPES = frozenset({'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml', 'application/vnd.openxmlformats-officedocument.spreadsheetml.template.main+xml', 'application/vnd.ms-excel.sheet.macroenabled.main+xml', 'application/vnd.ms-excel.template.macroenabled.main+xml', 'application/vnd.ms-excel.addin.macroenabled.main+xml'})
CT_KIND = {ct.lower(): kind for kind, ct, rel in PART_TYPES}
CT_KIND.update({ct: 'workbook' for ct in WORKBOOK_TYPES})
REL_KINDS = defaultdict(set)
for _kind, _ct, _rel in PART_TYPES:
    REL_KINDS[_rel].add(_kind)
    if _rel.startswith(OD_REL):
        REL_KINDS[STRICT_REL + _rel[len(OD_REL):]].add(_kind)
for _prefix in (OD_REL, STRICT_REL):
    REL_KINDS[_prefix + 'customXml'].add('customXml_data')
    REL_KINDS[_prefix + 'officeDocument'].update({'workbook', 'excluded_main_document'})
    for _suffix in ('header', 'footer', 'footnotes', 'endnotes', 'slide', 'notesSlide', 'worksheet', 'chartsheet', 'sharedStrings', 'calcChain', 'drawing', 'vmlDrawing', 'chart', 'image', 'audio', 'video', 'oleObject', 'package'):
        REL_KINDS[_prefix + _suffix].add('excluded_content')
ALLOWED_KINDS = frozenset(CT_KIND.values()) | {'customXml_data'}
GENERIC_XML_TYPES = frozenset({'application/xml', 'text/xml'})
_CT_NODE = re.compile('^/\\{' + re.escape(CT_NS) + '\\}Types\\[1\\]/\\{' + re.escape(CT_NS) + '\\}(Default|Override)\\[\\d+\\]$')
_REL_NODE = re.compile('^/\\{' + re.escape(REL_NS) + '\\}Relationships\\[1\\]/\\{' + re.escape(REL_NS) + '\\}Relationship\\[\\d+\\]$')
_QNODE = re.compile('/\\{([^}]+)\\}([^/\\[]+)\\[\\d+\\]')
_WORKBOOK_NODE = re.compile('^/\\{(' + '|'.join((re.escape(x) for x in sorted(SHEET_NAMESPACES))) + ')\\}workbook\\[1\\]/\\{\\1\\}(?:' + '|'.join(sorted(WORKBOOK_CHILDREN)) + ')\\[[1-9]\\d*\\]$')

def relationship_source(part):
    if part == '_rels/.rels':
        return ''
    directory, name = posixpath.split(part)
    if posixpath.basename(directory) != '_rels' or not name.endswith('.rels'):
        return None
    return posixpath.join(posixpath.dirname(directory), name[:-5])

def package_target(source, target):
    if not isinstance(target, str) or '\\' in target:
        return None
    uri = urlsplit(target)
    if uri.scheme or uri.netloc or uri.query or uri.fragment:
        return None
    path = unquote(uri.path)
    value = posixpath.normpath(path.lstrip('/') if path.startswith('/') else posixpath.join(posixpath.dirname(source), path))
    if value in ('', '.', '..') or value.startswith('../') or value.startswith('/'):
        return None
    return value

class ContextBuilder:

    def __init__(self, xml_status=None):
        self.xml_status = xml_status or {}
        self.present = set()
        self.ct_rows = defaultdict(dict)
        self.rel_rows = defaultdict(dict)
        self.metadata_roots = set()

    def add(self, k, value):
        if not (k.startswith('["part",') or k.startswith('["xml",')):
            return
        layer, part, node, field = parts(k)
        if layer == 'part' and field == 'present' and (value is True):
            self.present.add(part)
        elif layer == 'xml' and field == 'present' and (value is True):
            if part == '[Content_Types].xml' and node == '/{' + CT_NS + '}Types[1]':
                self.metadata_roots.add(part)
            elif relationship_source(part) is not None and node == '/{' + REL_NS + '}Relationships[1]':
                self.metadata_roots.add(part)
        elif layer == 'xml' and field.startswith('@'):
            if part == '[Content_Types].xml' and _CT_NODE.fullmatch(node) and (field in ('@PartName', '@Extension', '@ContentType')):
                self.ct_rows[node][field[1:]] = value
            elif relationship_source(part) is not None and _REL_NODE.fullmatch(node) and (field in ('@Type', '@Target', '@TargetMode', '@Id')):
                self.rel_rows[part, node][field[1:]] = value

    def finish(self):
        overrides = defaultdict(set)
        defaults = defaultdict(set)
        incoming = defaultdict(list)
        errors = []
        if '[Content_Types].xml' not in self.present or '[Content_Types].xml' not in self.metadata_roots or self.xml_status.get('[Content_Types].xml') not in (None, 'parsed'):
            errors.append('Content Types part missing or unreadable')
        for node, row in sorted(self.ct_rows.items()):
            ct = row.get('ContentType')
            if not isinstance(ct, str) or not ct:
                errors.append('invalid Content Types row ' + node)
                continue
            if _CT_NODE.fullmatch(node).group(1) == 'Override':
                target = package_target('', row.get('PartName'))
                if target is None:
                    errors.append('invalid Override part URI ' + node)
                else:
                    overrides[target].add(ct)
            else:
                ext = row.get('Extension')
                if isinstance(ext, str) and ext:
                    defaults[ext.lower()].add(ct)
                else:
                    errors.append('invalid Default extension ' + node)
        for part in sorted(self.present):
            if relationship_source(part) is not None and (part not in self.metadata_roots or self.xml_status.get(part) not in (None, 'parsed')):
                errors.append('unreadable or invalid relationships ' + part)
        for (part, node), row in sorted(self.rel_rows.items()):
            if row.get('TargetMode', 'Internal') == 'External':
                continue
            if row.get('TargetMode', 'Internal') != 'Internal':
                errors.append('invalid relationship TargetMode ' + part + ' ' + node)
                continue
            target = package_target(relationship_source(part), row.get('Target'))
            reltype = row.get('Type')
            if target is None or not isinstance(reltype, str) or (not reltype):
                errors.append('invalid internal relationship ' + part + ' ' + node)
                continue
            incoming[target].append({'source': relationship_source(part), 'relationship_part': part, 'type': reltype, 'target': row['Target']})
        output = {}
        for part in sorted(self.present | set(overrides) | set(incoming)):
            values = overrides.get(part) or defaults.get(part.rsplit('.', 1)[-1].lower(), set())
            rels = incoming.get(part, [])
            ctypes = sorted(values)
            normtypes = {x.lower() for x in ctypes}
            types = sorted({x['type'] for x in rels})
            relsets = [REL_KINDS[t] for t in types if t in REL_KINDS]
            ckind = CT_KIND.get(next(iter(normtypes))) if len(normtypes) == 1 else None
            status, kind, reason = ('unknown', '', 'no exact allowed content type or unambiguous relationship type')
            if part == '[Content_Types].xml' or relationship_source(part) is not None:
                status, kind, reason = ('typed', 'classification_metadata', 'OPC metadata is classification evidence only')
            elif len(normtypes) > 1:
                status, reason = ('conflict', 'conflicting content type declarations')
            elif ckind:
                if any((ckind not in rs for rs in relsets)):
                    status, reason = ('conflict', 'content type and relationship kinds disagree')
                else:
                    status, kind, reason = ('typed', ckind, 'exact dedicated content type; no contradictory recognized relationship')
            elif relsets:
                shared = set.intersection(*(set(x) for x in relsets))
                if len(shared) == 1:
                    rkind = next(iter(shared))
                    if rkind in ALLOWED_KINDS and (not normtypes or normtypes <= GENERIC_XML_TYPES):
                        status, kind, reason = ('typed', rkind, 'unambiguous internal relationship type with generic/missing content type')
                    elif rkind not in ALLOWED_KINDS:
                        status, kind, reason = ('typed', rkind, 'relationship denotes an excluded content part')
                    else:
                        status, reason = ('conflict', 'dedicated unlisted content type contradicts allowed relationship kind')
                elif not shared:
                    status, reason = ('conflict', 'conflicting relationship kinds')
            output[part] = {'status': status, 'kind': kind, 'content_type': ctypes, 'relationship_types': types, 'evidence': rels, 'present': part in self.present, 'reason': reason}
        return {'version': VERSION, 'parts': output, 'metadata_errors': sorted(set(errors))}

def context_from_facts(facts, xml_status=None):
    builder = ContextBuilder(xml_status)
    for k, value in facts.items() if hasattr(facts, 'items') else facts:
        builder.add(k, value)
    return builder.finish()

def material_scope(k, observed, context):
    layer, part, node, field = parts(k)
    annotation = {'material_scope_allowed': False, 'material_part_kind': '', 'material_scope_reason': ''}
    if layer not in ('part', 'xml'):
        annotation['material_scope_reason'] = 'material scope contains original part/XML fields only'
        return annotation
    evidence = (context or {}).get('parts', {}).get(part, {})
    annotation['material_part_kind'] = evidence.get('kind', '')
    if (context or {}).get('part_metadata_errors', {}).get(part, []) if (context or {}).get('relationship_scoped') else (context or {}).get('metadata_errors'):
        annotation['material_scope_reason'] = 'classification metadata incomplete or unreadable'
    elif evidence.get('status') != 'typed' or evidence.get('kind') not in ALLOWED_KINDS:
        annotation['material_scope_reason'] = evidence.get('reason', 'no own-document part-type evidence')
    elif evidence['kind'] == 'workbook':
        good = layer == 'xml' and bool(_WORKBOOK_NODE.fullmatch(node))
        annotation['material_scope_allowed'] = good
        annotation['material_scope_reason'] = 'explicit workbook settings leaf direct-child element only' if good else 'mixed workbook: only exact workbookPr/calcPr/fileVersion/webPublishing direct-child elements; no nested content or part/root aggregates'
    elif evidence['kind'] in MASTER_KINDS:
        assert MASTER_SCOPE_MODE == 'all_fields'
        annotation['material_scope_allowed'] = True
        annotation['material_scope_reason'] = 'allowed template or layout part'
    else:
        annotation['material_scope_allowed'] = True
        annotation['material_scope_reason'] = 'allowed template/settings/property part kind established in this document'
    return annotation

def material_unknown(k, observed, context, expected_state=None):
    if observed.get('state') == 'unreadable':
        return True
    if observed.get('state') == 'part_absent' and (expected_state or {}).get('state') != 'part_absent':
        return False
    return not material_scope(k, observed, context)['material_scope_allowed']

def resolve_package(z, fmt, selected_parts=None, *, read_part=None):
    from .opc import resolve_package as resolve
    return resolve(z, fmt, selected_parts, read_part=read_part)
