"""Build older history for the biquote CFD market from Dukascopy's historical price data (one-off, offline).

Dukascopy publishes its history in the S3 bucket ``cfg-public-proper-wallaby`` (eu-west-1, Requester Pays: the
downloader pays AWS transfer, a few cents for everything here). Their terms: the data needs no licence or
permission, stays Dukascopy's property and is for non-commercial use (FibTrader View is free).

What it does, per instrument:
* downloads the monthly 1-minute candle files ``<SYM>/<YYYY>/<MM>_candles_min_1.bi5`` (month 0-based) and, for the
  current month, the daily ``<SYM>/<YYYY>/<MM>/<DD>/{BID,ASK}_candles_min_1.bi5`` files; raw files are cached so a
  re-run downloads nothing twice;
* takes the mid of bid and ask (what biquote shows), drops minutes without volume (weekends, closed sessions);
* builds 5m / 15m / 30m / 1h / 4h / 1D (UTC buckets, like biquote) and undoes stock splits it can see in the daily
  closes (a one-day jump of 2:1, 3:1, 4:1, 5:1, 10:1, 20:1 … that the next days confirm);
* writes one ``<biquote symbol>.npz`` per instrument (arrays ``1m`` … ``1D`` of rows ts, o, h, l, c, v; prices
  already scaled), keeping 1m for the last year, 5m–30m for the last 3 years and 1h / 4h / 1D for everything.
  (npz, not SQLite: the cache drive is FAT32.)

The server side (``import_history.py``) only adds bars older than the first biquote bar of each series.

Usage (needs AWS credentials in the environment; numpy, boto3):
    python dukascopy_history.py --out /Volumes/CACHE/dukascopy/out --cache /Volumes/CACHE/dukascopy/raw [--only EURUSD,US500]
"""
from __future__ import annotations

import argparse
import json
import lzma
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

BUCKET = "cfg-public-proper-wallaby"
MIN = 60_000
IVMS = {"1m": MIN, "5m": 5 * MIN, "15m": 15 * MIN, "30m": 30 * MIN, "1h": 60 * MIN, "4h": 240 * MIN, "1D": 1440 * MIN}
KEEP_DAYS = {"1m": 366, "5m": 3 * 366, "15m": 3 * 366, "30m": 3 * 366}      # 1h / 4h / 1D: all

# biquote symbol -> Dukascopy instrument (61 of our 70; PDD, JD, BEKE, FUTU, BB, aluminium, nickel, zinc and lead
# are not in Dukascopy's catalogue)
FX = ("EURUSD GBPUSD USDJPY USDCHF AUDUSD USDCAD NZDUSD EURGBP EURJPY EURCHF EURAUD EURCAD EURNZD GBPJPY GBPCHF "
      "GBPAUD GBPCAD GBPNZD AUDJPY AUDCHF AUDCAD AUDNZD NZDJPY NZDCHF NZDCAD CADJPY CADCHF CHFJPY").split()
MAP = {**{p: p for p in FX},
       "XAUUSD": "XAUUSD", "XAGUSD": "XAGUSD", "XPTUSD": "XPTCMDUSD", "XPDUSD": "XPDCMDUSD",
       "XCUUSD": "COPPERCMDUSD", "XNGUSD": "GASCMDUSD", "USOIL": "LIGHTCMDUSD", "UKOIL": "BRENTCMDUSD",
       "US500": "USA500IDXUSD", "USTEC": "USATECHIDXUSD", "US30": "USA30IDXUSD", "DXY": "DOLLARIDXUSD",
       "DE30": "DEUIDXEUR", "FR40": "FRAIDXEUR", "UK100": "GBRIDXGBP", "STOXX50": "EUSIDXEUR",
       "JP225": "JPNIDXJPY", "HK50": "HKGIDXHKD", "AUS200": "AUSIDXAUD",
       **{s: f"{s}USUSD" for s in "AAPL MSFT NVDA TSLA AMZN NFLX AMD INTC BA BAC TSM BABA FTNT".split()},
       "META": "FBUSUSD"}
STOCKS = set("AAPL MSFT NVDA TSLA AMZN NFLX AMD INTC BA BAC TSM BABA FTNT META".split())

# monthly file record: ask (open, close, low, high, volume) then bid (same), time = month start + index minutes
MONTH_DT = np.dtype([("ao", ">u4"), ("ac", ">u4"), ("al", ">u4"), ("ah", ">u4"), ("av", ">f4"),
                     ("bo", ">u4"), ("bc", ">u4"), ("bl", ">u4"), ("bh", ">u4"), ("bv", ">f4")])
# daily BID / ASK file record: seconds from day start, open, close, low, high, volume
DAY_DT = np.dtype([("t", ">u4"), ("o", ">u4"), ("c", ">u4"), ("l", ">u4"), ("h", ">u4"), ("v", ">f4")])


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


