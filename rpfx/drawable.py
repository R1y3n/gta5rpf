"""Legacy (gen8) drawable parsing: YDR / YDD / YFT -> plain numpy meshes."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np

from .hashing import jenk
from .resource import Rsc, SYS_BASE
from .textures import Texture, read_texture_dictionary, read_texture_name_only

# VertexComponentType -> byte size
VT_SIZE = {0: 0, 1: 4, 2: 4, 3: 8, 4: 0, 5: 8, 6: 12, 7: 16, 8: 4, 9: 4, 10: 4}
VT_Half2, VT_Float, VT_Half4, VT_Float2, VT_Float3, VT_Float4, VT_UByte4, VT_Colour, VT_SNorm = 1, 2, 3, 5, 6, 7, 8, 9, 10
SEM = ["Position", "BlendWeights", "BlendIndices", "Normal", "Colour0", "Colour1",
       "TexCoord0", "TexCoord1", "TexCoord2", "TexCoord3", "TexCoord4", "TexCoord5",
       "TexCoord6", "TexCoord7", "Tangent", "Binormal"]
LOD_NAMES = ["high", "med", "low", "vlow"]

TEX_PARAM_PRIORITY = [jenk(n) for n in ("diffusesampler", "texturesampler", "diffusetexture",
                                        "basesampler", "diffusesampler2")]


@dataclass
class Shader:
    name_hash: int
    file_hash: int
    textures: Dict[int, str] = field(default_factory=dict)   # param-hash -> texture name
    vectors: Dict[int, tuple] = field(default_factory=dict)  # param-hash -> first vec4

    def diffuse_name(self) -> Optional[str]:
        for h in TEX_PARAM_PRIORITY:
            if self.textures.get(h):
                return self.textures[h]
        for n in self.textures.values():
            if n:
                return n
        return None


@dataclass
class Geometry:
    shader_index: int
    positions: np.ndarray                   # (N,3) float32
    indices: np.ndarray                     # (M,) uint32 (triangle list)
    normals: Optional[np.ndarray] = None
    colors: Optional[np.ndarray] = None     # (N,4) uint8
    uvs: Dict[int, np.ndarray] = field(default_factory=dict)  # channel -> (N,2)
    bone_ids: Optional[np.ndarray] = None
    blend_weights: Optional[np.ndarray] = None
    blend_indices: Optional[np.ndarray] = None
    vertex_flags: int = 0


@dataclass
class Model:
    lod: str
    geometries: List[Geometry]
    bone_index: int = 0
    has_skin: bool = False


@dataclass
class DrawableData:
    name: str
    models: List[Model]
    shaders: List[Shader]
    embedded_textures: List[Texture]
    bbox: tuple = ((0, 0, 0), (0, 0, 0))


# ---------------------------------------------------------------- vertices
def _component_layout(flags: int, types: int):
    out, off = [], 0
    for k in range(16):
        if (flags >> k) & 1:
            t = (types >> (k * 4)) & 0xF
            out.append((k, t, off))
            off += VT_SIZE.get(t, 0)
    return out, off


def _decode(arr: np.ndarray, t: int, count: int):
    """arr: (count, size) uint8 slice of the interleaved buffer."""
    sub = np.ascontiguousarray(arr)
    if t == VT_Float3: return sub.view("<f4").reshape(count, 3).astype(np.float32)
    if t == VT_Float2: return sub.view("<f4").reshape(count, 2).astype(np.float32)
    if t == VT_Float4: return sub.view("<f4").reshape(count, 4).astype(np.float32)
    if t == VT_Float: return sub.view("<f4").reshape(count, 1).astype(np.float32)
    if t == VT_Half2: return sub.view("<f2").reshape(count, 2).astype(np.float32)
    if t == VT_Half4: return sub.view("<f2").reshape(count, 4).astype(np.float32)
    if t == VT_SNorm: return np.clip(sub.view("<i1").reshape(count, 4).astype(np.float32) / 127.0, -1, 1)
    if t in (VT_UByte4, VT_Colour): return sub.reshape(count, 4).copy()
    return None


def read_geometry(rsc: Rsc, addr: int) -> Optional[Geometry]:
    vb_ptr = rsc.u64(addr + 0x18)
    ib_ptr = rsc.u64(addr + 0x38)
    if not vb_ptr or not ib_ptr:
        return None
    bone_ptr = rsc.u64(addr + 0x68)
    bone_cnt = rsc.u16(addr + 0x72)
    # VertexBuffer
    stride, _flags = rsc.unpack("<HH", vb_ptr + 8)
    d1 = rsc.u64(vb_ptr + 0x10)
    vcount = rsc.u32(vb_ptr + 0x18)
    d2 = rsc.u64(vb_ptr + 0x20)
    info = rsc.u64(vb_ptr + 0x30)
    data_ptr = d1 or d2
    if not (data_ptr and info and vcount and stride):
        return None
    vflags, dstride, _u6, dcount, types = rsc.unpack("<IHBBQ", info)
    comps, _computed = _component_layout(vflags, types)
    raw = np.frombuffer(rsc.bytes(data_ptr, vcount * stride), dtype=np.uint8).reshape(vcount, stride)
    g = Geometry(shader_index=0, positions=None, indices=None, vertex_flags=vflags)
    for sem, t, off in comps:
        sz = VT_SIZE.get(t, 0)
        if not sz or off + sz > stride:
            continue
        val = _decode(raw[:, off:off + sz], t, vcount)
        if val is None:
            continue
        if sem == 0: g.positions = val[:, :3]
        elif sem == 1: g.blend_weights = val
        elif sem == 2: g.blend_indices = val
        elif sem == 3: g.normals = val[:, :3]
        elif sem == 4: g.colors = val
        elif 6 <= sem <= 13 and val.shape[1] == 2: g.uvs[sem - 6] = val
    if g.positions is None:
        return None
    # IndexBuffer
    icount = rsc.u32(ib_ptr + 8)
    iptr = rsc.u64(ib_ptr + 0x10)
    idx = np.frombuffer(rsc.bytes(iptr, icount * 2), dtype="<u2").astype(np.uint32) if iptr and icount else np.zeros(0, np.uint32)
    idx = idx[: (len(idx) // 3) * 3]
    g.indices = idx
    if bone_ptr and bone_cnt:
        g.bone_ids = np.frombuffer(rsc.bytes(bone_ptr, bone_cnt * 2), dtype="<u2").copy()
    return g


def read_model(rsc: Rsc, addr: int, lod: str) -> Optional[Model]:
    geoms_ptr = rsc.u64(addr + 8)
    count = rsc.u16(addr + 0x10)
    smap_ptr = rsc.u64(addr + 0x20)
    skel_bind = rsc.u32(addr + 0x28)
    gptrs = rsc.ptr_array(geoms_ptr, count)
    smap = list(rsc.unpack("<%dH" % count, smap_ptr)) if smap_ptr and count else [0] * count
    geoms = []
    for i, p in enumerate(gptrs):
        if not p:
            continue
        g = read_geometry(rsc, p)
        if g is not None:
            g.shader_index = smap[i] if i < len(smap) else 0
            geoms.append(g)
    return Model(lod, geoms, bone_index=(skel_bind >> 24) & 0xFF, has_skin=((skel_bind >> 8) & 0xFF) == 1)


def read_shader_group(rsc: Rsc, addr: int):
    txd_ptr = rsc.u64(addr + 0x10)
    sh_ptr = rsc.u64(addr + 0x18)
    sh_cnt = rsc.u16(addr + 0x20)
    textures: List[Texture] = []
    if txd_ptr:
        try:
            textures = read_texture_dictionary(rsc, txd_ptr)
        except Exception:
            textures = []
    shaders = []
    for sp in rsc.ptr_array(sh_ptr, sh_cnt):
        if not sp:
            shaders.append(Shader(0, 0))
            continue
        params_ptr = rsc.u64(sp)
        name = rsc.u32(sp + 8)
        pcount = rsc.u8(sp + 0x10)
        fname = rsc.u32(sp + 0x18)
        sh = Shader(name, fname)
        if params_ptr and pcount:
            plist, pos_extra = [], 0
            for i in range(pcount):
                dtype, _u1, _u2, _u4, dptr = rsc.unpack("<BBHIQ", params_ptr + 16 * i)
                plist.append((dtype, dptr))
                pos_extra += 16 * dtype  # dtype 0 -> 0, 1 -> 16, n -> 16n
            hpos = params_ptr + 16 * pcount + pos_extra
            hashes = rsc.unpack("<%dI" % pcount, hpos)
            for (dtype, dptr), h in zip(plist, hashes):
                if dtype == 0:
                    if dptr:
                        try:
                            sh.textures[h] = read_texture_name_only(rsc, dptr)
                        except Exception:
                            pass
                elif dptr:
                    try:
                        sh.vectors[h] = rsc.unpack("<4f", dptr)
                    except Exception:
                        pass
        shaders.append(sh)
    return shaders, textures


def read_drawable_base(rsc: Rsc, addr: int, name: str = "") -> DrawableData:
    sg_ptr = rsc.u64(addr + 0x10)
    bmin = rsc.unpack("<3f", addr + 0x30)
    bmax = rsc.unpack("<3f", addr + 0x40)
    lod_ptrs = [rsc.u64(addr + 0x50 + 8 * i) for i in range(4)]
    shaders, textures = ([], [])
    if sg_ptr:
        shaders, textures = read_shader_group(rsc, sg_ptr)
    models: List[Model] = []
    for lod, lp in zip(LOD_NAMES, lod_ptrs):
        if not lp:
            continue
        arr_ptr, cnt, cap = rsc.list_header(lp)
        for mp in rsc.ptr_array(arr_ptr, cap):
            if mp:
                m = read_model(rsc, mp, lod)
                if m:
                    models.append(m)
    return DrawableData(name, models, shaders, textures, (bmin, bmax))


def read_drawable(rsc: Rsc, addr: int = SYS_BASE) -> DrawableData:
    """YDR root (rage::rmcDrawable / gtaDrawable)."""
    name = rsc.cstr(rsc.u64(addr + 0xA8)) if rsc.u64(addr + 0xA8) else ""
    return read_drawable_base(rsc, addr, name)


def read_drawable_dictionary(rsc: Rsc, addr: int = SYS_BASE) -> List[DrawableData]:
    hp, hc, _ = rsc.list_header(addr + 0x20)
    hashes = list(rsc.unpack("<%dI" % hc, hp)) if hp and hc else []
    dp, dc, _cap = rsc.list_header(addr + 0x30)
    out = []
    for i, p in enumerate(rsc.ptr_array(dp, dc)):
        if not p:
            continue
        d = read_drawable(rsc, p)
        if not d.name:
            d.name = "%08x" % hashes[i] if i < len(hashes) else "drawable_%d" % i
        out.append(d)
    return out


def read_fragment(rsc: Rsc, addr: int = SYS_BASE) -> List[DrawableData]:
    """YFT: main drawable + the optional drawable array. (Physics-child drawables
    positioned by FragPhysicsLODGroup are NOT included yet.)"""
    name = rsc.cstr(rsc.u64(addr + 0x58)) if rsc.u64(addr + 0x58) else "fragment"
    out = []
    dp = rsc.u64(addr + 0x30)
    if dp:
        out.append(read_drawable_base(rsc, dp, name))
    arr_ptr = rsc.u64(addr + 0x38)
    names_ptr = rsc.u64(addr + 0x40)
    cnt = rsc.u32(addr + 0x48)
    if arr_ptr and 0 < cnt < 256:
        nptrs = rsc.ptr_array(names_ptr, cnt) if names_ptr else []
        for i, p in enumerate(rsc.ptr_array(arr_ptr, cnt)):
            if p:
                nm = rsc.cstr(nptrs[i]) if i < len(nptrs) and nptrs[i] else "%s_%d" % (name, i)
                out.append(read_drawable_base(rsc, p, nm))
    return out
