"""Route (loop) generation and splitting routes between bots.

A route is a loop XRP -> T1 -> ... -> Tk -> XRP with 3 to 5 hops, where each hop
is a market that actually exists (an order book or AMM pool).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Dict, Iterable, List, Sequence, Set, Tuple

from .amounts import XRP, Asset


@dataclass(frozen=True)
class Route:
    assets: Tuple[Asset, ...]  # XRP, T1, ..., Tk, XRP

    @property
    def hops(self) -> int:
        return len(self.assets) - 1

    @property
    def tokens(self) -> Tuple[Asset, ...]:
        return self.assets[1:-1]

    @property
    def key(self) -> str:
        return ">".join(a.key for a in self.assets)

    @property
    def label(self) -> str:
        return " → ".join(a.short for a in self.assets)

    def edge_keys(self) -> List[Tuple[str, str]]:
        return [(self.assets[i].key, self.assets[i + 1].key) for i in range(self.hops)]

    @property
    def token_set_key(self) -> str:
        return "|".join(sorted(a.key for a in self.tokens))

    @staticmethod
    def from_key(key: str) -> "Route":
        return Route(tuple(Asset.from_key(k) for k in key.split(">")))


def build_routes(
    pairs: Iterable[Tuple[Asset, Asset]],
    min_hops: int = 3,
    max_hops: int = 5,
) -> List[Route]:
    """Enumerate simple loops starting and ending at XRP.

    `pairs` are the directed markets that exist (src, dst). Tokens are never
    repeated inside a loop (repeating would just be a smaller loop plus noise).
    """
    adj: Dict[Asset, Set[Asset]] = {}
    for a, b in pairs:
        if a == b:
            continue
        adj.setdefault(a, set()).add(b)

    routes: List[Route] = []

    def dfs(path: List[Asset]):
        node = path[-1]
        hops_so_far = len(path) - 1
        for nxt in sorted(adj.get(node, ())):
            if nxt == XRP:
                if hops_so_far + 1 >= min_hops:
                    routes.append(Route(tuple(path + [XRP])))
                continue
            if nxt in path:
                continue
            # need at least one more hop back to XRP after nxt
            if hops_so_far + 2 > max_hops:
                continue
            path.append(nxt)
            dfs(path)
            path.pop()

    dfs([XRP])
    return routes


def _stable_hash(s: str) -> int:
    return int(hashlib.sha256(s.encode()).hexdigest()[:12], 16)


def partition_routes(routes: Sequence[Route], n_bots: int) -> List[List[Route]]:
    """Split routes between bots so they don't compete.

    All loops over the same set of tokens (in any order/direction) go to the
    same bot, because they trade the same markets. Groups of short loops are
    handed out first (they are cheapest to trade, so this gives every bot
    some), then the rest go wherever there are fewest routes. Deterministic:
    the same inputs always give the same split. Execution additionally uses
    market locks, so two bots can never hit the same market at the same time.
    """
    if n_bots <= 0:
        raise ValueError("n_bots must be >= 1")
    groups: Dict[str, List[Route]] = {}
    for r in routes:
        groups.setdefault(r.token_set_key, []).append(r)
    ordered = sorted(
        groups.items(), key=lambda kv: (min(r.hops for r in kv[1]), -len(kv[1]), _stable_hash(kv[0]))
    )
    buckets: List[List[Route]] = [[] for _ in range(n_bots)]
    for _, rs in ordered:
        target = min(range(n_bots), key=lambda i: (len(buckets[i]), i))
        buckets[target].extend(sorted(rs, key=lambda r: r.key))
    return buckets
