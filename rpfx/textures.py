"""YTD / embedded texture dictionaries: parsing, DDS container writing, XML."""
import struct
from dataclasses import dataclass
from typing import List, Optional

from .hashing import jenk
from .resource import Rsc, SYS_BASE

FMT_NAMES = {
    21: "D3DFMT_A8R8G8B8", 22: "D3DFMT_X8R8G8B8", 25: "D3DFMT_A1R5G5B5",
    28: "D3DFMT_A8", 32: "D3DFMT_A8B8G8R8", 50: "D3DFMT_L8",
    0x31545844: "D3DFMT_DXT1", 0x33545844: "D3DFMT_DXT3", 0x35545844: "D3DFMT_DXT5",
    0x31495441: "D3DFMT_ATI1", 0x32495441: "D3DFMT_ATI2", 0x20374342: "D3DFMT_BC7",
}
USAGE_NAMES = ["UNKNOWN", "DEFAULT", "TERRAIN", "CLOUDDENSITY", "CLOUDNORMAL", "CABLE", "FENCE",
               "ENVEFF", "SCRIPT", "WATERFLOW", "WATERFOAM", "WATERFOG", "WATEROCEAN", "WATER",
               "FOAMOPACITY", "FOAM", "DIFFUSEMIPSHARPEN", "DIFFUSEDETAIL", "DIFFUSEDARK",
               "DIFFUSEALPHAOPAQUE", "DIFFUSE", "DETAIL", "NORMAL", "SPECULAR", "EMISSIVE",
               "TINTPALETTE", "SKIPPROCESSING", "DONOTOPTIMIZE", "TEST", "COUNT"]
USAGE_FLAG_NAMES = ["NOT_HALF", "HD_SPLIT", "X2", "X4", "Y4", "X8", "X16", "X32", "X64", "Y64",
                    "X128", "X256", "X512", "Y512", "X1024", "Y1024", "X2048", "Y2048",
                    "EMBEDDEDSCRIPTRT", "UNK19", "UNK20", "UNK21", "FLAG_FULL", "MAPS_HALF", "UNK24"]


def usage_name(v: int) -> str:
    return USAGE_NAMES[v] if v < len(USAGE_NAMES) else str(v)


def usage_flags_str(flags: int) -> str:
    """Mimic .NET [Flags] enum ToString()."""
    if flags == 0:
        return "0"
    names, rest = [], flags
    for i, n in enumerate(USAGE_FLAG_NAMES):
        if flags & (1 << i):
            names.append(n)
            rest &= ~(1 << i)
    return str(flags) if rest else ", ".join(names)


def fmt_name(f: int) -> str:
    return FMT_NAMES.get(f, str(f))


@dataclass
class Texture:
    name: str
    width: int
    height: int
    depth: int
    stride: int
    fmt: int
    levels: int
    unk32: int
    usage_data: int
    extra_flags: int
    data: bytes

    @property
    def usage(self): return self.usage_data & 0x1F
    @property
    def usage_flags(self): return self.usage_data >> 5


def read_texture(rsc: Rsc, addr: int) -> Texture:
    name_ptr = rsc.u64(addr + 0x28)
    unk32 = rsc.u16(addr + 0x32)
    usage_data = rsc.u32(addr + 0x40)
    extra = rsc.u32(addr + 0x48)
    w, h, d, stride, fmt = rsc.unpack("<HHHHI", addr + 0x50)
    levels = rsc.u8(addr + 0x5D)
    data_ptr = rsc.u64(addr + 0x70)
    total, ln = 0, stride * h
    for _ in range(levels):
        total += ln
        ln //= 4
    data = rsc.bytes(data_ptr, total) if data_ptr and total else b""
    return Texture(rsc.cstr(name_ptr), w, h, d, stride, fmt, levels, unk32, usage_data, extra, data)


def read_texture_dictionary(rsc: Rsc, addr: int = SYS_BASE) -> List[Texture]:
    hp, hc, _ = rsc.list_header(addr + 0x20)      # name hashes (unused here)
    tp, tc, tcap = rsc.list_header(addr + 0x30)
    out = []
    for p in rsc.ptr_array(tp, tc):
        if p:
            out.append(read_texture(rsc, p))
    return out


def read_texture_name_only(rsc: Rsc, addr: int) -> str:
    """Shader texture parameters usually point at a bare TextureBase (name only)."""
    return rsc.cstr(rsc.u64(addr + 0x28))


# ------------------------------------------------------------------ DDS
def _dds_header(w, h, levels, pf, size_or_pitch, linear):
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | (0x20000 if levels > 1 else 0) | (0x8 if linear else 0x80000)
    caps = 0x1000 | ((0x8 | 0x400000) if levels > 1 else 0)
    hdr = struct.pack("<4sIIIIIII44s", b"DDS ", 124, flags, h, w, size_or_pitch, 0, levels, b"\0" * 44)
    return hdr + pf + struct.pack("<IIIII", caps, 0, 0, 0, 0)


