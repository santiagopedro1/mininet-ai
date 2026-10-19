"""Rootless ADR-0003 version-1 randomness and failure selection."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from hashlib import sha256
from itertools import combinations
from typing import Literal, TypeVar

GENERATOR_VERSION = "sha256-counter-v1"
WORD_SPACE = 1 << 64
T = TypeVar("T")


class CounterStream:
    """Independent SHA-256 counter stream, with no counter wraparound."""

    def __init__(self, seed: int, label: str, *, counter: int = 0) -> None:
        if type(seed) is not int or not 0 <= seed < WORD_SPACE:
            raise ValueError("seed must be an unsigned 64-bit integer")
        if type(counter) is not int or not 0 <= counter <= WORD_SPACE:
            raise ValueError("counter must be in [0, 2^64]")
        self.key = sha256(
            b"mininet-ai/link-failure-latency/v1\x00"
            + seed.to_bytes(8, "big")
            + label.encode("utf-8")
        ).digest()
        self._counter = counter

    def word(self) -> int:
        if self._counter == WORD_SPACE:
            raise OverflowError("SHA-256 stream counter exhausted")
        digest = sha256(self.key + self._counter.to_bytes(8, "big")).digest()
        self._counter += 1
        return int.from_bytes(digest[:8], "big")

    def draw(self, n: int) -> int:
        if type(n) is not int or not 1 <= n <= WORD_SPACE:
            raise ValueError("draw bound must be in [1, 2^64]")
        limit = WORD_SPACE // n * n
        while True:
            word = self.word()
            if word < limit:
                return word % n

    def shuffle(self, items: list[T]) -> None:
        """Shuffle the supplied ordered population in place."""
        for i in range(len(items) - 1, 0, -1):
            j = self.draw(i + 1)
            items[i], items[j] = items[j], items[i]


def canonical_pair(left: str, right: str) -> tuple[str, str]:
    a, b = sorted((left, right), key=lambda value: value.encode("utf-8"))
    return a, b


@dataclass(frozen=True)
class GeneratedTopology:
    switches: tuple[str, ...]
    cycle: tuple[str, ...]
    chords: tuple[tuple[str, str], ...]
    edges: tuple[tuple[str, str], ...]
    placement_order: tuple[str, ...]
    attachments: dict[str, str]
    ports: dict[tuple[str, str], int]


def generate_topology(seed: int) -> GeneratedTopology:
    """Generate the exact fixed ten-switch ring-plus-chords distribution."""
    stream = CounterStream(seed, "topology")
    switches = tuple(sorted((f"s{i}" for i in range(1, 11)), key=str.encode))
    cycle = list(switches)
    stream.shuffle(cycle)
    cycle_edges = {
        canonical_pair(switch, cycle[(i + 1) % len(cycle)])
        for i, switch in enumerate(cycle)
    }
    candidates = [pair for pair in combinations(switches, 2) if pair not in cycle_edges]
    count = 1 + stream.draw(5)
    stream.shuffle(candidates)
    chords = tuple(candidates[:count])
    edges = tuple(sorted(cycle_edges | set(chords)))
    placement = list(switches)
    CounterStream(seed, "placement").shuffle(placement)
    attachments: dict[str, str] = dict(zip(("h1", "h2"), placement[:2], strict=True))
    ports = {}
    for switch in switches:
        neighbors = sorted(
            right if left == switch else left
            for left, right in edges
            if switch in (left, right)
        )
        neighbors.extend(
            sorted(host for host, owner in attachments.items() if owner == switch)
        )
        for number, neighbor in enumerate(neighbors, 1):
            ports[switch, neighbor] = number
    return GeneratedTopology(
        switches, tuple(cycle), chords, edges, tuple(placement), attachments, ports
    )


class FailurePasses(Iterator[tuple[str, str]]):
    """Consume one fault per attempt, with independent, fresh per-pass streams."""

    def __init__(
        self,
        seed: int,
        eligible: Iterable[tuple[str, str]],
        *,
        kind: Literal["warmup", "measured"],
    ) -> None:
        CounterStream(seed, "topology")  # Validate even before the first selection.
        if kind not in ("warmup", "measured"):
            raise ValueError("failure pass kind must be warmup or measured")
        self._eligible = sorted({canonical_pair(*pair) for pair in eligible})
        if not self._eligible or any(left == right for left, right in self._eligible):
            raise ValueError("eligible links must be nonempty distinct-node pairs")
        self._seed = seed
        self._kind = kind
        self._pass = 0
        self._remaining: Iterator[tuple[str, str]] = iter(())

    def __next__(self) -> tuple[str, str]:
        try:
            return next(self._remaining)
        except StopIteration:
            links = self._eligible.copy()
            CounterStream(
                self._seed, f"{self._kind}-failure-pass:{self._pass}"
            ).shuffle(links)
            self._pass += 1
            self._remaining = iter(links)
            return next(self._remaining)