class Source:
    def __init__(self, cache: Path):
        import boto3
        self.s3 = boto3.client("s3", region_name="eu-west-1")
        self.cache = cache
        self.downloaded = 0
        self.requests = 0

    def prefixes(self, prefix: str) -> list[str]:
        out, token = [], None
        while True:
            kw = {"Bucket": BUCKET, "Prefix": prefix, "Delimiter": "/", "RequestPayer": "requester"}
            if token:
                kw["ContinuationToken"] = token
            r = self.s3.list_objects_v2(**kw)
            self.requests += 1
            out += [p["Prefix"] for p in r.get("CommonPrefixes", [])]
            out += [o["Key"] for o in r.get("Contents", [])]
            if not r.get("IsTruncated"):
                return out
            token = r["NextContinuationToken"]

    def get(self, key: str, refresh: bool = False) -> bytes | None:
        path = self.cache / key
        if path.exists() and not refresh:
            return path.read_bytes()
        try:
            body = self.s3.get_object(Bucket=BUCKET, Key=key, RequestPayer="requester")["Body"].read()
        except self.s3.exceptions.NoSuchKey:
            return None
        self.requests += 1
        self.downloaded += len(body)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        return body


def month_start_ms(y: int, m0: int) -> int:
    return int(datetime(y, m0 + 1, 1, tzinfo=timezone.utc).timestamp() * 1000)


def decode_month(raw: bytes, y: int, m0: int) -> np.ndarray:
    """-> rows [ts, o, h, l, c, v] (price integers as float, scaled later), minutes with volume only."""
    a = np.frombuffer(lzma.decompress(raw), dtype=MONTH_DT)
    ts = month_start_ms(y, m0) + np.arange(len(a), dtype=np.int64) * MIN
    keep = (a["av"] > 0) | (a["bv"] > 0)
    a, ts = a[keep], ts[keep]
    mid = lambda x, y_: (a[x].astype(np.float64) + a[y_].astype(np.float64)) / 2  # noqa: E731
    o, c = mid("ao", "bo"), mid("ac", "bc")
    h = np.maximum(mid("ah", "bh"), np.maximum(o, c))
    lo = np.minimum(mid("al", "bl"), np.minimum(o, c))
    v = a["bv"].astype(np.float64)
    return np.column_stack([ts.astype(np.float64), o, h, lo, c, v])


def decode_day(bid_raw: bytes, ask_raw: bytes, day_ms: int) -> np.ndarray:
    b = np.frombuffer(lzma.decompress(bid_raw), dtype=DAY_DT)
    a = np.frombuffer(lzma.decompress(ask_raw), dtype=DAY_DT)
    if len(a) != len(b) or not np.array_equal(a["t"], b["t"]):
        return np.empty((0, 6))
    keep = (a["v"] > 0) | (b["v"] > 0)
    a, b = a[keep], b[keep]
    ts = day_ms + b["t"].astype(np.int64) * 1000
    mid = lambda f: (a[f].astype(np.float64) + b[f].astype(np.float64)) / 2  # noqa: E731
    o, c = mid("o"), mid("c")
    h = np.maximum(mid("h"), np.maximum(o, c))
    lo = np.minimum(mid("l"), np.minimum(o, c))
    return np.column_stack([ts.astype(np.float64), o, h, lo, c, b["v"].astype(np.float64)])


def aggregate(m1: np.ndarray, ivms: int) -> np.ndarray:
    if not len(m1):
        return m1
    ts = m1[:, 0].astype(np.int64)
    bucket = ts - ts % ivms
    starts = np.flatnonzero(np.r_[True, bucket[1:] != bucket[:-1]])
    ends = np.r_[starts[1:], len(m1)] - 1
    return np.column_stack([bucket[starts].astype(np.float64), m1[starts, 1],
                            np.maximum.reduceat(m1[:, 2], starts), np.minimum.reduceat(m1[:, 3], starts),
                            m1[ends, 4], np.add.reduceat(m1[:, 5], starts)])


def split_factors(daily: np.ndarray) -> list[tuple[int, float]]:
    """[(ts of the first bar after the split, ratio old / new)] — a one-day jump close to a whole ratio that holds."""
    out = []
    closes, opens = daily[:, 4], daily[:, 1]
    for i in range(1, len(daily) - 3):
        r = closes[i - 1] / opens[i]
        for k in (2, 3, 4, 5, 6, 8, 10, 15, 20, 25, 30, 40, 50):
            for ratio in (k, 1 / k):
                if abs(r / ratio - 1) < 0.06:
                    after = np.median(closes[i:i + 3])
                    if abs(closes[i - 1] / after / ratio - 1) < 0.12:
                        out.append((int(daily[i, 0]), ratio))
    return out


