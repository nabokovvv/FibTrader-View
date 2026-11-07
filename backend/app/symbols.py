"""Unified symbol registry: ids are "<market>:<SYMBOL>"; aliases map friendly names to ids; cross-market search."""
from __future__ import annotations

import asyncio
import logging
import time

from .markets.base import Provider, SymbolInfo

log = logging.getLogger("lv.symbols")

MARKET_ORDER = ["bybit", "us", "moex", "yahoo", "hl", "bq", "bybit-spot"]
MARKET_LABEL = {"bybit": "Bybit Perp", "bybit-spot": "Bybit Spot", "us": "US Stocks", "moex": "MOEX", "yahoo": "Global",
                "hl": "Hyperliquid", "bq": "CFD"}
# Alias priority: earlier markets win when the same alias is declared twice (exchange data before Hyperliquid perps;
# biquote CFDs last, so the names people already use keep opening what they opened before).
ALIAS_PRIORITY = ["yahoo", "moex", "us", "bybit", "bybit-spot", "hl", "bq"]


class SymbolRegistry:
    def __init__(self, providers: dict[str, Provider]):
        self.providers = providers
        self._cache: dict[str, SymbolInfo | None] = {}
        self._alias: dict[str, str] = {}
        self._alias_ts = 0.0
        self._lists: dict[str, list[SymbolInfo]] = {}
        self._lists_ts = 0.0

    # ------------------------------------------------------------------ lists
    async def lists(self) -> dict[str, list[SymbolInfo]]:
        if self._fresh():
            return self._lists
        lock = self.__dict__.setdefault("_refresh_lock", asyncio.Lock())
        async with lock:  # single flight: concurrent cold requests share one refresh
            if self._fresh():
                return self._lists
            return await self._refresh_lists()

    def _fresh(self) -> bool:
        age = time.time() - self._lists_ts
        ok = getattr(self, "_lists_ok", False)
        return age < (3600 if ok else 30)  # a failed (or partly failed) refresh is retried after 30 s

    async def _refresh_lists(self) -> dict[str, list[SymbolInfo]]:
        results = await asyncio.gather(*(p.symbols() for p in self.providers.values()), return_exceptions=True)
        lists: dict[str, list[SymbolInfo]] = {}
        self._lists_ok = not any(isinstance(r, BaseException) or not r for r in results)
        for (market, _), res in zip(self.providers.items(), results):
            if isinstance(res, BaseException):
                log.warning("symbols for %s failed: %s", market, res)
                lists[market] = self._lists.get(market, [])
            else:
                lists[market] = res
        self._lists, self._lists_ts = lists, time.time()
        alias: dict[str, str] = {}
        for market in ALIAS_PRIORITY:
            for s in lists.get(market, []):
                for a in s.aliases:
                    alias.setdefault(a.upper(), s.id)
                alias.setdefault(s.id.upper(), s.id)
        self._alias = alias
        self._by_id = {f"{m}:{x.symbol}": x for m, xs in lists.items() for x in xs}
        return lists

    # -------------------------------------------------------------------- get
    async def get(self, ident: str) -> SymbolInfo | None:
        hit = self._cache.get(ident)
        if hit is not None:
            return hit
        info = await self._lookup(ident)
        if info is not None:
            self._cache[info.id] = info  # only canonical ids: client-chosen spellings must not grow the cache
        return info

    async def _lookup(self, ident: str) -> SymbolInfo | None:
        await self.lists()
        if ":" not in ident:
            target = self._alias.get(ident.upper())
            if not target:
                return None
            ident = target
        market, _, symbol = ident.partition(":")
        provider = self.providers.get(market)
        if provider is None or not symbol:
            return None
        index = getattr(self, "_by_id", {})
        hit = index.get(f"{market}:{symbol}") or (index.get(f"{market}:{symbol.upper()}") if market.startswith("bybit") else None)
        if hit is not None:
            return hit
        if market.startswith("bybit"):
            return None  # Bybit ids come only from the catalogue (refreshed above): no per-request network fallback
        return await provider.resolve(symbol)

    # ----------------------------------------------------------------- search
    async def search(self, query: str, limit: int = 40, markets: list[str] | None = None) -> list[SymbolInfo]:
        q = query.strip()
        if not q:
            return []
        qu = q.upper()
        qn = qu.replace(" ", "")           # "snp 500" == "SNP500"
        lists = await self.lists()
        scored: list[tuple[int, int, SymbolInfo]] = []
        seen: set[str] = set()
        for mi, market in enumerate(MARKET_ORDER):
            if markets and market not in markets:
                continue
            for s in lists.get(market, []):
                sym = s.symbol.upper()
                disp = (s.display or s.symbol).upper()
                name = s.name.upper()
                aliases = [a.upper() for a in s.aliases]
                if market == "hl":
                    sym = disp  # "xyz:TSLA" is an internal name: people type TSLA
                    aliases = [a for a in aliases if a != disp]  # its own ticker is no alias (exchange data first)
                score = 0
                if disp == qu and disp != sym:
                    score = 110          # the UI name of a cryptic ticker (SPX for ^GSPC) is what people type
                elif qu in aliases or qn in (a.replace(" ", "") for a in aliases):
                    score = 105          # curated alias (GOLD -> gold futures) beats a ticker that merely equals it
                elif sym == qu or disp == qu:
                    score = 100
                elif sym.startswith(qu):
                    score = 80 - min(len(sym) - len(qu), 20)
                elif any(a.startswith(qu) for a in aliases):
                    score = 70
                elif qu in sym:
                    score = 50
                elif len(qu) >= 2 and (qu in name or any(qu in a for a in aliases)):
                    score = 40 - (10 if not name.startswith(qu) else 0)
                if score:
                    if s.market == "bybit" and not sym.endswith("USDT"):
                        score -= 15
                    elif s.market == "hl":
                        if s.type == "crypto":
                            score -= 30   # crypto: the Bybit USDT perpetual comes first
                        else:
                            if score < 100:
                                score -= 10   # "BTC" should not open with BTCD (dominance) before BTCUSDT
                            if not s.extra.get("primary", True):
                                score -= 5    # the same stock on a smaller Hyperliquid exchange
                    elif s.market == "bq":
                        score -= 6    # CFDs after Hyperliquid (its history is much longer): GOLD, TSLA open HL first
                    scored.append((score, -mi, s))
                    seen.add(s.id)
        scored.sort(key=lambda t: (-t[0], -t[1], t[2].symbol))
        out = [s for _, _, s in scored[:limit]]

        yahoo = self.providers.get("yahoo")
        if yahoo is not None and hasattr(yahoo, "search") and len(q) >= 2 and len(out) < limit \
                and (not markets or "yahoo" in markets):
            try:
                extra = await asyncio.wait_for(yahoo.search(q, limit=10), timeout=3.0)  # type: ignore[attr-defined]
            except (asyncio.TimeoutError, Exception) as exc:  # noqa: BLE001
                log.debug("yahoo search failed: %s", exc)
                extra = []
            for s in extra:
                if s.id in seen:
                    continue
                # Prefer our own Bybit/US/MOEX listings over Yahoo duplicates of the same ticker.
                if any(s.symbol == o.symbol and o.market in ("us",) for o in out):
                    continue
                out.append(s)
                seen.add(s.id)
                if len(out) >= limit:
                    break
        return out
