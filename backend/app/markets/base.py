"""Provider protocol and shared data types."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Protocol, runtime_checkable

# [ts_ms, open, high, low, close, volume]
Bar = list[float]


@dataclass
class SymbolInfo:
    id: str                     # "<market>:<SYMBOL>", e.g. "bybit:BTCUSDT"
    market: str                 # provider key
    symbol: str                 # provider-native ticker
    name: str                   # human name
    type: str                   # crypto | stock | etf | index | future | fund | currency
    exchange: str               # display exchange, e.g. BYBIT, NASDAQ, MOEX, COMEX
    currency: str = "USD"
    price_precision: int = 2
    volume_precision: int = 0
    timezone: str = "UTC"       # exchange timezone (session logic / display hint)
    session: str = "24x7"       # informational
    aliases: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)
    display: str = ""           # name shown in the UI when the provider ticker is cryptic (^GSPC -> SPX)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("extra", None)
        d["display"] = self.display or self.symbol
        return d


@dataclass
class Quote:
    id: str
    price: float
    change_pct: float | None = None   # vs previous close / 24h
    ts: int | None = None             # ms

    def to_dict(self) -> dict:
        return {"id": self.id, "p": self.price, "ch": self.change_pct, "t": self.ts}


@runtime_checkable
class Provider(Protocol):
    market: str
    native_intervals: frozenset[str]

    async def symbols(self) -> list[SymbolInfo]:
        """Full (or curated) instrument list for search/watchlists."""
        ...

    async def resolve(self, symbol: str) -> SymbolInfo | None:
        """Resolve a ticker not present in symbols() (e.g. arbitrary Yahoo ticker)."""
        ...

    async def fetch_bars(self, symbol: str, interval: str, start_ms: int, end_ms: int) -> list[Bar]:
        """Ascending bars with open time in [start_ms, end_ms]; may include the current unclosed bar.
        Must handle provider paging internally."""
        ...

    def history_floor(self, interval: str) -> int | None:
        """Earliest reachable open time (ms) for this interval, or None if unlimited."""
        ...

    async def quotes(self, symbols: list[str]) -> list[Quote]:
        ...


def precision_from_tick(tick: float | str | None, default: int = 2) -> int:
    """Number of decimals implied by a tick size like 0.001 -> 3."""
    if tick in (None, "", 0):
        return default
    s = f"{float(tick):.12f}".rstrip("0")
    if "." not in s:
        return 0
    return max(0, len(s.split(".")[1]))
