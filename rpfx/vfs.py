"""Linux-style virtual filesystem over a (possibly nested) RPF."""
import fnmatch
from typing import Dict, List, Optional, Tuple

from .rpf import RpfArchive, RpfEntry


class Node:
    __slots__ = ("archive", "entry")

    def __init__(self, archive: RpfArchive, entry: RpfEntry):
        self.archive, self.entry = archive, entry

    @property
    def name(self) -> str:
        return self.entry.name or self.archive.name

    @property
    def is_container(self) -> bool:
        return self.entry.is_dir or self.entry.is_rpf

    def __eq__(self, o):
        return isinstance(o, Node) and o.entry is self.entry

    def __hash__(self):
        return id(self.entry)


class Vfs:
    def __init__(self, root_archive: RpfArchive):
        self._open: Dict[int, RpfArchive] = {}
        self.root = Node(root_archive, root_archive.root)

    def _sub(self, node: Node) -> RpfArchive:
        key = id(node.entry)
        if key not in self._open:
            self._open[key] = node.archive.open_nested(node.entry)
        return self._open[key]

    def enter(self, node: Node) -> Node:
        """RPF file node -> root node of the nested archive; dirs unchanged."""
        if node.entry.is_rpf:
            sub = self._sub(node)
            return Node(sub, sub.root)
        return node

    def children(self, node: Node) -> List[Node]:
        node = self.enter(node)
        kids = [Node(node.archive, c) for c in node.entry.children]
        kids.sort(key=lambda n: (not n.is_container, n.name.lower()))
        return kids

    def find_child(self, node: Node, name: str) -> Optional[Node]:
        node = self.enter(node)
        c = node.entry.child(name)
        return Node(node.archive, c) if c else None

    def resolve(self, stack: List[Node], path: str) -> List[Node]:
        """Return the new stack (root..target). Raises FileNotFoundError."""
        st = [self.root] if path.startswith("/") else list(stack)
        for tok in [t for t in path.split("/") if t]:
            if tok == ".":
                continue
            if tok == "..":
                if len(st) > 1:
                    st.pop()
                continue
            cur = st[-1]
            if not cur.is_container:
                raise FileNotFoundError(path)
            ch = self.find_child(cur, tok)
            if ch is None:
                raise FileNotFoundError(path)
            st.append(ch)
        return st

    def glob(self, stack: List[Node], pattern: str) -> List[Tuple[List[Node], Node]]:
        """Expand a (last-component) wildcard; returns [(stack_of_parent, node)]."""
        head, _, tail = pattern.rpartition("/")
        if not any(c in tail for c in "*?["):
            st = self.resolve(stack, pattern)
            return [(st[:-1], st[-1])]
        base = self.resolve(stack, head if head else ("/" if pattern.startswith("/") else "."))
        out = []
        for ch in self.children(base[-1]):
            if fnmatch.fnmatch(ch.name.lower(), tail.lower()):
                out.append((base, ch))
        return out

    @staticmethod
    def path_of(stack: List[Node]) -> str:
        return "/" + "/".join(n.name for n in stack[1:])

    def walk(self, node: Node, prefix: str = ""):
        """Yield (relative_path, node) for all files below node (depth-first)."""
        for ch in self.children(node):
            rel = prefix + ch.name
            if ch.is_container:
                yield from self.walk(ch, rel + "/")
            else:
                yield rel, ch
