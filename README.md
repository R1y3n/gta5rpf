# rpfx - standalone GTA V RPF browser / exporter (Python)

No .NET, no CodeWalker at runtime. `pip install numpy cryptography Pillow`, then:

    python -m rpfx --exe gta5.exe x64a.rpf        # first run scans the exe (~10-60 s), key is cached
    python -m rpfx x64a.rpf                         # later runs use the cached key
    python -m rpfx --key <base64> x64a.rpf          # or pass the AES key directly
    python -m rpfx --print-key --exe gta5.exe       # just extract/cache the key

Interactive shell (nested .rpf files behave like folders, tab-completion works):

    rpfx:/> ls -l
    rpfx:/> cd levels/gta5/props.rpf
    rpfx:/> find *.ydr
    rpfx:/> info prop_bench_01.ydr
    rpfx:/> export prop_bench_01.ydr --as glb,obj -o out
    rpfx:/> export *.ytd --as xml,dds -o out
    rpfx:/> export -r /models --as auto -o out        # whole folder; auto = ytd->xml+dds, ydr/ydd/yft->glb
    rpfx:/> export car.yft --as glb --lod all --txd /vehshare.ytd

Non-interactive: `rpfx x.rpf -c "export -r /models --as auto -o out"`.

## Formats
| type | formats |
|---|---|
| any | `raw` (decrypted + inflated bytes) |
| ytd | `xml` (CodeWalker layout) + `dds`, `png` |
| ydr / ydd / yft | `glb`, `obj` (+mtl, png textures), `xml` (summary only) |

Textures: embedded dictionaries are used automatically; otherwise `<name>.ytd` / `<name>_hi.ytd` next to the
model, or `--txd path`. Axes: Z-up -> glTF Y-up (use `--zup` to keep GTA axes). Triangle winding is flipped
by default (D3D clockwise -> glTF CCW); use `--no-flip-winding` if a model looks inside-out.

## Status - read this
Developed from CodeWalker's source (format knowledge) and tested ONLY on synthetic archives
(`python tests/test_e2e.py`) because no game files were available. Verified: .NET `Random` port,
Jenkins hash, AES, RPF7 TOC (open + AES), nested RPFs, deflate, resource page model, YTD -> DDS/PNG/XML,
mesh -> GLB (validated by trimesh + pygltflib). NOT verified against real data: NG decryption,
magic.dat unscrambling, real-world vertex layouts. Not implemented: gen9 (Enhanced) resources,
skeleton/skinning, YFT physics-child drawables, faithful CodeWalker XML for ydr/ydd/yft, ymt/ymap/ytyp
XML, XML -> resource (import).

`rpfx/magic.dat` is CodeWalker's data blob (NG keys/tables; needs the AES key from the exe to decode).
Without it everything except NG-encrypted entries still works.

## License
Derived from CodeWalker (GPL-3.0). This project is therefore GPL-3.0-or-later.
