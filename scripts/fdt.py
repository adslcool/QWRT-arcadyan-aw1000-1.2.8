"""Small FDT v17 codec for preserving the existing FIT and patching only its DTB."""
import hashlib
import struct
import zlib


class Node:
    def __init__(self, name):
        self.name, self.props, self.children = name, {}, []
        self.reservations = b'\0'*16

    def get(self, path):
        n = self
        for part in path.strip('/').split('/'):
            if part:
                n = next(c for c in n.children if c.name == part)
        return n

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


def parse(data):
    if len(data) < 40:
        raise ValueError('FDT header truncated')
    h = struct.unpack_from('>10I', data)
    if h[0] != 0xd00dfeed or h[1] > len(data) or h[5] != 17:
        raise ValueError('Unsupported FDT')
    strings = data[h[3]:h[3]+h[8]]
    pos, end, stack, root = h[2], h[2]+h[9], [], None
    if end > h[1] or h[3]+h[8] > h[1]:
        raise ValueError('FDT bounds')
    while pos < end:
        token, = struct.unpack_from('>I', data, pos); pos += 4
        if token == 1:
            stop = data.index(0, pos, end)
            n = Node(data[pos:stop].decode()); pos = (stop+4)&~3
            if stack: stack[-1].children.append(n)
            else: root = n
            stack.append(n)
        elif token == 2:
            stack.pop()
        elif token == 3:
            size, off = struct.unpack_from('>2I', data, pos); pos += 8
            if pos+size > end or off >= len(strings): raise ValueError('FDT property bounds')
            name = strings[off:strings.index(0, off)].decode()
            stack[-1].props[name] = data[pos:pos+size]; pos = (pos+size+3)&~3
        elif token == 4:
            pass
        elif token == 9:
            if stack or root is None: raise ValueError('FDT nesting')
            off = h[4]
            while True:
                if off+16 > h[1]: raise ValueError('FDT reservation bounds')
                pair = data[off:off+16]; off += 16
                if pair == b'\0'*16: break
            root.reservations = data[h[4]:off]
            return root, h[7]
        else:
            raise ValueError('Unknown FDT token')
    raise ValueError('Missing FDT END')


def pack(root, boot_cpu=0):
    strings = bytearray(); offsets = {}
    for node in root.walk():
        for key in node.props:
            if key not in offsets:
                offsets[key] = len(strings); strings += key.encode()+b'\0'
    body = bytearray()
    def pad():
        body.extend(b'\0' * (-len(body)%4))
    def emit(n):
        body.extend(struct.pack('>I', 1)+n.name.encode()+b'\0'); pad()
        for key, val in n.props.items():
            body.extend(struct.pack('>3I', 3, len(val), offsets[key])+val); pad()
        for c in n.children: emit(c)
        body.extend(struct.pack('>I', 2))
    emit(root); body.extend(struct.pack('>I', 9))
    reservations = root.reservations
    off_body = 40+len(reservations); off_strings = off_body+len(body)
    h = struct.pack('>10I', 0xd00dfeed, off_strings+len(strings), off_body,
                    off_strings, 40, 17, 16, boot_cpu, len(strings), len(body))
    return h+reservations+body+strings


def _prepare_fit(data, mutate):
    fit, cpu = parse(data)
    if any(n.name.startswith('signature') for n in fit.walk()):
        raise ValueError('Refusing to modify a signed FIT')
    images = fit.get('/images')
    fdts = [n for n in images.children if n.props.get('type') == b'flat_dt\0']
    if len(fdts) != 1: raise ValueError('Expected one DTB')
    fdt = fdts[0]
    dt, dt_cpu = parse(fdt.props['data'])
    if b'arcadyan,aw1000\0' not in dt.props.get('compatible', b''):
        raise ValueError('Unexpected board')
    mutate(dt)
    blob = pack(dt, dt_cpu)
    fdt.props['data'] = blob
    for node in fdt.children:
        algo = node.props.get('algo', b'').rstrip(b'\0').decode()
        if algo == 'crc32': value = struct.pack('>I', zlib.crc32(blob))
        elif algo in ('sha1', 'sha256'): value = hashlib.new(algo, blob).digest()
        else: raise ValueError('Unsupported FIT hash '+algo)
        node.props['value'] = value
    return pack(fit, cpu)


def prepare_phone_fit(data):
    def mutate(dt):
        dt.get('/soc/spi@78b8000').props['status'] = b'disabled\0'
    return _prepare_fit(data, mutate)


def prepare_led_fit(data):
    """Expose the otherwise truncated AW1000 power channel as ipq::led0."""
    expected = [5, 6, 7, 14, 13, 1, 9, 10, 2, 11, 12, 3, 4, 8, 15]
    def mutate(dt):
        ledc = dt.get('/soc/ledc@191E000')
        if ledc.props.get('compatible') != b'qti,ledc\0':
            raise ValueError('Unexpected AW1000 LED controller')
        raw = ledc.props.get('qti,ledc_blink_indices', b'')
        if len(raw) != 4 * len(expected):
            raise ValueError('Unexpected AW1000 LED index count')
        values = list(struct.unpack('>%dI' % len(expected), raw))
        if values != expected:
            raise ValueError('Unexpected AW1000 LED index map')
        values[0] = 15
        ledc.props['qti,ledc_blink_indices'] = struct.pack('>%dI' % len(values), *values)
    return _prepare_fit(data, mutate)
