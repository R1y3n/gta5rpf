"""rpfx - browse a GTA V RPF like a Linux filesystem and export files."""
import argparse
import cmd
import os
import shlex
import sys
from typing import List

from .export import ExportError, default_formats, export_node, ext_of
from .keys import cache_key, find_aes_key, load_keys
from .rpf import open_rpf, page_size_from_flags
from .vfs import Node, Vfs

FORMATS = ["auto", "raw", "xml", "dds", "png", "glb", "obj"]


def _progress(p):
    sys.stderr.write("\r  searching exe for AES key... %3d%%" % int(p * 100))
    sys.stderr.flush()
    if p >= 1.0:
        sys.stderr.write("\n")


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def human(n: int) -> str:
    for u in ("B", "K", "M", "G"):
        if n < 1024 or u == "G":
            return ("%d%s" % (n, u)) if u == "B" else ("%.1f%s" % (n, u))
        n /= 1024.0


class Shell(cmd.Cmd):
    prompt = "rpfx:/> "
    intro = "rpfx - type 'help' for commands, 'quit' to leave."

    def __init__(self, vfs: Vfs):
        super().__init__()
        self.vfs = vfs
        self.stack: List[Node] = [vfs.root]
        self._set_prompt()

    # ---- helpers
    def _set_prompt(self):
        self.prompt = "rpfx:%s> " % (Vfs.path_of(self.stack) or "/")

    def _err(self, msg):
        print("rpfx: " + msg)

    def _kind(self, n: Node) -> str:
        e = n.entry
        if e.is_dir: return "d"
        if e.is_rpf: return "r"
        return "-"

    def _size(self, n: Node) -> int:
        e = n.entry
        if e.kind == "res":
            return page_size_from_flags(e.sys_flags) + page_size_from_flags(e.gfx_flags)
        return e.usize or e.size

    def _enc(self, n: Node) -> str:
        e = n.entry
        if e.is_dir: return "-"
        if e.kind == "res": return "res"
        return "enc" if e.encrypted else "bin"

    def _parse(self, line, parser):
        try:
            return parser.parse_args(shlex.split(line))
        except ValueError as ex:
            self._err(str(ex))
            return None

    # ---- navigation
    def do_pwd(self, line):
        """pwd - print the current directory"""
        print(Vfs.path_of(self.stack) or "/")

    def do_cd(self, line):
        """cd [path] - change directory (.rpf files are directories)"""
        target = shlex.split(line)[0] if line.strip() else "/"
        try:
            st = self.vfs.resolve(self.stack, target)
        except FileNotFoundError:
            return self._err("cd: no such file or directory: " + target)
        if not st[-1].is_container:
            return self._err("cd: not a directory: " + target)
        st[-1] = self.vfs.enter(st[-1])
        self.stack = st
        self._set_prompt()

    def do_ls(self, line):
        """ls [-l] [path] - list directory (-l: kind, type, size)"""
        p = _Parser(prog="ls", add_help=False)
        p.add_argument("-l", action="store_true")
        p.add_argument("path", nargs="?", default=".")
        a = self._parse(line, p)
        if not a:
            return
        try:
            st = self.vfs.resolve(self.stack, a.path)
        except FileNotFoundError:
            return self._err("ls: no such file or directory: " + a.path)
        node = st[-1]
        if not node.is_container:
            kids = [node]
        else:
            kids = self.vfs.children(node)
        for k in kids:
            nm = k.name + ("/" if k.is_container else "")
            if a.l:
                print("%s %-3s %9s  %s" % (self._kind(k), self._enc(k), human(self._size(k)) if not k.entry.is_dir else "-", nm))
            else:
                print(nm)

    def do_tree(self, line):
        """tree [path] [-d depth] - recursive listing"""
        p = _Parser(prog="tree", add_help=False)
        p.add_argument("path", nargs="?", default=".")
        p.add_argument("-d", type=int, default=3)
        a = self._parse(line, p)
        if not a:
            return
        try:
            st = self.vfs.resolve(self.stack, a.path)
        except FileNotFoundError:
            return self._err("tree: no such path: " + a.path)

        def rec(node, depth, indent):
            if depth > a.d:
                return
            for k in self.vfs.children(node):
                print("%s%s%s" % (indent, k.name, "/" if k.is_container else ""))
                if k.is_container:
                    rec(k, depth + 1, indent + "  ")
        rec(st[-1], 1, "")

    def do_find(self, line):
        """find <pattern> [path] - search (case-insensitive wildcard) below a directory"""
        import fnmatch
        parts = shlex.split(line)
        if not parts:
            return self._err("usage: find <pattern> [path]")
        pat = parts[0].lower()
        try:
            st = self.vfs.resolve(self.stack, parts[1] if len(parts) > 1 else ".")
        except FileNotFoundError:
            return self._err("find: no such path")
        base = Vfs.path_of(st).rstrip("/")
        for rel, n in self.vfs.walk(st[-1]):
            if fnmatch.fnmatch(n.name.lower(), pat):
                print(base + "/" + rel)

    def do_info(self, line):
        """info <file> - show entry details"""
        for pat in shlex.split(line):
            try:
                matches = self.vfs.glob(self.stack, pat)
            except FileNotFoundError:
                self._err("info: no such file: " + pat)
                continue
            for _, n in matches:
                e = n.entry
                print("name       :", e.name)
                print("kind       :", {"dir": "directory", "bin": "binary", "res": "resource"}[e.kind])
                if not e.is_dir:
                    print("offset     : 0x%X (sector %d)" % (e.offset * 512, e.offset))
                    print("stored size:", e.size)
                    if e.kind == "bin":
                        print("uncompressed:", e.usize, " encrypted:", e.encrypted)
                    else:
                        print("sys/gfx flags: 0x%08X / 0x%08X  (virtual %s)" % (
                            e.sys_flags, e.gfx_flags, human(self._size(n))))

    # ---- export
    def do_export(self, line):
        """export <path|glob>... [--as FMT[,FMT]] [-o DIR] [-r] [--lod high|med|low|vlow|all]
                [--txd FILE.ytd] [--no-textures] [--zup] [--no-flip-winding]
        FMT: auto raw xml dds png glb obj   (auto: ytd->xml+dds, ydr/ydd/yft->glb, else raw)"""
        p = _Parser(prog="export", add_help=False)
        p.add_argument("paths", nargs="+")
        p.add_argument("--as", dest="fmt", default="auto")
        p.add_argument("-o", dest="out", default="export")
        p.add_argument("-r", dest="rec", action="store_true")
        p.add_argument("--lod", default="high", choices=["high", "med", "low", "vlow", "all"])
        p.add_argument("--txd", action="append", default=[])
        p.add_argument("--no-textures", action="store_true")
        p.add_argument("--zup", action="store_true")
        p.add_argument("--no-flip-winding", action="store_true")
        a = self._parse(line, p)
        if not a:
            return
        fmts = [f.strip().lower() for f in a.fmt.split(",") if f.strip()]
        bad = [f for f in fmts if f not in FORMATS]
        if bad:
            return self._err("unknown format: %s (choose from %s)" % (",".join(bad), " ".join(FORMATS)))
        txd_nodes = []
        for t in a.txd:
            try:
                txd_nodes.append(self.vfs.resolve(self.stack, t)[-1])
            except FileNotFoundError:
                self._err("--txd: no such file: " + t)
        done = failed = 0
        for pat in a.paths:
            try:
                matches = self.vfs.glob(self.stack, pat)
            except FileNotFoundError:
                self._err("export: no such file: " + pat)
                failed += 1
                continue
            if not matches:
                self._err("export: nothing matches " + pat)
                failed += 1
                continue
            for pst, n in matches:
                jobs = []   # (parent_stack, node, outdir)
                if n.is_container:
                    if not a.rec:
                        self._err("export: %s is a directory (use -r)" % n.name)
                        continue
                    sub = self.vfs.enter(n)
                    base = os.path.join(a.out, n.name)
                    for st, f, prefix in self._walk_files([*pst, sub]):
                        jobs.append((st, f, os.path.join(base, prefix)))
                else:
                    jobs.append((pst, n, a.out))
                for parent_stack, node, outdir in jobs:
                    try:
                        files = export_node(self.vfs, parent_stack, node, fmts, outdir, lod=a.lod,
                                            txd_nodes=txd_nodes, y_up=not a.zup,
                                            flip_winding=not a.no_flip_winding,
                                            textures=not a.no_textures, log=print)
                        for f in files:
                            print("  wrote", f)
                        done += 1
                    except Exception as ex:
                        failed += 1
                        self._err("%s: %s" % (node.name, ex))
        print("%d exported, %d failed" % (done, failed))

    def _walk_files(self, st, prefix=""):
        """Yield (parent_stack, file_node, relative_dir_prefix) below st[-1]."""
        for ch in self.vfs.children(st[-1]):
            if ch.is_container:
                yield from self._walk_files([*st, self.vfs.enter(ch)], prefix + ch.name + "/")
            else:
                yield st, ch, prefix

    do_extract = do_export

    def do_quit(self, line):
        """quit - leave"""
        return True
    do_exit = do_EOF = do_quit

    def emptyline(self):
        pass

    # ---- completion
    def completedefault(self, text, line, begidx, endidx):
        base, _, frag = text.rpartition("/")
        try:
            st = self.vfs.resolve(self.stack, base if base else ("/" if text.startswith("/") else "."))
        except FileNotFoundError:
            return []
        return [(base + "/" if base or text.startswith("/") else "") + k.name + ("/" if k.is_container else "")
                for k in self.vfs.children(st[-1]) if k.name.lower().startswith(frag.lower())]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="rpfx", description="Browse/export GTA V RPF archives (standalone).")
    ap.add_argument("archive", nargs="?", help="path to an .rpf file")
    ap.add_argument("--exe", help="path to gta5.exe (used once to find the AES key; the key is cached)")
    ap.add_argument("--key", help="AES key as base64 (skips the exe scan)")
    ap.add_argument("--no-cache", action="store_true", help="ignore/skip the cached key")
    ap.add_argument("--print-key", action="store_true", help="find the key in --exe, print it (base64), exit")
    ap.add_argument("-c", dest="commands", action="append", default=[], help="run a command non-interactively (repeatable)")
    a = ap.parse_args(argv)

    if a.print_key:
        if not a.exe:
            ap.error("--print-key needs --exe")
        import base64
        k = find_aes_key(a.exe, _progress)
        cache_key(k)
        print(base64.b64encode(k).decode())
        return 0
    if not a.archive:
        ap.error("an .rpf archive is required")
    try:
        keys = load_keys(a.exe, a.key, _progress, use_cache=not a.no_cache)
    except Exception as ex:
        print("rpfx: " + str(ex), file=sys.stderr)
        return 2
    if not keys.has_ng:
        print("rpfx: note: magic.dat not found - NG-encrypted entries will fail.", file=sys.stderr)
    try:
        arc = open_rpf(a.archive, keys)
    except Exception as ex:
        print("rpfx: cannot open archive: %s" % ex, file=sys.stderr)
        return 2
    sh = Shell(Vfs(arc))
    if a.commands:
        for c in a.commands:
            print("rpfx> " + c)
            if sh.onecmd(c):
                break
        return 0
    try:
        import readline  # noqa: F401  (history/tab completion)
    except ImportError:
        pass
    sh.cmdloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
