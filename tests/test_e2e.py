import base64, io, json, os, struct, sys, tempfile, zlib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))
import numpy as np
from fake_rpf import *
from rpfx.keys import Keys, unscramble_magic
from rpfx.rpf import open_rpf
from rpfx.vfs import Vfs
from rpfx.cli import Shell
from rpfx.hashing import joaat, DotNetRandom
from rpfx.crypto import aes_decrypt

KEY = bytes(range(32))
keys = Keys(aes_key=KEY)           # no NG material in this synthetic test

inner = build_rpf({"readme.txt": b"hello nested", "x.ytd": ("res", build_ytd())}, enc="AES", aes_key=KEY)
top = build_rpf({
    "models": {"prop.ydr": ("res", build_ydr()), "prop.ytd": ("res", build_ytd()),
               "peds.ydd": ("res", build_ydd()), "car.yft": ("res", build_yft())},
    "docs": {"notes.txt": ("bin_z", b"compressed text " * 50), "plain.bin": b"\x01\x02\x03"},
    "inner.rpf": ("rpf", inner),
}, enc="OPEN")
d = tempfile.mkdtemp()
path = os.path.join(d, "test.rpf")
open(path, "wb").write(top)

arc = open_rpf(path, keys)
sh = Shell(Vfs(arc))
out = os.path.join(d, "out")
for c in ["ls", "ls -l models", "cd models", "pwd", "info prop.ydr", "cd /inner.rpf", "pwd", "ls -l", "cd /", "tree -d 2",
          "find *.ytd",
          "export docs/notes.txt --as raw -o %s/raw" % out,
          "export models/prop.ytd --as xml,dds,png -o %s/ytd" % out,
          "export models/prop.ydr --as glb,obj,xml -o %s/ydr" % out,
          "export models/peds.ydd --as glb -o %s/ydd" % out,
          "export models/car.yft --as glb -o %s/yft" % out,
          "export /inner.rpf/x.ytd --as xml -o %s/nested" % out,
          "export -r models --as auto -o %s/rec" % out,
          "export models/*.yd? --as glb --lod all -o %s/glob" % out]:
    print(">>", c)
    sh.onecmd(c)

# ---- assertions
assert open(out + "/raw/notes.txt", "rb").read() == b"compressed text " * 50
xml = open(out + "/ytd/prop.ytd.xml").read(); print(xml)
assert "<Format>D3DFMT_DXT1</Format>" in xml and "<Name>testtex_a</Name>" in xml
dds = open(out + "/ytd/prop/testtex_a.dds", "rb").read(); assert dds[:4] == b"DDS "
from PIL import Image
im = Image.open(out + "/ytd/prop/testtex_a.png"); print("png", im.size, im.getpixel((0, 0)))
assert im.getpixel((0, 0))[:3] == (255, 0, 0)       # red texel survived BGRA->RGBA
im2 = Image.open(io.BytesIO(open(out + "/ytd/prop/testtex_b.dds", "rb").read())); im2.load(); print("dxt1", im2.size, im2.convert("RGB").getpixel((0, 0)))

def check_glb(p, nmeshes_min=1, expect_img=True):
    b = open(p, "rb").read()
    magic, ver, total = struct.unpack_from("<III", b, 0)
    assert magic == 0x46546C67 and ver == 2 and total == len(b), (magic, ver, total, len(b))
    jl, jt = struct.unpack_from("<II", b, 12); assert jt == 0x4E4F534A
    doc = json.loads(b[20:20 + jl])
    bl, bt = struct.unpack_from("<II", b, 20 + jl); assert bt == 0x004E4942
    assert doc["buffers"][0]["byteLength"] == bl
    assert len(doc["meshes"]) >= nmeshes_min
    for a in doc["accessors"]:
        v = doc["bufferViews"][a["bufferView"]]; assert v["byteOffset"] + v["byteLength"] <= bl
    if expect_img: assert doc.get("images"), "no embedded texture"
    return doc
doc = check_glb(out + "/ydr/prop.glb"); print("ydr glb ok", len(doc["meshes"]), "mesh(es),", len(doc["images"]), "image(s)")
pos = doc["accessors"][doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]]
print("POSITION min/max", pos["min"], pos["max"])
assert pos["min"] == [-1, 0, -1] and pos["max"] == [1, 0, 1]       # z-up -> y-up
d2 = check_glb(out + "/ydd/peds.glb", 2); print("ydd glb ok", [m["name"] for m in d2["meshes"]])
d3 = check_glb(out + "/yft/car.glb"); print("yft glb ok")
obj = open(out + "/ydr/prop.obj").read(); assert obj.count("\nf ") == 2 and "map_Kd" in open(out + "/ydr/prop.mtl").read()
assert os.path.exists(out + "/nested/x.ytd.xml")
assert os.path.exists(out + "/rec/models/prop.glb") and os.path.exists(out + "/rec/models/prop.ytd.xml")
# magic.dat plumbing: encode with the inverse of unscramble, decode with ours
payload = os.urandom(5000)
c = zlib.compressobj(9, zlib.DEFLATED, -15); comp = c.compress(payload) + c.flush()
comp += b"\0" * ((-len(comp)) % 16)
from rpfx.crypto import aes_encrypt
enc_ = aes_encrypt(comp, KEY)
r = DotNetRandom(joaat(KEY)); rbs = [np.frombuffer(r.next_bytes(len(enc_)), np.uint8).astype(np.int32) for _ in range(4)]
scr = ((np.frombuffer(enc_, np.uint8).astype(np.int32) + sum(rbs)) & 0xFF).astype(np.uint8).tobytes()
assert unscramble_magic(scr, KEY)[:5000] == payload
print("ALL OK")