def build(src: Source, bq: str, dk: str, now_ms: int) -> dict[str, np.ndarray]:
    years = sorted(int(p.rstrip("/").split("/")[-1]) for p in src.prefixes(f"{dk}/") if p.rstrip("/").split("/")[-1].isdigit())
    pieces: list[np.ndarray] = []
    cur = datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc)
    for y in years:
        if y < 2000:
            continue
        keys = set(src.prefixes(f"{dk}/{y}/"))
        for m0 in range(12):
            key = f"{dk}/{y}/{m0:02d}_candles_min_1.bi5"
            if key in keys:
                raw = src.get(key)
                if raw:
                    pieces.append(decode_month(raw, y, m0))
            elif (y, m0) == (cur.year, cur.month - 1) and f"{dk}/{y}/{m0:02d}/" in keys:
                for dp in src.prefixes(f"{dk}/{y}/{m0:02d}/"):
                    d = dp.rstrip("/").split("/")[-1]
                    if not dp.endswith("/") or not d.isdigit():
                        continue
                    day_ms = int(datetime(y, m0 + 1, int(d), tzinfo=timezone.utc).timestamp() * 1000)
                    bid = src.get(f"{dk}/{y}/{m0:02d}/{d}/BID_candles_min_1.bi5", refresh=True)
                    ask = src.get(f"{dk}/{y}/{m0:02d}/{d}/ASK_candles_min_1.bi5", refresh=True)
                    if bid and ask:
                        pieces.append(decode_day(bid, ask, day_ms))
    if not pieces:
        return {}
    m1 = np.concatenate(pieces)
    m1 = m1[np.argsort(m1[:, 0], kind="stable")]
    m1 = m1[np.r_[True, m1[1:, 0] != m1[:-1, 0]]]
    out = {iv: (m1 if iv == "1m" else aggregate(m1, ms)) for iv, ms in IVMS.items()}
    if bq in STOCKS:
        for ts, ratio in split_factors(out["1D"]):
            log(f"  {bq}: split at {datetime.fromtimestamp(ts / 1000, tz=timezone.utc):%Y-%m-%d}, ratio {ratio:g}")
            for iv, arr in out.items():
                before = arr[:, 0] < ts
                arr[before, 1:5] /= ratio
    for iv, days in KEEP_DAYS.items():
        out[iv] = out[iv][out[iv][:, 0] >= now_ms - days * 86_400_000]
    return out


def biquote_scale(bq: str, daily: np.ndarray) -> float:
    """Power of ten that turns Dukascopy's integer prices into biquote's level (compared on common days)."""
    url = f"https://biquote.io/api/{bq}/ohlc?interval=1d&limit=1000"
    data = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "fibtrader-view"}), timeout=30))
    ref = {int(datetime.fromisoformat(b["openTime"].replace("Z", "+00:00")).timestamp() * 1000): b["close"]
           for b in data["bars"] if not b.get("isOpen")}
    pairs = [(ref[int(t)], c) for t, c in zip(daily[:, 0], daily[:, 4]) if int(t) in ref]
    if len(pairs) < 20:
        raise RuntimeError(f"{bq}: only {len(pairs)} common days with biquote")
    ratio = float(np.median([c / r for r, c in pairs]))
    p = round(np.log10(ratio))
    scale = 10.0 ** p
    diffs = np.array([abs(c / scale / r - 1) for r, c in pairs])
    log(f"  {bq}: scale 1e{p}, close vs biquote on {len(pairs)} days: median {np.median(diffs) * 100:.3f}% "
        f"p95 {np.percentile(diffs, 95) * 100:.3f}%")
    return scale


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--only", default="")
    args = ap.parse_args()
    src = Source(Path(args.cache))
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    now_ms = int(time.time() * 1000)
    todo = [s for s in MAP if not args.only or s in args.only.split(",")]
    summary = {}
    for bq in todo:
        t0 = time.time()
        try:
            out = build(src, bq, MAP[bq], now_ms)
            if not out:
                log(f"{bq}: no data")
                continue
            scale = biquote_scale(bq, out["1D"])
        except Exception as exc:  # noqa: BLE001
            log(f"{bq}: FAILED {exc}")
            summary[bq] = {"error": str(exc)}
            continue
        n = {}
        for iv, arr in out.items():
            arr[:, 1:5] /= scale
            n[iv] = len(arr)
        np.savez_compressed(out_dir / f"{bq}.npz", **out)
        first = datetime.fromtimestamp(out["1D"][0, 0] / 1000, tz=timezone.utc).strftime("%Y-%m-%d") if len(out["1D"]) else "-"
        summary[bq] = {"from": first, **n}
        log(f"{bq} ({MAP[bq]}): from {first}, {n} in {time.time() - t0:.0f}s; downloaded so far "
            f"{src.downloaded / 1e6:.0f} MB, {src.requests} requests")
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
