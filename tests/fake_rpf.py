"""Builds synthetic RPF7 archives + legacy-layout resources for testing.
(Mirrors the documented layouts, so it validates plumbing, NOT real-game quirks.)"""
import struct
import zlib

import numpy as np
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from rpfx.hashing import jenk

SYS, GFX = 0x50000000, 0x60000000


def aes_enc(data, key):
    e = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    n = len(data) - len(data) % 16
    return e.update(data[:n]) + e.finalize() + data[n:]


class Arena:
    def __init__(self):
        self.sys, self.gfx = bytearray(), bytearray()

    def alloc(self, seg, size, align=16):
        buf = self.sys if seg == "s" else self.gfx
        while len(buf) % align:
            buf.append(0)
        off = len(buf)
        buf.extend(b"\0" * size)
        return (SYS if seg == "s" else GFX) + off

    def write(self, addr, data):
        buf = self.sys if (addr >> 28) == 5 else self.gfx
        off = addr & 0x0FFFFFFF
        buf[off:off + len(data)] = data

    def put(self, seg, data, align=16):
        a = self.alloc(seg, len(data), align)
        self.write(a, data)
        return a

    def cstr(self, s):
        return self.put("s", s.encode() + b"\0", 1)

    def u(self, addr, fmt, *v):
        self.write(addr, struct.pack(fmt, *v))

    def finish(self):
        def pad(b):
            n = (len(b) + 0x1FFF) // 0x2000
            return bytes(b) + b"\0" * (n * 0x2000 - len(b)), n << 17
        s, sf = pad(self.sys)
        g, gf = pad(self.gfx) if len(self.gfx) else (b"", 0)
        return s + g, sf, gf


