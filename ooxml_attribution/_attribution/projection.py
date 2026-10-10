import json, zipfile, hashlib, struct
import mmap, tempfile, time, zlib
from collections import Counter, defaultdict
from pathlib import Path
from .. import scanner
from .features import key, parts
from .material import resolve_package
CENTRAL_FIELDS = ('signature', 'version_made_by', 'version_needed', 'flags', 'compression', 'time_raw', 'date_raw', 'crc32_raw', 'compressed_size_raw', 'size_raw', 'name_length', 'extra_length', 'comment_length', 'disk_number', 'internal_attr', 'external_attr', 'local_offset_raw')
LOCAL_FIELDS = ('signature', 'version_needed', 'flags', 'compression', 'time_raw', 'date_raw', 'crc32_raw', 'compressed_size_raw', 'size_raw', 'name_length', 'extra_length')

class _BoundedPartReader:

    def __init__(self, stream, budget, name):
        self.stream = stream
        self.budget = budget
        self.name = name
        self.count = 0

    def read(self, size=-1):
        remaining = min(scanner.MAX_PART - self.count, scanner.MAX_TOTAL - self.budget.total)
        requested = remaining + 1 if size is None or size < 0 else min(size, remaining + 1)
        data = self.stream.read(requested)
        self.count += len(data)
        self.budget.total += len(data)
        self.budget.by_part[self.name] += len(data)
        if self.count > scanner.MAX_PART:
            raise ValueError('part size limit')
        if self.budget.total > scanner.MAX_TOTAL:
            raise ValueError('selected read total size limit')
        return data

    def close(self):
        self.stream.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

class _ReadBudget:

    def __init__(self, archive):
        self.archive = archive
        self.total = 0
        self.by_part = Counter()

    def open(self, name):
        info = name if isinstance(name, zipfile.ZipInfo) else self.archive.getinfo(name)
        if info.file_size > scanner.MAX_PART:
            raise ValueError('part size limit')
        if self.total > scanner.MAX_TOTAL:
            raise ValueError('selected read total size limit')
        return _BoundedPartReader(self.archive.open(info), self, info.filename)

    def read_part(self, name):
        with self.open(name) as f:
            return f.read()

def stream_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda: f.read(1024 * 1024), b''):
            h.update(b)
    return h.hexdigest()

def dependencies(keys):
    keys = set(keys)
    for k in list(keys):
        layer, p, node, field = parts(k)
        if layer in ('part', 'xml', 'xml_document', 'zip', 'zip_structure'):
            keys.add(key('part', p, '', 'present'))
        if layer == 'xml':
            keys.add(key('xml', p, node, 'present'))
        if layer == 'zip_structure':
            keys.add(key('zip', p, node, 'extra'))
    return keys

def zip_archive_projection(f, z, size, keys, inv):
    requested = {parts(k)[3]: k for k in keys if parts(k)[:3] == ('zip_archive', '', '')}
    if not requested:
        return
    values = {'comment_hex': z.comment.hex(), 'entry_order': z.namelist()}
    f.seek(max(0, size - 65557))
    tail = f.read()
    for pos in range(len(tail) - 22, -1, -1):
        if tail[pos:pos + 4] == b'PK\x05\x06':
            vals = struct.unpack_from('<4s4H2IH', tail, pos)
            if pos + 22 + vals[-1] == len(tail):
                names = ('signature', 'disk', 'central_disk', 'disk_entries', 'total_entries', 'central_size', 'central_offset', 'comment_length')
                values.update({name: v.hex() if isinstance(v, bytes) else v for name, v in zip(names, vals)})
                values['eocd_offset'] = size - len(tail) + pos
                break
    for field, k in requested.items():
        if field in values:
            inv['facts'][k] = values[field]

