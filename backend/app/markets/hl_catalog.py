"""Readable names for Hyperliquid HIP-3 tickers (stocks, indices, commodities, FX, ETFs).

Keyed by the bare ticker (``TSLA`` of ``xyz:TSLA``): the same ticker on another HIP-3 exchange is the same
underlying. Tickers missing here still work — they show the bare ticker and type "other" — so new listings need
no code change; add a line when a listing deserves a proper name. Only names we are sure of are listed.

Value: (name, type, aliases[, display]). ``display`` replaces the ticker in the UI (EUR -> EURUSD).
"""
from __future__ import annotations

_S, _E, _I, _C, _F = "stock", "etf", "index", "commodity", "currency"

CATALOG: dict[str, tuple] = {
    # indices
    "XYZ100": ("Nasdaq-100", _I, ["NDX", "NASDAQ", "NASDAQ100", "NAS100", "US100", "NQ"]),
    "SP500": ("S&P 500", _I, ["SPX", "S&P500", "SNP500", "US500", "ES"]),
    "JP225": ("Nikkei 225", _I, ["NIKKEI", "N225", "NI225"]),
    "KR200": ("KOSPI 200", _I, ["KOSPI", "KOSPI200"]),
    "NIFTY": ("Nifty 50", _I, ["NIFTY50"]),
    "TOTAL2": ("Crypto market cap excluding BTC", _I, ["TOTAL2"]),
    "OTHERS": ("Crypto market cap: others", _I, []),
    "BTCD": ("Bitcoin dominance", _I, ["BTC.D", "BTCDOM"]),
    "10Y": ("US 10-year Treasury yield", _I, ["US10Y", "TNX"]),
    "2Y": ("US 2-year Treasury yield", _I, ["US02Y", "US2Y"]),
    "30Y": ("US 30-year Treasury yield", _I, ["US30Y"]),
    # commodities
    "GOLD": ("Gold", _C, ["XAU", "XAUUSD", "GC"]),
    "SILVER": ("Silver", _C, ["XAG", "XAGUSD", "SI"]),
    "PLATINUM": ("Platinum", _C, ["XPT", "XPTUSD"]),
    "PALLADIUM": ("Palladium", _C, ["XPD", "XPDUSD"]),
    "COPPER": ("Copper", _C, ["HG", "XCU"]),
    "CL": ("WTI crude oil", _C, ["WTI", "OIL", "USOIL", "CRUDE"]),
    "BRENTOIL": ("Brent crude oil", _C, ["BRENT", "UKOIL"]),
    "NATGAS": ("Natural gas", _C, ["NG", "GAS"]),
    "HO": ("Heating oil", _C, ["HEATINGOIL"]),
    # FX (prices as quoted on Hyperliquid: EUR = USD per euro, JPY = yen per dollar)
    "EUR": ("Euro / US dollar", _F, ["EURUSD", "EUR/USD"], "EURUSD"),
    "GBP": ("British pound / US dollar", _F, ["GBPUSD", "GBP/USD"], "GBPUSD"),
    "JPY": ("US dollar / Japanese yen", _F, ["USDJPY", "USD/JPY"], "USDJPY"),
    # ETFs
    "EWY": ("iShares MSCI South Korea ETF", _E, []),
    "EWJ": ("iShares MSCI Japan ETF", _E, []),
    "EWZ": ("iShares MSCI Brazil ETF", _E, []),
    "EWT": ("iShares MSCI Taiwan ETF", _E, []),
    "XLE": ("Energy Select Sector SPDR ETF", _E, []),
    "SMH": ("VanEck Semiconductor ETF", _E, []),
    "SOXL": ("Direxion Semiconductor Bull 3x ETF", _E, []),
    "MAGS": ("Roundhill Magnificent Seven ETF", _E, []),
    "XBI": ("SPDR S&P Biotech ETF", _E, []),
    "TLT": ("iShares 20+ Year Treasury Bond ETF", _E, []),
    "URNM": ("Sprott Uranium Miners ETF", _E, ["URANIUM"]),
    "GDX": ("VanEck Gold Miners ETF", _E, []),
    "IGV": ("iShares Expanded Tech-Software ETF", _E, []),
    "KORU": ("Direxion South Korea Bull 3x ETF", _E, []),
    # stocks
    "TSLA": ("Tesla", _S, []), "NVDA": ("NVIDIA", _S, []), "HOOD": ("Robinhood", _S, []), "INTC": ("Intel", _S, []),
    "PLTR": ("Palantir", _S, []), "COIN": ("Coinbase", _S, []), "META": ("Meta Platforms", _S, []),
    "AAPL": ("Apple", _S, []), "MSFT": ("Microsoft", _S, []), "ORCL": ("Oracle", _S, []),
    "GOOGL": ("Alphabet (Google)", _S, ["GOOG", "GOOGLE"]), "AMZN": ("Amazon", _S, []), "AMD": ("AMD", _S, []),
    "MU": ("Micron Technology", _S, []), "SNDK": ("SanDisk", _S, []), "MSTR": ("Strategy (MicroStrategy)", _S, []),
    "CRCL": ("Circle", _S, []), "NFLX": ("Netflix", _S, []), "COST": ("Costco", _S, []), "LLY": ("Eli Lilly", _S, []),
    "TSM": ("TSMC", _S, []), "RIVN": ("Rivian", _S, []), "BABA": ("Alibaba", _S, []),
    "SMSN": ("Samsung Electronics", _S, ["SAMSUNG"]), "USAR": ("USA Rare Earth", _S, []), "CRWV": ("CoreWeave", _S, []),
    "GME": ("GameStop", _S, []), "SOFTBANK": ("SoftBank Group", _S, []), "HYUNDAI": ("Hyundai Motor", _S, []),
    "KIOXIA": ("Kioxia", _S, []), "HIMS": ("Hims & Hers", _S, []), "DKNG": ("DraftKings", _S, []),
    "LITE": ("Lumentum", _S, []), "BX": ("Blackstone", _S, []), "MRVL": ("Marvell", _S, []), "RKLB": ("Rocket Lab", _S, []),
    "ZM": ("Zoom", _S, []), "EBAY": ("eBay", _S, []), "ARM": ("Arm Holdings", _S, []), "ASML": ("ASML", _S, []),
    "BB": ("BlackBerry", _S, []), "DELL": ("Dell", _S, []), "IBM": ("IBM", _S, []), "AVGO": ("Broadcom", _S, []),
    "NOW": ("ServiceNow", _S, []), "NBIS": ("Nebius", _S, []), "WDC": ("Western Digital", _S, []), "NOK": ("Nokia", _S, []),
    "BE": ("Bloom Energy", _S, []), "QCOM": ("Qualcomm", _S, []), "AMAT": ("Applied Materials", _S, []),
    "GEV": ("GE Vernova", _S, []), "IREN": ("IREN", _S, []), "NET": ("Cloudflare", _S, []), "CRWD": ("CrowdStrike", _S, []),
    "RDDT": ("Reddit", _S, []), "AAOI": ("Applied Optoelectronics", _S, []), "MRNA": ("Moderna", _S, []),
    "BMNR": ("BitMine Immersion", _S, []), "CVX": ("Chevron", _S, []), "LRCX": ("Lam Research", _S, []),
    "UMC": ("United Microelectronics", _S, []), "CAMBRICON": ("Cambricon", _S, []), "TWST": ("Twist Bioscience", _S, []),
    "GLW": ("Corning", _S, []), "ICE": ("Intercontinental Exchange", _S, []), "XIAOMI": ("Xiaomi", _S, []),
    "COHR": ("Coherent", _S, []), "CRDO": ("Credo Technology", _S, []), "STX": ("Seagate", _S, []),
    "VST": ("Vistra", _S, []), "TER": ("Teradyne", _S, []), "CIEN": ("Ciena", _S, []), "MELI": ("MercadoLibre", _S, []),
    "SMCI": ("Super Micro Computer", _S, []), "SOFI": ("SoFi", _S, []), "TTWO": ("Take-Two Interactive", _S, []),
    "CIFR": ("Cipher Mining", _S, []), "IONQ": ("IonQ", _S, []), "GPRO": ("GoPro", _S, []),
    # private companies (pre-IPO perpetuals)
    "OAI": ("OpenAI (pre-IPO)", _S, ["OPENAI"]), "UNITREE": ("Unitree Robotics (pre-IPO)", _S, []),
    "SHEIN": ("Shein (pre-IPO)", _S, []), "OURA": ("Oura (pre-IPO)", _S, []),
}


def describe(ticker: str) -> tuple[str, str, list[str], str]:
    """(name, type, aliases, display) for a bare HIP-3 ticker."""
    hit = CATALOG.get(ticker)
    if hit is None:
        return ticker, "other", [], ticker
    name, typ, aliases = hit[0], hit[1], list(hit[2])
    display = hit[3] if len(hit) > 3 else ticker
    return name, typ, aliases, display