def make_texture(ar: Arena, name, w, h, fmt, levels, data, usage=20, flags=0):
    t = ar.alloc("s", 0x90)
    ar.u(t + 0x28, "<Q", ar.cstr(name))
    ar.u(t + 0x32, "<H", 7)
    ar.u(t + 0x40, "<I", usage | (flags << 5))
    ar.u(t + 0x48, "<I", 1)
    stride = {21: w * 4, 0x31545844: w // 2}[fmt]
    ar.u(t + 0x50, "<HHHHI", w, h, 1, stride, fmt)
    ar.u(t + 0x5D, "<B", levels)
    ar.u(t + 0x70, "<Q", ar.put("g", data))
    return t


def make_txd(ar: Arena, root, tex_ptrs, names):
    ha = ar.put("s", struct.pack("<%dI" % len(names), *[jenk(n) for n in names]))
    pa = ar.put("s", struct.pack("<%dQ" % len(tex_ptrs), *tex_ptrs))
    ar.u(root + 0x20, "<QHH", ha, len(names), len(names))
    ar.u(root + 0x30, "<QHH", pa, len(tex_ptrs), len(tex_ptrs))


def build_ytd():
    ar = Arena()
    root = ar.alloc("s", 0x40)
    px = np.array([[255, 0, 0, 255], [0, 255, 0, 255], [0, 0, 255, 255], [255, 255, 255, 255]], np.uint8)
    # BGRA order for D3DFMT_A8R8G8B8 on little endian: bytes B,G,R,A
    bgra = px[:, [2, 1, 0, 3]].tobytes()
    mip1 = bytes([128, 128, 128, 255])
    t1 = make_texture(ar, "testtex_a", 2, 2, 21, 2, bgra + mip1)
    # DXT1 8x8: 4 blocks (8 bytes each) + mip(4x4)=1 block + mip(2x2)=1 block, stride = w/2 = 4 ... 4*8=32 total
    blk = struct.pack("<HHI", 0xF800, 0x001F, 0x00000000)    # red / blue, all index 0 -> red
    t2 = make_texture(ar, "testtex_b", 8, 8, 0x31545844, 3, blk * 4 + blk * 1 + blk * 1)
    make_txd(ar, root, [t1, t2], ["testtex_a", "testtex_b"])
    ar.u(root + 0x00, "<I", 0)
    return ar.finish()


def _model(ar, geom_ptrs, smap):
    m = ar.alloc("s", 0x30)
    gp = ar.put("s", struct.pack("<%dQ" % len(geom_ptrs), *geom_ptrs))
    sm = ar.put("s", struct.pack("<%dH" % len(smap), *smap))
    ar.u(m + 8, "<Q", gp)
    ar.u(m + 0x10, "<HH", len(geom_ptrs), len(geom_ptrs))
    ar.u(m + 0x20, "<Q", sm)
    ar.u(m + 0x28, "<I", 0)
    return m


def _geometry(ar, offset=(0, 0, 0)):
    ox, oy, oz = offset
    quad = [(-1, -1, 0), (1, -1, 0), (1, 1, 0), (-1, 1, 0)]
    uvs = [(0, 1), (1, 1), (1, 0), (0, 0)]
    vb = bytearray()
    for (x, y, z), (u, v) in zip(quad, uvs):
        vb += struct.pack("<3f", x + ox, y + oy, z + oz)       # position
        vb += struct.pack("<3f", 0, 0, 1)                      # normal
        vb += bytes([255, 128, 64, 255])                       # colour0
        vb += struct.pack("<2f", u, v)                         # texcoord0
    vdata = ar.put("g", bytes(vb))
    flags = (1 << 0) | (1 << 3) | (1 << 4) | (1 << 6)
    types = (6 << 0) | (6 << 12) | (9 << 16) | (5 << 24)
    info = ar.alloc("s", 16)
    ar.u(info, "<IHBBQ", flags, 36, 0, 4, types)
    vbuf = ar.alloc("s", 0x80)
    ar.u(vbuf + 8, "<HH", 36, 0)
    ar.u(vbuf + 0x10, "<Q", vdata)
    ar.u(vbuf + 0x18, "<I", 4)
    ar.u(vbuf + 0x30, "<Q", info)
    idx = struct.pack("<6H", 0, 1, 2, 0, 2, 3)
    ibuf = ar.alloc("s", 0x60)
    ar.u(ibuf + 8, "<I", 6)
    ar.u(ibuf + 0x10, "<Q", ar.put("g", idx))
    g = ar.alloc("s", 0x98)
    ar.u(g + 0x18, "<Q", vbuf)
    ar.u(g + 0x38, "<Q", ibuf)
    ar.u(g + 0x58, "<II", 6, 2)
    ar.u(g + 0x60, "<H", 4)
    ar.u(g + 0x70, "<H", 36)
    return g


def _shader_group(ar, with_txd=True):
    txd = 0
    if with_txd:
        txd = ar.alloc("s", 0x40)
        px = bytes([0, 0, 255, 255,  0, 255, 0, 255,  255, 0, 0, 255,  255, 255, 255, 255])  # BGRA
        t = make_texture(ar, "quadtex", 2, 2, 21, 1, px)
        make_txd(ar, txd, [t], ["quadtex"])
    # one shader, one texture param (DiffuseSampler) + one vector param
    texref = ar.alloc("s", 80)
    ar.u(texref + 0x28, "<Q", ar.cstr("quadtex"))
    vec = ar.put("s", struct.pack("<4f", 1, 1, 1, 1))
    pb = ar.alloc("s", 16 * 2 + 16 + 4 * 2)
    ar.u(pb, "<BBHIQ", 0, 0, 0, 0, texref)
    ar.u(pb + 16, "<BBHIQ", 1, 0, 0, 0, vec)
    ar.u(pb + 32, "<4f", 1, 1, 1, 1)                        # embedded vector data
    ar.u(pb + 48, "<2I", jenk("diffusesampler"), jenk("matdiffusecolor"))
    sfx = ar.alloc("s", 48)
    ar.u(sfx, "<Q", pb)
    ar.u(sfx + 8, "<I", jenk("default"))
    ar.u(sfx + 0x10, "<B", 2)
    ar.u(sfx + 0x18, "<I", jenk("default.sps"))
    sarr = ar.put("s", struct.pack("<Q", sfx))
    sg = ar.alloc("s", 64)
    ar.u(sg + 0x10, "<Q", txd)
    ar.u(sg + 0x18, "<Q", sarr)
    ar.u(sg + 0x20, "<HH", 1, 1)
    return sg


def _drawable_base(ar, root, offset=(0, 0, 0), with_txd=True, name=None):
    sg = _shader_group(ar, with_txd)
    g = _geometry(ar, offset)
    m = _model(ar, [g], [0])
    marr = ar.put("s", struct.pack("<Q", m))
    hdr = ar.alloc("s", 16)
    ar.u(hdr, "<QHH", marr, 1, 1)
    ar.u(root + 0x10, "<Q", sg)
    ar.u(root + 0x30, "<3f", -1, -1, 0)
    ar.u(root + 0x40, "<3f", 1, 1, 0)
    ar.u(root + 0x50, "<Q", hdr)
    if name:
        ar.u(root + 0xA8, "<Q", ar.cstr(name))


def build_ydr():
    ar = Arena()
    root = ar.alloc("s", 0xD0)
    _drawable_base(ar, root, name="testprop")
    return ar.finish()


def build_ydd():
    ar = Arena()
    root = ar.alloc("s", 0x40)
    ds = []
    for i in range(2):
        d = ar.alloc("s", 0xD0)
        _drawable_base(ar, d, offset=(i * 3, 0, 0), with_txd=(i == 0))
        ds.append(d)
    ha = ar.put("s", struct.pack("<2I", jenk("part_a"), jenk("part_b")))
    da = ar.put("s", struct.pack("<2Q", *ds))
    ar.u(root + 0x20, "<QHH", ha, 2, 2)
    ar.u(root + 0x30, "<QHH", da, 2, 2)
    return ar.finish()


def build_yft():
    ar = Arena()
    root = ar.alloc("s", 0x130)
    d = ar.alloc("s", 0x150)
    _drawable_base(ar, d)
    ar.u(root + 0x30, "<Q", d)
    ar.u(root + 0x58, "<Q", ar.cstr("testcar"))
    return ar.finish()


# ----------------------------------------------------------------- RPF7 writer
def _res_file(built):
    data, sf, gf = built
    comp = zlib.compressobj(9, zlib.DEFLATED, -15)
    c = comp.compress(data) + comp.flush()
    return struct.pack("<IIII", 0x37435352, 0, sf, gf) + c, sf, gf


def build_rpf(tree, enc="OPEN", aes_key=None):
    """tree: {name: bytes | ('res', built) | ('rpf', bytes) | dict}"""
    flat = []   # BFS list of dicts: {name, kind, ...}
    root = {"name": "", "kind": "dir", "children": tree}
    queue = [root]
    flat.append(root)
    order = [root]
    i = 0
    while i < len(order):
        d = order[i]
        i += 1
        d["ent_index"] = len(flat)
        for nm, v in d["children"].items():
            e = {"name": nm}
            if isinstance(v, dict):
                e.update(kind="dir", children=v)
                order.append(e)
            elif isinstance(v, tuple) and v[0] == "res":
                blob, sf, gf = _res_file(v[1])
                e.update(kind="res", blob=blob, sf=sf, gf=gf)
            elif isinstance(v, tuple) and v[0] == "rpf":
                e.update(kind="rpf", blob=v[1])
            elif isinstance(v, tuple) and v[0] == "bin_z":
                comp = zlib.compressobj(9, zlib.DEFLATED, -15)
                e.update(kind="binz", blob=comp.compress(v[1]) + comp.flush(), usize=len(v[1]))
            else:
                e.update(kind="bin", blob=v)
            flat.append(e)
        d["ent_count"] = len(d["children"])

    names, offs = bytearray(), {}
    for e in flat:
        offs[id(e)] = len(names)
        names += e["name"].encode() + b"\0"
    while len(names) % 16:
        names.append(0)
    n = len(flat)
    toc_len = 16 + n * 16 + len(names)
    data_start = (toc_len + 511) // 512 * 512
    body, cur = bytearray(), data_start
    ent = bytearray()
    for e in flat:
        no = offs[id(e)]
        if e["kind"] == "dir":
            ent += struct.pack("<IIII", no, 0x7FFFFF00, e.get("ent_index", 0), e.get("ent_count", 0))
            continue
        blob = e["blob"]
        sector = cur // 512
        pad = (-len(blob)) % 512
        body += blob + b"\0" * pad
        cur += len(blob) + pad
        if e["kind"] in ("bin", "binz", "rpf"):
            size = len(blob) if e["kind"] == "binz" else 0
            usize = e.get("usize", len(blob))
            q = no | (size << 16) | (sector << 40)
            ent += struct.pack("<QII", q, usize, 0)
        else:
            sz = len(blob)
            ent += struct.pack("<H", no) + bytes([sz & 255, (sz >> 8) & 255, (sz >> 16) & 255])
            ent += bytes([sector & 255, (sector >> 8) & 255, ((sector >> 16) & 255) | 0x80])
            ent += struct.pack("<II", e["sf"], e["gf"])
    if enc == "AES":
        ent, names = aes_enc(bytes(ent), aes_key), aes_enc(bytes(names), aes_key)
        etype = 0x0FFFFFF9
    else:
        etype = 0x4E45504F
    head = struct.pack("<4I", 0x52504637, n, len(names), etype)
    toc = head + bytes(ent) + bytes(names)
    return toc + b"\0" * (data_start - len(toc)) + bytes(body)