def zip_header_projection(f, info, cursor, requested, inv):
    f.seek(cursor)
    vals = struct.unpack('<4s6H3I5H2I', f.read(46))
    if vals[0] != b'PK\x01\x02':
        raise ValueError('bad central header')
    cn, ce, cc = (f.read(vals[10]), f.read(vals[11]), f.read(vals[12]))
    next_cursor = cursor + 46 + len(cn) + len(ce) + len(cc)
    if not requested:
        return next_cursor
    name = info.filename
    central = {fld: val.hex() if isinstance(val, bytes) else val for fld, val in zip(CENTRAL_FIELDS, vals)}
    central.update(create_system=info.create_system, create_version=info.create_version, extract_version=info.extract_version, comment_hex=cc.hex())
    if ('central', 'extra') in requested:
        central['extra'] = scanner.extra_fields(ce)
    for (node, field), destinations in requested.items():
        if node == 'central' and field in central:
            for k in destinations if isinstance(destinations, list) else [destinations]:
                inv['facts'][k] = central[field]
    if any((node == 'local' for node, field in requested)):
        f.seek(info.header_offset)
        lv = struct.unpack('<4s5H3I2H', f.read(30))
        if lv[0] != b'PK\x03\x04':
            raise ValueError('bad local header')
        ln, le = (f.read(lv[-2]), f.read(lv[-1]))
        local = {fld: val.hex() if isinstance(val, bytes) else val for fld, val in zip(LOCAL_FIELDS, lv)}
        local['zip_time'] = scanner.dos_date(lv[5], lv[4])
        if ('local', 'extra') in requested:
            local['extra'] = scanner.extra_fields(le)
        for (node, field), destinations in requested.items():
            if node == 'local' and field in local:
                for k in destinations if isinstance(destinations, list) else [destinations]:
                    inv['facts'][k] = local[field]
    return next_cursor

def xml_projection(z, name, keys, inv, logical_name=None):
    from lxml import etree as ET
    logical_name = logical_name or name
    destination = inv
    inv = {**inv, 'facts': {}, 'xml_status': {}}
    tail = b''
    with z.open(name) as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            s = (tail + b).replace(b'\x00', b'').lower()
            if b'<!doctype' in s:
                raise ValueError('DTD not supported')
            tail = b[-32:]
    wanted = defaultdict(set)
    for k in keys:
        l, p, n, field = parts(k)
        if l == 'xml' and p == logical_name:
            wanted[n].add(field)
    stack = []
    top = Counter()
    top_children = []
    document_tree = None

    def tag(e):
        if isinstance(e, ET._Comment):
            return '#comment'
        if isinstance(e, ET._ProcessingInstruction):
            return '#pi:' + e.target
        return str(e.tag)
    with z.open(name) as f:
        for event, e in ET.iterparse(f, events=('start', 'end', 'comment', 'pi'), resolve_entities=False, load_dtd=False, no_network=True, recover=False, huge_tree=False):
            if event in ('start', 'comment', 'pi'):
                t = tag(e)
                counts = stack[-1]['counts'] if stack else top
                counts[t] += 1
                if not stack:
                    top_children.append(t)
                node = (stack[-1]['node'] if stack else '') + '/' + t + f'[{counts[t]}]'
                if stack and stack[-1]['children'] is not None:
                    stack[-1]['children'].append(t)
                fields = wanted.get(node, set())
                if 'present' in fields:
                    inv['facts'][key('xml', logical_name, node, 'present')] = True
                if event != 'start':
                    if 'text' in fields:
                        inv['facts'][key('xml', logical_name, node, 'text')] = e.text or ''
                    continue
                if document_tree is None:
                    document_tree = e.getroottree()
                for field in fields:
                    if field.startswith('@') and field[1:] in e.attrib:
                        inv['facts'][key('xml', logical_name, node, field)] = e.attrib[field[1:]]
                stack.append({'node': node, 'fields': fields, 'counts': Counter(), 'children': [] if 'children' in fields else None})
            else:
                frame = stack.pop()
                node = frame['node']
                if 'text' in frame['fields']:
                    inv['facts'][key('xml', logical_name, node, 'text')] = e.text or ''
                if frame['children'] is not None:
                    inv['facts'][key('xml', logical_name, node, 'children')] = frame['children']
                e.clear()
                parent = e.getparent()
                if parent is not None:
                    while e.getprevious() is not None:
                        del parent[0]
    if document_tree is not None:
        info = document_tree.docinfo
        document_info = {'xml_version': info.xml_version, 'encoding': info.encoding, 'standalone': info.standalone, 'doctype': info.doctype, 'children': top_children}
        for field, value in document_info.items():
            k = key('xml_document', logical_name, '', field)
            if k in keys:
                inv['facts'][k] = value
    inv['xml_status'][logical_name] = 'parsed'
    destination['facts'].update(inv['facts'])
    destination['xml_status'].update(inv['xml_status'])

