"""Small, dependency-free graph algorithms.

The original tool listed ``networkx`` as a requirement and never imported it.
Everything actually needed here is a few dozen lines, so the engine keeps a
zero-dependency install and still gets real cycle detection and centrality.
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple


class DiGraph:
    """A directed graph keyed by hashable node ids."""

    def __init__(self) -> None:
        self._out: Dict[str, Set[str]] = defaultdict(set)
        self._in: Dict[str, Set[str]] = defaultdict(set)
        self._nodes: Set[str] = set()
        self.edge_data: Dict[Tuple[str, str], dict] = {}

    # -- construction -------------------------------------------------------

    def add_node(self, node: str) -> None:
        self._nodes.add(node)
        self._out.setdefault(node, set())
        self._in.setdefault(node, set())

    def add_edge(self, src: str, dst: str, **data) -> None:
        self.add_node(src)
        self.add_node(dst)
        self._out[src].add(dst)
        self._in[dst].add(src)
        if data:
            self.edge_data.setdefault((src, dst), {}).update(data)

    # -- inspection ---------------------------------------------------------

    @property
    def nodes(self) -> List[str]:
        return sorted(self._nodes)

    @property
    def edges(self) -> List[Tuple[str, str]]:
        return sorted((s, d) for s in self._out for d in self._out[s])

    def successors(self, node: str) -> Set[str]:
        return self._out.get(node, set())

    def predecessors(self, node: str) -> Set[str]:
        return self._in.get(node, set())

    def out_degree(self, node: str) -> int:
        return len(self._out.get(node, ()))

    def in_degree(self, node: str) -> int:
        return len(self._in.get(node, ()))

    def __contains__(self, node: str) -> bool:
        return node in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    # -- algorithms ---------------------------------------------------------

    def reachable_from(self, sources: Iterable[str]) -> Set[str]:
        """Every node reachable from any of ``sources`` (BFS)."""
        seen: Set[str] = set()
        queue = deque(s for s in sources if s in self._nodes)
        seen.update(queue)
        while queue:
            node = queue.popleft()
            for nxt in self._out.get(node, ()):
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append(nxt)
        return seen

    def strongly_connected_components(self) -> List[List[str]]:
        """Tarjan's algorithm, iterative so deep graphs cannot blow the stack."""
        index_of: Dict[str, int] = {}
        low: Dict[str, int] = {}
        on_stack: Set[str] = set()
        stack: List[str] = []
        result: List[List[str]] = []
        counter = 0

        for root in self.nodes:
            if root in index_of:
                continue
            work: List[Tuple[str, int]] = [(root, 0)]
            while work:
                node, pointer = work[-1]
                if pointer == 0:
                    index_of[node] = low[node] = counter
                    counter += 1
                    stack.append(node)
                    on_stack.add(node)
                recursed = False
                children = sorted(self._out.get(node, ()))
                for i in range(pointer, len(children)):
                    child = children[i]
                    if child not in index_of:
                        work[-1] = (node, i + 1)
                        work.append((child, 0))
                        recursed = True
                        break
                    if child in on_stack:
                        low[node] = min(low[node], index_of[child])
                if recursed:
                    continue
                if low[node] == index_of[node]:
                    component: List[str] = []
                    while True:
                        top = stack.pop()
                        on_stack.discard(top)
                        component.append(top)
                        if top == node:
                            break
                    result.append(sorted(component))
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
        return result

    def cycles(self, min_size: int = 2) -> List[List[str]]:
        """Groups of nodes that mutually depend on each other."""
        out = [c for c in self.strongly_connected_components() if len(c) >= min_size]
        # A self-loop is a cycle too.
        for node in self.nodes:
            if node in self._out.get(node, ()) and [node] not in out:
                out.append([node])
        return sorted(out, key=len, reverse=True)

    def pagerank(self, damping: float = 0.85, iterations: int = 40) -> Dict[str, float]:
        """Importance by incoming links.

        Better than raw in-degree for "what breaks the most if I change it":
        being used by an important module counts for more than being used by
        a leaf.
        """
        nodes = self.nodes
        n = len(nodes)
        if n == 0:
            return {}
        rank = {node: 1.0 / n for node in nodes}
        dangling = [x for x in nodes if not self._out.get(x)]
        for _ in range(iterations):
            leaked = damping * sum(rank[d] for d in dangling) / n
            nxt = {node: (1.0 - damping) / n + leaked for node in nodes}
            for node in nodes:
                succ = self._out.get(node, ())
                if not succ:
                    continue
                share = damping * rank[node] / len(succ)
                for target in succ:
                    nxt[target] += share
            rank = nxt
        total = sum(rank.values()) or 1.0
        return {k: v / total for k, v in rank.items()}

    def longest_path_length(self, limit: int = 5000) -> int:
        """Depth of the dependency chain, ignoring back edges in cycles."""
        depth: Dict[str, int] = {}
        visiting: Set[str] = set()

        def walk(node: str, budget: int) -> int:
            if node in depth:
                return depth[node]
            if node in visiting or budget <= 0:
                return 0
            visiting.add(node)
            best = 0
            for child in self._out.get(node, ()):
                best = max(best, 1 + walk(child, budget - 1))
            visiting.discard(node)
            depth[node] = best
            return best

        import sys
        old = sys.getrecursionlimit()
        sys.setrecursionlimit(max(old, 10000))
        try:
            return max((walk(n, limit) for n in self.nodes), default=0)
        finally:
            sys.setrecursionlimit(old)

    def to_dict(self) -> Dict[str, object]:
        return {
            "nodes": [{"id": n, "in": self.in_degree(n), "out": self.out_degree(n)}
                      for n in self.nodes],
            "edges": [{"from": s, "to": d, **self.edge_data.get((s, d), {})}
                      for s, d in self.edges],
        }


def instability(graph: DiGraph, node: str) -> float:
    """Martin's instability metric: ``out / (in + out)``.

    0 means nothing depends on this and it depends on everything (a leaf
    consumer); 1 means everything depends on it. Stable, highly-depended-upon
    modules are the expensive ones to change.
    """
    fan_out = graph.out_degree(node)
    fan_in = graph.in_degree(node)
    total = fan_in + fan_out
    return round(fan_out / total, 3) if total else 0.0