def _pf(flags, fourcc=b"\0\0\0\0", bits=0, r=0, g=0, b=0, a=0):
    return struct.pack("<II4sIIIII", 32, flags, fourcc, bits, r, g, b, a)


def mip_size(fmt: int, w: int, h: int) -> Optional[int]:
    w, h = max(1, w), max(1, h)
    bw, bh = (w + 3) // 4, (h + 3) // 4
    if fmt in (0x31545844, 0x31495441):
        return bw * bh * 8
    if fmt in (0x33545844, 0x35545844, 0x32495441, 0x20374342):
        return bw * bh * 16
    bpp = {21: 4, 22: 4, 32: 4, 25: 2, 28: 1, 50: 1}.get(fmt)
    return None if bpp is None else w * h * bpp


def make_dds(t: Texture) -> bytes:
    f, w, h = t.fmt, t.width, t.height
    comp = {0x31545844: b"DXT1", 0x33545844: b"DXT3", 0x35545844: b"DXT5",
            0x31495441: b"ATI1", 0x32495441: b"ATI2"}
    extra = b""
    linear = False
    if f in comp:
        pf = _pf(0x4, comp[f])
    elif f == 0x20374342:                      # BC7 -> DX10 header
        pf = _pf(0x4, b"DX10")
        extra = struct.pack("<IIIII", 98, 3, 0, 1, 0)
    elif f == 21: pf, linear = _pf(0x41, bits=32, r=0xFF0000, g=0xFF00, b=0xFF, a=0xFF000000), True
    elif f == 22: pf, linear = _pf(0x40, bits=32, r=0xFF0000, g=0xFF00, b=0xFF), True
    elif f == 32: pf, linear = _pf(0x41, bits=32, r=0xFF, g=0xFF00, b=0xFF0000, a=0xFF000000), True
    elif f == 25: pf, linear = _pf(0x41, bits=16, r=0x7C00, g=0x3E0, b=0x1F, a=0x8000), True
    elif f == 28: pf, linear = _pf(0x2, bits=8, a=0xFF), True
    elif f == 50: pf, linear = _pf(0x20000, bits=8, r=0xFF), True
    else:
        raise ValueError("unsupported texture format %s" % fmt_name(f))
    top = mip_size(f, w, h)
    pitch = (w * {21: 4, 22: 4, 32: 4, 25: 2, 28: 1, 50: 1}[f]) if linear else top
    body = bytearray()
    cw, ch, off = w, h, 0
    for _ in range(max(1, t.levels)):
        sz = mip_size(f, cw, ch)
        chunk = t.data[off:off + sz]
        body += chunk
        off += sz
        cw, ch = max(1, cw // 2), max(1, ch // 2)
    return _dds_header(w, h, max(1, t.levels), pf, pitch, linear) + extra + bytes(body)


def dds_to_png(dds: bytes) -> Optional[bytes]:
    """Top mip -> PNG via Pillow (returns None if Pillow can't decode it)."""
    try:
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(dds))
        im.load()
        buf = io.BytesIO()
        im.convert("RGBA").save(buf, "PNG")
        return buf.getvalue()
    except Exception:
        return None


# ------------------------------------------------------------------ XML
def _esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;"))


def texture_dictionary_xml(textures: List[Texture]) -> str:
    """Write the established YTD XML layout with one-space indentation."""
    L = ['<?xml version="1.0" encoding="UTF-8"?>']
    if not textures:
        L.append("<TextureDictionary />")
    else:
        L.append("<TextureDictionary>")
        for t in textures:
            L.append(" <Item>")
            L.append("  <Name>%s</Name>" % _esc(t.name) if t.name else "  <Name />")
            L.append('  <Unk32 value="%d" />' % t.unk32)
            L.append("  <Usage>%s</Usage>" % usage_name(t.usage))
            L.append("  <UsageFlags>%s</UsageFlags>" % usage_flags_str(t.usage_flags))
            L.append('  <ExtraFlags value="%d" />' % t.extra_flags)
            L.append('  <Width value="%d" />' % t.width)
            L.append('  <Height value="%d" />' % t.height)
            L.append('  <MipLevels value="%d" />' % t.levels)
            L.append("  <Format>%s</Format>" % fmt_name(t.fmt))
            L.append("  <FileName>%s.dds</FileName>" % _esc(t.name or "null"))
            L.append(" </Item>")
        L.append("</TextureDictionary>")
    return "\n".join(L) + "\n"