def from_file(path, keys, expected, fmt):
    keys = dependencies(keys)
    path = Path(path)
    if fmt not in ('DOCX', 'XLSX', 'PPTX') or path.suffix.lower() not in ('.docx', '.xlsx', '.pptx'):
        raise ValueError('Only DOCX, XLSX and PPTX are supported')
    if path.stat().st_size > scanner.MAX_FILE:
        raise ValueError('input size limit exceeded')
    if stream_sha(path) != expected:
        raise ValueError('input SHA-256 changed before projection')
    inv = {'sha256': expected, 'format': fmt, 'size': path.stat().st_size, 'facts': {}, 'xml_status': {}, 'part_errors': {}, 'errors': [], 'status': 'parsed', 'projection_scope': 'selected fields only'}
    if inv['size'] > scanner.MAX_FILE:
        raise ValueError('input size limit exceeded')
    with zipfile.ZipFile(path) as z, path.open('rb') as raw_zip:
        entries = z.infolist()
        names = {i.filename: i for i in entries}
        if len(entries) != len(names):
            raise ValueError('duplicate ZIP names')
        if len(entries) > scanner.MAX_ENTRIES:
            raise ValueError('ZIP entry limit')
        budget = _ReadBudget(z)
        selected = {parts(k)[1] for k in keys if parts(k)[0] in ('xml', 'xml_document', 'part', 'zip', 'zip_structure')}
        package = resolve_package(z, fmt, selected_parts=selected, read_part=budget.read_part)
        if budget.total > scanner.MAX_TOTAL:
            raise ValueError('selected read total size limit')
        aliases = package['part_aliases']
        inv['material_context'] = package['context']
        inv['part_aliases'] = aliases
        inv['main_part'] = package['main_part']
        inv['alias_evidence'] = package.get('alias_evidence', {})
        keys = dependencies(keys)
        zip_archive_projection(raw_zip, z, inv['size'], keys, inv)
        requested = defaultdict(lambda: defaultdict(list))
        for k in keys:
            layer, p, node, field = parts(k)
            if layer == 'zip':
                requested[aliases.get(p, p)][node, field].append(k)
        cursor = z.start_dir
        for i in entries:
            cursor = zip_header_projection(raw_zip, i, cursor, requested.get(i.filename, {}), inv)
        for name in sorted({parts(k)[1] for k in keys if parts(k)[0] in ('xml', 'xml_document', 'part', 'zip', 'zip_structure')}):
            physical = aliases.get(name, name)
            if physical not in names:
                continue
            i = names[physical]
            inv['facts'][key('part', name, '', 'present')] = True
            if any((parts(k)[0] in ('xml', 'xml_document') and parts(k)[1] == name for k in keys)):
                try:
                    if i.file_size > scanner.MAX_PART:
                        raise ValueError('part size limit')
                    xml_projection(budget, physical, keys, inv, logical_name=name)
                except Exception as ex:
                    inv['part_errors'][name] = str(ex)
                    inv['xml_status'][name] = 'unreadable'
                    inv['errors'].append({'part': name, 'error': str(ex)})
                    inv['status'] = 'partial'
        inv['projection_bounds'] = {'declared_total_bytes': sum((i.file_size for i in entries)), 'inflated_bytes_read': budget.total, 'inflated_bytes_by_part': dict(budget.by_part), 'max_part': scanner.MAX_PART, 'max_total_read': scanner.MAX_TOTAL, 'max_entries': scanner.MAX_ENTRIES}
    if stream_sha(path) != expected:
        raise ValueError('input SHA-256 changed during projection')
    return inv
_DEFLATE_CHUNK = 128 * 1024

class _DeflateObservationError(ValueError):
    pass

