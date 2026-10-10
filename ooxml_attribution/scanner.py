import struct
MAX_FILE = 256 * 1024 * 1024
MAX_PART = 64 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_ENTRIES = 20000

def dos_date(d, t):
    return f'{1980 + (d >> 9):04d}-{d >> 5 & 15:02d}-{d & 31:02d} {t >> 11:02d}:{t >> 5 & 63:02d}:{(t & 31) * 2:02d}'

def extra_fields(raw):
    rows = []
    pos = 0
    while pos < len(raw):
        if pos + 4 > len(raw):
            raise ValueError('truncated extra header')
        tag, size = struct.unpack_from('<HH', raw, pos)
        payload = raw[pos + 4:pos + 4 + size]
        if len(payload) != size:
            raise ValueError('truncated extra payload')
        row = {'id': f'0x{tag:04X}', 'size': size, 'hex': payload.hex()}
        if tag == 41504 and size >= 4:
            sig, pad = struct.unpack_from('<HH', payload)
            row.update(signature=f'0x{sig:04X}', pad_value=pad, padding_bytes=size - 4, zero_padding=not any(payload[4:]))
        rows.append(row)
        pos += 4 + size
    return rows