def _deflate_payload_offset(raw, info, boundary):
    if info.header_offset < 0 or info.header_offset + 30 > boundary:
        raise _DeflateObservationError('local header outside member boundary')
    raw.seek(info.header_offset)
    header = raw.read(30)
    if len(header) != 30:
        raise _DeflateObservationError('truncated local header')
    signature, version, flags, method, clock, date, crc, compressed, size, name_n, extra_n = struct.unpack('<IHHHHHIIIHH', header)
    if signature != 67324752:
        raise _DeflateObservationError('invalid local header signature')
    if method != info.compress_type or flags != info.flag_bits:
        raise _DeflateObservationError('local/central compression method or flags differ')
    offset = info.header_offset + 30 + name_n + extra_n
    if info.compress_size < 0 or offset + info.compress_size > boundary:
        raise _DeflateObservationError('compressed payload crosses member boundary')
    return offset

def _match_deflate_level6(zipped, raw, info, offset, max_part):
    position = 0
    matches = True
    decoded = 0

    def compare(output):
        nonlocal position, matches
        start = position
        position += len(output)
        if not matches:
            return
        if position > info.compress_size:
            matches = False
            return
        raw.seek(offset + start)
        view = memoryview(output)
        for index in range(0, len(output), _DEFLATE_CHUNK):
            n = min(_DEFLATE_CHUNK, len(output) - index)
            block = raw.read(n)
            if len(block) != n:
                raise _DeflateObservationError('truncated compressed payload')
            if block != view[index:index + n]:
                matches = False
                break
    with tempfile.TemporaryFile(prefix='ooxml-deflate6-') as spool:
        with zipped.open(info, 'r') as payload:
            while True:
                chunk = payload.read(_DEFLATE_CHUNK)
                if not chunk:
                    break
                decoded += len(chunk)
                if decoded > info.file_size or decoded > max_part:
                    raise _DeflateObservationError('decoded member exceeds declared size or limit')
                spool.write(chunk)
        if decoded != info.file_size:
            raise _DeflateObservationError('decoded size differs from central size')
        spool.flush()
        mapped = mmap.mmap(spool.fileno(), 0, access=mmap.ACCESS_READ) if decoded else None
        try:
            compressor = zlib.compressobj(6, zlib.DEFLATED, -15, 8, zlib.Z_DEFAULT_STRATEGY)
            compare(compressor.compress(mapped if mapped is not None else b''))
            compare(compressor.flush(zlib.Z_FINISH))
        finally:
            if mapped is not None:
                mapped.close()
    return matches and position == info.compress_size

def observe_deflate_level6(path, expected_sha256, *, limits=None, reference_zlib_runtime='1.3'):
    started = time.perf_counter()
    path = Path(path)
    bounds = {'max_file': scanner.MAX_FILE, 'max_part': scanner.MAX_PART, 'max_total': scanner.MAX_TOTAL, 'max_compressed_total': scanner.MAX_FILE, 'max_entries': scanner.MAX_ENTRIES}
    if limits is not None:
        if not isinstance(limits, dict) or set(limits) - set(bounds):
            raise ValueError('unknown DEFLATE observation limit')
        for name, value in limits.items():
            if type(value) is not int or not 0 <= value <= bounds[name]:
                raise ValueError('DEFLATE limits may only reduce scanner limits')
        bounds.update(limits)
    result = {'status': 'unavailable', 'all_deflate_level6': None, 'entry_count': 0, 'deflate_count': 0, 'deflate_checked': 0, 'stored_count': 0, 'stored_checked': 0, 'errors': [], 'elapsed_seconds': None, 'zlib_compile_version': zlib.ZLIB_VERSION, 'zlib_runtime_version': zlib.ZLIB_RUNTIME_VERSION, 'reference_zlib_runtime': reference_zlib_runtime, 'parameters': {'level': 6, 'method': 'DEFLATED', 'wbits': -15, 'memLevel': 8, 'strategy': 'Z_DEFAULT_STRATEGY', 'compress_calls_per_member': 1, 'flush': 'Z_FINISH', 'chunk_bytes': _DEFLATE_CHUNK}, 'limits': bounds}
    before = path.stat()
    if stream_sha(path) != expected_sha256:
        raise ValueError('input SHA-256 changed before DEFLATE consistency observation')
    try:
        if zlib.ZLIB_RUNTIME_VERSION != reference_zlib_runtime:
            result['status'] = 'zlib_version_mismatch'
        elif before.st_size > bounds['max_file']:
            result['status'] = 'limit_exceeded'
        else:
            with zipfile.ZipFile(path) as zipped, path.open('rb') as raw:
                infos = zipped.infolist()
                result['entry_count'] = len(infos)
                result['deflate_count'] = sum((info.compress_type == zipfile.ZIP_DEFLATED for info in infos))
                result['stored_count'] = sum((info.compress_type == zipfile.ZIP_STORED for info in infos))
                if len(infos) != len({info.filename for info in infos}):
                    result['status'] = 'duplicate_names'
                elif len(infos) > bounds['max_entries'] or sum((info.file_size for info in infos)) > bounds['max_total'] or sum((info.compress_size for info in infos)) > bounds['max_compressed_total'] or any((info.file_size > bounds['max_part'] for info in infos)):
                    result['status'] = 'limit_exceeded'
                else:
                    offsets = sorted({info.header_offset for info in infos} | {zipped.start_dir})
                    next_offset = dict(zip(offsets, offsets[1:]))
                    matches = []
                    for info in infos:
                        try:
                            if info.flag_bits & 65:
                                raise _DeflateObservationError('encrypted_member')
                            if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                                raise _DeflateObservationError('unsupported_compression')
                            boundary = min(next_offset.get(info.header_offset, zipped.start_dir), zipped.start_dir, before.st_size)
                            offset = _deflate_payload_offset(raw, info, boundary)
                            if info.compress_type == zipfile.ZIP_STORED:
                                decoded = 0
                                with zipped.open(info, 'r') as payload:
                                    while True:
                                        chunk = payload.read(_DEFLATE_CHUNK)
                                        if not chunk:
                                            break
                                        decoded += len(chunk)
                                        if decoded > info.file_size or decoded > bounds['max_part']:
                                            raise _DeflateObservationError('decoded stored size exceeds limit')
                                if decoded != info.file_size:
                                    raise _DeflateObservationError('stored decoded size differs from central size')
                                result['stored_checked'] += 1
                            else:
                                matches.append(_match_deflate_level6(zipped, raw, info, offset, bounds['max_part']))
                                result['deflate_checked'] += 1
                        except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError, zlib.error, MemoryError) as exc:
                            result['errors'].append({'part': info.filename, 'error': type(exc).__name__ + ': ' + str(exc)})
                    if result['errors']:
                        result['status'] = 'partial' if result['deflate_checked'] or result['stored_checked'] else 'unavailable'
                    elif not result['deflate_count']:
                        result['status'] = 'no_deflate'
                    else:
                        assert result['deflate_checked'] == result['deflate_count'] == len(matches)
                        result['status'] = 'ok'
                        result['all_deflate_level6'] = all(matches)
    except (OSError, ValueError, RuntimeError, zipfile.BadZipFile, NotImplementedError, zlib.error, MemoryError) as exc:
        result['status'] = 'unavailable'
        result['all_deflate_level6'] = None
        result['errors'].append({'part': '', 'error': type(exc).__name__ + ': ' + str(exc)})
    finally:
        try:
            after = path.stat()
            unchanged = (before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns) and stream_sha(path) == expected_sha256
        except OSError as exc:
            raise ValueError('input unavailable after DEFLATE consistency observation') from exc
        if not unchanged:
            raise ValueError('input changed during DEFLATE consistency observation')
        result['elapsed_seconds'] = time.perf_counter() - started
    return result

def needed(rules, fmt):
    return dependencies({atom['key'] for family in rules['families'] if family['format'] == fmt for atom in family['when']})

def project(path, digest, fmt, rules):
    out = from_file(path, needed(rules, fmt), digest, fmt)
    return (out, {'mode': 'selected_field_projection', 'source': str(Path(path).resolve()), 'sha256': digest, 'main_part': out['main_part'], 'part_aliases': out['part_aliases'], 'alias_evidence': out['alias_evidence'], 'bounds': out['projection_bounds']})
