"""
Crypto + Metals Futures Telegram Bot
======================================
Features:
- Live price for BTC, ETH, DOGE, LTC, BNB, SOL, XRP, AVAX  (Binance Futures)
- Live price for Gold (XAU) and Silver (XAG)                 (Bybit Futures)
- Smart price formatting — full decimals for small coins (DOGE = $0.09643)
- Technical indicators: EMA, MACD, RSI, Bollinger, SAR       (crypto only)
- Whale Activity Detection for both crypto and metals
- Market Depth Analysis                                       (crypto only)
- Set TP / SL with auto-notification
- Auto whale alert subscriptions

Setup:
1. pip install python-telegram-bot requests
2. Create bot via @BotFather and get token
3. export BOT_TOKEN="your_token_here"
4. python main.py
"""

import os
import sys
import random
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ============================================================
# CONFIGURATION
# ============================================================
BOT_TOKEN = os.environ.get("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN environment variable not set!")
    print("   export BOT_TOKEN='your_token_here'")
    sys.exit(1)

BINANCE_DIRECT_BASE = "https://fapi.binance.com"
BINANCE_API_URL     = "https://v0-binance-futures-api-two.vercel.app/api/crypto"
BYBIT_API_URL       = "https://api.bybit.com/v5"

# Crypto — via Binance Futures proxy
SYMBOLS = ["BTC", "ETH", "DOGE", "LTC", "BNB", "SOL", "XRP", "AVAX"]

# Metals — via Bybit Futures (public API, no key needed)
METAL_SYMBOLS = ["XAU", "XAG"]
METAL_BYBIT_MAP = {
    "XAU": ("XAUUSDT", "linear"),   # Gold  / USDT Linear Perpetual
    "XAG": ("XAGUSD",  "inverse"),  # Silver / USD  Inverse Perpetual
}
METAL_NAMES = {"XAU": "Gold", "XAG": "Silver"}
METAL_EMOJI = {"XAU": "🪙",   "XAG": "🥈"}
# USD value of a single order to count as whale
METAL_WHALE_THRESHOLD = {"XAU": 100_000, "XAG": 50_000}

# In-memory stores (use DB in production)
user_alerts        = {}   # {user_id: [alert_dict, ...]}
whale_subscriptions = {}  # {user_id: [symbol, ...]}
last_whale_cache   = {}   # {f"{user_id}_{symbol}": summary_dict}

MOTIVATIONAL_QUOTES = [
    "Every loss is a lesson. Keep learning!",
    "Markets reward patience. Stay strong!",
    "Even the best traders have losses. You got this!",
    "Failure is the mother of success. Keep going!",
    "One loss doesn't define you. Tomorrow is a new day!",
    "Risk management is key. You're learning!",
    "The market will always be there. Take a break if needed!",
    "Losses are tuition fees for trading education!",
    "Stay disciplined, the profits will come!",
    "Every expert was once a beginner. Keep practicing!",
]

# ============================================================
# UTILITIES
# ============================================================
def format_price(price: float) -> str:
    """
    Smart decimal formatter — auto strips trailing zeros:
      >= 100      →  2 decimals     $94,123.50   (BTC/ETH/Gold)
      1–99        →  up to 4 dec    $30.45       (Silver)
      0.0001–0.99 →  up to 6 dec    $0.09643     (DOGE) ✅  $0.5423 (XRP)
      < 0.0001    →  up to 8 dec    $0.00000123
    """
    if price >= 100:
        return f"{price:,.2f}"
    elif price >= 1:
        return f"{price:,.4f}".rstrip("0").rstrip(".")
    elif price >= 0.0001:
        return f"{price:.6f}".rstrip("0")
    else:
        return f"{price:.8f}".rstrip("0")


def format_number(num: float) -> str:
    """Compact large number formatter: 1.23B / 45.6M / 789.0K"""
    if num >= 1_000_000_000:
        return f"{num / 1_000_000_000:.2f}B"
    elif num >= 1_000_000:
        return f"{num / 1_000_000:.2f}M"
    elif num >= 1_000:
        return f"{num / 1_000:.2f}K"
    return f"{num:,.0f}"


# ============================================================
# BINANCE API  (crypto) — Direct + Vercel Fallback
# ============================================================
# ============================================================
# BINANCE DIRECT + VERCEL FALLBACK (replacement section)
# ============================================================

# ── Indicator helpers ────────────────────────────────────────

def _ema(closes, period):
    k = 2 / (period + 1)
    result = [closes[0]]
    for v in closes[1:]:
        result.append(v * k + result[-1] * (1 - k))
    return result

def _rsi(closes, period=14):
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0)); losses.append(max(-d, 0))
    ag = sum(gains[-period:]) / period
    al = sum(losses[-period:]) / period
    if al == 0: return 100.0
    return round(100 - (100 / (1 + ag/al)), 2)

def _macd(closes):
    if len(closes) < 26: return 0, 0, 0
    e12 = _ema(closes, 12); e26 = _ema(closes, 26)
    ml  = [a - b for a, b in zip(e12, e26)]
    sig = _ema(ml, 9)
    return round(ml[-1], 6), round(sig[-1], 6), round(ml[-1] - sig[-1], 6)

def _bollinger(closes, period=20, std_dev=2):
    if len(closes) < period: return closes[-1], closes[-1], closes[-1]
    window = closes[-period:]
    mid    = sum(window) / period
    var    = sum((x - mid) ** 2 for x in window) / period
    std    = var ** 0.5
    return round(mid + std_dev * std, 6), round(mid, 6), round(mid - std_dev * std, 6)

def _sar(candles):
    if len(candles) < 5: return candles[-1]["close"], True
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    af, max_af = 0.02, 0.2
    bull   = closes[1] > closes[0]
    ep     = highs[0] if bull else lows[0]
    sar    = lows[0]  if bull else highs[0]
    for i in range(2, len(candles)):
        ps = sar
        if bull:
            sar = ps + af * (ep - ps); sar = min(sar, lows[i-1], lows[i-2])
            if closes[i] > ep: ep = closes[i]; af = min(af + 0.02, max_af)
            if closes[i] < sar: bull = False; sar = ep; ep = closes[i]; af = 0.02
        else:
            sar = ps + af * (ep - sar); sar = max(sar, highs[i-1], highs[i-2])
            if closes[i] < ep: ep = closes[i]; af = min(af + 0.02, max_af)
            if closes[i] > sar: bull = True; sar = ep; ep = closes[i]; af = 0.02
    return round(sar, 8), bull

def _support_resistance(candles):
    if len(candles) < 10: return [], []
    highs   = [c["high"]  for c in candles]
    lows    = [c["low"]   for c in candles]
    current = candles[-1]["close"]
    sh, sl  = [], []
    for i in range(2, len(candles) - 2):
        if highs[i] == max(highs[i-2:i+3]): sh.append(highs[i])
        if lows[i]  == min(lows[i-2:i+3]):  sl.append(lows[i])
    def cluster(lvls, tol=0.005):
        lvls = sorted(set(round(l, 8) for l in lvls))
        if not lvls: return []
        groups = []; grp = [lvls[0]]
        for l in lvls[1:]:
            if abs(l - grp[-1]) / grp[-1] < tol: grp.append(l)
            else: groups.append(grp); grp = [l]
        if grp: groups.append(grp)
        return [round(sum(g)/len(g), 8) for g in groups]
    res = sorted([l for l in cluster(sh) if l > current])[-3:]
    sup = sorted([l for l in cluster(sl)  if l < current], reverse=True)[:3]
    return res, sup


# ── Main fetcher ─────────────────────────────────────────────

def fetch_from_binance_direct(symbol: str, interval: str = "1h", proxies: dict = None) -> dict:
    """
    Fetch from Binance Futures directly and return data
    in the same format as the Vercel proxy response.
    """
    try:
        sym = f"{symbol}USDT"

        # 1. Ticker
        t = requests.get(
            f"{BINANCE_DIRECT_BASE}/fapi/v1/ticker/24hr",
            params={"symbol": sym}, proxies=proxies, timeout=8
        ).json()
        if "lastPrice" not in t:
            return {"success": False, "error": "Binance blocked or invalid"}

        price  = float(t["lastPrice"])
        high   = float(t["highPrice"])
        low    = float(t["lowPrice"])
        change = float(t["priceChangePercent"])
        volume = float(t["volume"])

        # 2. Funding rate
        fr_data = requests.get(
            f"{BINANCE_DIRECT_BASE}/fapi/v1/fundingRate",
            params={"symbol": sym, "limit": 1}, proxies=proxies, timeout=8
        ).json()
        funding = float(fr_data[0]["fundingRate"]) if isinstance(fr_data, list) and fr_data else 0.0

        # 3. Klines
        klines = requests.get(
            f"{BINANCE_DIRECT_BASE}/fapi/v1/klines",
            params={"symbol": sym, "interval": interval, "limit": 100}, proxies=proxies, timeout=8
        ).json()
        if not isinstance(klines, list) or len(klines) < 10:
            return {"success": False, "error": "Klines fetch failed"}

        candles = [{"open": float(k[1]), "high": float(k[2]),
                    "low": float(k[3]),  "close": float(k[4]),
                    "vol": float(k[5])}  for k in klines]
        closes  = [c["close"] for c in candles]
        vols    = [c["vol"]   for c in candles]

        # 4. Depth
        depth_raw = requests.get(
            f"{BINANCE_DIRECT_BASE}/fapi/v1/depth",
            params={"symbol": sym, "limit": 50}, proxies=proxies, timeout=8
        ).json()
        bids = [[float(b[0]), float(b[1])] for b in depth_raw.get("bids", [])]
        asks = [[float(a[0]), float(a[1])] for a in depth_raw.get("asks", [])]

        # ── Compute indicators ──
        e9  = _ema(closes, 9);  e21 = _ema(closes, 21); e50 = _ema(closes, 50)
        ema_trend = "BULLISH" if e9[-1] > e21[-1] > e50[-1] else "BEARISH"

        ml, sig, hist = _macd(closes)
        macd_trend    = "BULLISH" if hist > 0 else "BEARISH"

        rsi_val = _rsi(closes)
        if rsi_val > 70:   rsi_sig = "OVERBOUGHT"
        elif rsi_val < 30: rsi_sig = "OVERSOLD"
        else:              rsi_sig = "NEUTRAL"

        bb_up, bb_mid, bb_lo = _bollinger(closes)
        if   price > bb_up: bb_pos = "OVERBOUGHT"
        elif price < bb_lo: bb_pos = "OVERSOLD"
        else:               bb_pos = "NEUTRAL"

        sar_val, sar_bull = _sar(candles)
        sar_trend = "BULLISH" if sar_bull else "BEARISH"

        # ── Support / Resistance ──
        res, sup = _support_resistance(candles)

        # ── Volume ──
        avg_vol   = sum(vols[-20:]) / 20 if len(vols) >= 20 else 1
        vol_ratio = round(vols[-1] / avg_vol, 2) if avg_vol else 1
        if vol_ratio > 1.5:   vol_sig = "HIGH"
        elif vol_ratio < 0.5: vol_sig = "LOW"
        else:                 vol_sig = "NORMAL"

        # ── Depth analysis ──
        bid_vol = sum(q for _, q in bids)
        ask_vol = sum(q for _, q in asks)
        bid_val = sum(p * q for p, q in bids)
        ask_val = sum(p * q for p, q in asks)
        ratio   = round(bid_vol / ask_vol, 2) if ask_vol else 1
        imb     = round((bid_val - ask_val) / (bid_val + ask_val) * 100, 2) if (bid_val + ask_val) else 0
        if   imb >  20: pressure = "STRONG_BUY"
        elif imb >   5: pressure = "BUY"
        elif imb < -20: pressure = "STRONG_SELL"
        elif imb <  -5: pressure = "SELL"
        else:           pressure = "NEUTRAL"

        lbid = max(bids, key=lambda x: x[0]*x[1], default=[price, 0])
        lask = max(asks, key=lambda x: x[0]*x[1], default=[price, 0])
        spread_p = asks[0][0] - bids[0][0] if bids and asks else 0

        # ── Whale detection (depth-based) ──
        whale_thresh = 500_000
        w_bids = [{"price":p,"qty":q,"value":p*q,"dist_pct":round(abs(price-p)/price*100,2)}
                  for p,q in bids if p*q >= whale_thresh]
        w_asks = [{"price":p,"qty":q,"value":p*q,"dist_pct":round(abs(p-price)/price*100,2)}
                  for p,q in asks if p*q >= whale_thresh]
        w_bids.sort(key=lambda x: x["value"], reverse=True)
        w_asks.sort(key=lambda x: x["value"], reverse=True)
        wb_vol = sum(x["value"] for x in w_bids)
        wa_vol = sum(x["value"] for x in w_asks)
        if wb_vol > wa_vol * 1.3:   w_sent = "BULLISH"
        elif wa_vol > wb_vol * 1.3: w_sent = "BEARISH"
        else:                       w_sent = "NEUTRAL"

        # ── Price action ──
        h6 = [c["high"]  for c in candles[-6:]]
        l6 = [c["low"]   for c in candles[-6:]]
        if h6[-1]>h6[-3]>h6[-5] and l6[-1]>l6[-3]>l6[-5]:
            pa_trend, pa_pattern = "UPTREND",   "HIGHER_HIGHS"
        elif h6[-1]<h6[-3]<h6[-5] and l6[-1]<l6[-3]<l6[-5]:
            pa_trend, pa_pattern = "DOWNTREND", "LOWER_LOWS"
        else:
            pa_trend, pa_pattern = "SIDEWAYS",  "NEUTRAL"

        bull_score = sum([ema_trend=="BULLISH", macd_trend=="BULLISH",
                         rsi_val<50, sar_bull, bb_pos=="OVERSOLD"])
        strength   = round(bull_score / 5 * 100)

        # ── Assemble Vercel-compatible response ──
        return {
            "success": True,
            "source":  "Binance Futures Direct",
            "data": {
                "price": {
                    "current":         price,
                    "lastFundingRate": funding,
                },
                "stats24h": {
                    "priceChangePercent": change,
                    "high":   high,
                    "low":    low,
                    "volume": volume,
                },
                "indicators": {
                    "ema": {
                        "ema9":  round(e9[-1],  6),
                        "ema21": round(e21[-1], 6),
                        "ema50": round(e50[-1], 6),
                        "trend": ema_trend,
                    },
                    "macd": {
                        "line":      ml,
                        "signal":    sig,
                        "histogram": hist,
                        "trend":     macd_trend,
                    },
                    "rsi": {
                        "value":  rsi_val,
                        "signal": rsi_sig,
                    },
                    "bollingerBands": {
                        "upper":    bb_up,
                        "middle":   bb_mid,
                        "lower":    bb_lo,
                        "position": bb_pos,
                    },
                    "sar": {
                        "value": sar_val,
                        "trend": sar_trend,
                    },
                },
                "supportResistance": {
                    "support":    sup,
                    "resistance": res,
                },
                "priceAction": {
                    "pattern":  pa_pattern,
                    "trend":    pa_trend,
                    "strength": strength,
                },
                "volume": {
                    "signal": vol_sig,
                    "ratio":  vol_ratio,
                },
                "marketDepth": {
                    "marketPressure": pressure,
                    "imbalance":      f"{imb:+.2f}%",
                    "bidAskRatio":    str(ratio),
                    "bids": {
                        "totalVolume": bid_vol,
                        "totalValue":  bid_val,
                        "largestWall": {
                            "price":    lbid[0],
                            "quantity": lbid[1],
                            "total":    lbid[0] * lbid[1],
                        },
                    },
                    "asks": {
                        "totalVolume": ask_vol,
                        "totalValue":  ask_val,
                        "largestWall": {
                            "price":    lask[0],
                            "quantity": lask[1],
                            "total":    lask[0] * lask[1],
                        },
                    },
                    "spread": {
                        "price":      round(spread_p, 6),
                        "percentage": f"{round(spread_p/price*100, 4)}%",
                    },
                },
                "whaleActivity": {
                    "detected":   bool(w_bids or w_asks),
                    "threshold":  whale_thresh,
                    "whale_bids": w_bids[:5],
                    "whale_asks": w_asks[:5],
                    "summary": {
                        "buyOrders":        len(w_bids),
                        "sellOrders":       len(w_asks),
                        "totalBuyVolume":   wb_vol,
                        "totalSellVolume":  wa_vol,
                        "sentiment":        w_sent,
                        "netVolume":        wb_vol - wa_vol,
                    },
                },
            },
        }

    except Exception as e:
        return {"success": False, "error": str(e)}


# ── Indian Proxy List ────────────────────────────────────────
INDIAN_PROXIES = [
    "103.93.193.141:58080",
    "217.217.249.160:8080",
    "203.192.217.6:8080",
    "160.19.41.61:80",
    "202.43.122.156:1111",
    "103.179.46.49:6789",
    "203.110.240.156:80",
    "103.199.215.43:6262",
    "103.80.224.33:83",
    "103.170.46.245:8080",
    "117.247.233.50:8080",
    "45.250.215.8:8080",
    "103.205.64.153:80",
    "103.147.98.122:8080",
    "45.248.27.145:8080",
    "103.136.82.252:83",
    "14.99.215.106:1111",
    "103.148.39.38:83",
]


def fetch_from_binance_proxy(symbol: str, interval: str = "1h") -> dict:
    """Try Binance Futures via Indian proxies."""
    import random
    proxies_to_try = random.sample(INDIAN_PROXIES, min(3, len(INDIAN_PROXIES)))
    for proxy in proxies_to_try:
        try:
            proxies = {
                "http":  f"http://{proxy}",
                "https": f"http://{proxy}",
            }
            # Quick test with ticker first
            sym = f"{symbol}USDT"
            test = requests.get(
                f"{BINANCE_DIRECT_BASE}/fapi/v1/ticker/24hr",
                params={"symbol": sym},
                proxies=proxies,
                timeout=6,
            ).json()
            if "lastPrice" not in test:
                continue
            # Proxy works — do full fetch
            result = fetch_from_binance_direct(symbol, interval, proxies=proxies)
            if result.get("success"):
                result["source"] = f"Binance Futures (Proxy)"
                return result
        except Exception:
            continue
    return {"success": False, "error": "All proxies failed"}


def fetch_crypto_data(symbol: str, interval: str = "1h") -> dict:
    """
    Step 1: Binance Futures direct (no proxy)
    Step 2: Binance Futures via Indian proxy
    Step 3: Vercel proxy fallback
    """
    # Step 1: Binance direct
    result = fetch_from_binance_direct(symbol, interval)
    if result.get("success"):
        return result

    # Step 2: Indian proxy
    result = fetch_from_binance_proxy(symbol, interval)
    if result.get("success"):
        return result

    # Step 3: Vercel fallback
    try:
        r = requests.get(
            f"{BINANCE_API_URL}?symbol={symbol}&interval={interval}&depth=50",
            timeout=15,
        )
        return r.json()
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_current_price(symbol: str) -> float:
    data = fetch_crypto_data(symbol)
    if data.get("success"):
        return data["data"]["price"]["current"]
    return 0.0



# ============================================================
# BYBIT API  (metals — Gold & Silver)
# ============================================================
def fetch_bybit_ticker(symbol: str) -> dict:
    """Return standardised ticker dict for a metal symbol."""
    if symbol not in METAL_BYBIT_MAP:
        return {"success": False, "error": "Not a metal symbol"}
    bybit_sym, category = METAL_BYBIT_MAP[symbol]
    try:
        url = f"{BYBIT_API_URL}/market/tickers?category={category}&symbol={bybit_sym}"
        r   = requests.get(url, timeout=15)
        d   = r.json()
        if d.get("retCode") == 0 and d["result"]["list"]:
            t = d["result"]["list"][0]
            return {
                "success":      True,
                "symbol":       symbol,
                "bybit_symbol": bybit_sym,
                "price":        float(t.get("lastPrice",      0)),
                "markPrice":    float(t.get("markPrice",      0)),
                "high":         float(t.get("highPrice24h",   0)),
                "low":          float(t.get("lowPrice24h",    0)),
                "volume":       float(t.get("volume24h",      0)),
                "turnover":     float(t.get("turnover24h",    0)),
                "change":       float(t.get("price24hPcnt",   0)) * 100,
                "funding":      float(t.get("fundingRate",    0)),
                "openInterest": float(t.get("openInterest",   0)),
            }
        return {"success": False, "error": d.get("retMsg", "Unknown")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def fetch_bybit_orderbook(symbol: str, limit: int = 50) -> dict:
    """Return bids/asks as [[price, qty], ...] lists."""
    if symbol not in METAL_BYBIT_MAP:
        return {"success": False, "error": "Not a metal symbol"}
    bybit_sym, category = METAL_BYBIT_MAP[symbol]
    try:
        url = f"{BYBIT_API_URL}/market/orderbook?category={category}&symbol={bybit_sym}&limit={limit}"
        r   = requests.get(url, timeout=15)
        d   = r.json()
        if d.get("retCode") == 0:
            res  = d["result"]
            bids = [[float(p), float(q)] for p, q in res.get("b", [])]
            asks = [[float(p), float(q)] for p, q in res.get("a", [])]
            return {"success": True, "bids": bids, "asks": asks}
        return {"success": False, "error": d.get("retMsg", "Unknown")}
    except Exception as e:
        return {"success": False, "error": str(e)}


def analyze_metal_whales(symbol: str, bids: list, asks: list, price: float) -> dict:
    """Detect whale-sized orders in the Bybit order book."""
    threshold   = METAL_WHALE_THRESHOLD.get(symbol, 100_000)
    whale_bids  = []
    whale_asks  = []

    for p, q in bids:
        val = p * q
        if val >= threshold:
            whale_bids.append({
                "price": p, "qty": q, "value": val,
                "dist_pct": round(abs(price - p) / price * 100, 2) if price else 0,
            })
    for p, q in asks:
        val = p * q
        if val >= threshold:
            whale_asks.append({
                "price": p, "qty": q, "value": val,
                "dist_pct": round(abs(p - price) / price * 100, 2) if price else 0,
            })

    whale_bids.sort(key=lambda x: x["value"], reverse=True)
    whale_asks.sort(key=lambda x: x["value"], reverse=True)

    total_bid = sum(p * q for p, q in bids)
    total_ask = sum(p * q for p, q in asks)
    ratio     = round(total_bid / total_ask, 2) if total_ask else 0
    net       = total_bid - total_ask

    sentiment = "BULLISH" if ratio > 1.3 else ("BEARISH" if ratio < 0.77 else "NEUTRAL")

    return {
        "detected":          bool(whale_bids or whale_asks),
        "threshold":         threshold,
        "whale_bids":        whale_bids[:5],
        "whale_asks":        whale_asks[:5],
        "total_buy_orders":  len(whale_bids),
        "total_sell_orders": len(whale_asks),
        "total_bid_vol":     total_bid,
        "total_ask_vol":     total_ask,
        "net_vol":           net,
        "ratio":             ratio,
        "sentiment":         sentiment,
    }


def get_metal_current_price(symbol: str) -> float:
    t = fetch_bybit_ticker(symbol)
    return t.get("price", 0.0) if t.get("success") else 0.0


# ============================================================
# MESSAGE FORMATTERS — METALS (Bybit)
# ============================================================
def format_metal_price_message(ticker: dict, symbol: str) -> str:
    name      = METAL_NAMES.get(symbol, symbol)
    emoji     = METAL_EMOJI.get(symbol, "💰")
    bybit_sym = ticker["bybit_symbol"]
    price     = ticker["price"]
    mark      = ticker["markPrice"]
    high      = ticker["high"]
    low       = ticker["low"]
    vol       = ticker["volume"]
    turnover  = ticker["turnover"]
    change    = ticker["change"]
    funding   = ticker["funding"]
    oi        = ticker["openInterest"]

    ce = "🟢" if change >= 0 else "🔴"
    ca = "▲"  if change >= 0 else "▼"
    ts = datetime.utcnow().strftime("%H:%M UTC")

    return f"""
{emoji} <b>{name} ({bybit_sym})</b>
━━━━━━━━━━━━━━━━━━━━

💵 Price:    <b>${format_price(price)}</b>
📍 Mark:     <code>${format_price(mark)}</code>
{ce} Change:  <b>{ca} {abs(change):.2f}%</b>
━━━━━━━━━━━━━━━━━━━━

📈 High:     <code>${format_price(high)}</code>
📉 Low:      <code>${format_price(low)}</code>
📊 Volume:   <code>{format_number(vol)}</code>
💰 Turnover: <code>${format_number(turnover)}</code>
📂 OI:       <code>{format_number(oi)}</code>
💸 Funding:  <code>{funding:.6f}</code>
━━━━━━━━━━━━━━━━━━━━

🕐 Updated: {ts}
📡 Bybit Futures
"""


def format_metal_whale_message(ticker: dict, whale: dict, symbol: str) -> str:
    name      = METAL_NAMES.get(symbol, symbol)
    emoji     = METAL_EMOJI.get(symbol, "💰")
    bybit_sym = ticker["bybit_symbol"]
    price     = ticker["price"]
    threshold = whale["threshold"]
    ts        = datetime.utcnow().strftime("%H:%M:%S UTC")

    if not whale["detected"]:
        return f"""
{emoji} <b>Whale Activity — {name} ({bybit_sym})</b>
━━━━━━━━━━━━━━━━━━━━━━━━

💵 <b>Price: ${format_price(price)}</b>
🎯 Threshold: ${format_number(threshold)}

━━━━━━━━━━━━━━━━━━━━━━━━

❌ <b>No whale activity detected</b>

No orders above ${format_number(threshold)} in the order book.

━━━━━━━━━━━━━━━━━━━━━━━━
🕐 {ts}
📡 Bybit Futures
"""

    sentiment = whale["sentiment"]
    ratio     = whale["ratio"]
    net_vol   = whale["net_vol"]
    buy_vol   = whale["total_bid_vol"]
    sell_vol  = whale["total_ask_vol"]

    se = "🟢🐋" if sentiment == "BULLISH" else ("🔴🐋" if sentiment == "BEARISH" else "🟡🐋")
    st = ("BULLISH (Whales Buying)" if sentiment == "BULLISH"
          else "BEARISH (Whales Selling)" if sentiment == "BEARISH"
          else "NEUTRAL")

    bid_lines = ""
    for i, o in enumerate(whale["whale_bids"][:3], 1):
        bid_lines += f"   {i}. 🟢 ${format_price(o['price'])}  qty:{o['qty']:.2f}  val:${format_number(o['value'])}  ({o['dist_pct']}% below)\n"

    ask_lines = ""
    for i, o in enumerate(whale["whale_asks"][:3], 1):
        ask_lines += f"   {i}. 🔴 ${format_price(o['price'])}  qty:{o['qty']:.2f}  val:${format_number(o['value'])}  ({o['dist_pct']}% above)\n"

    return f"""
{emoji} <b>Whale Activity — {name} ({bybit_sym})</b>
━━━━━━━━━━━━━━━━━━━━━━━━

💵 <b>Price: ${format_price(price)}</b>
🎯 Threshold: ${format_number(threshold)}

━━━ {se} WHALE SENTIMENT ━━━

<b>Sentiment:</b>    {st}
<b>Bid/Ask Ratio:</b> {ratio}

━━━ 📊 WHALE SUMMARY ━━━

🟢 <b>Buy Orders:</b>  {whale["total_buy_orders"]}
   └ Volume: <code>${format_number(buy_vol)}</code>

🔴 <b>Sell Orders:</b> {whale["total_sell_orders"]}
   └ Volume: <code>${format_number(sell_vol)}</code>

💰 <b>Net Volume:</b> <code>${format_number(abs(net_vol))}</code>
   └ {"🟢 Buyers dominating" if net_vol > 0 else "🔴 Sellers dominating"}

━━━ 🧱 TOP WHALE BIDS ━━━
{bid_lines.rstrip() if bid_lines else "   None above threshold"}

━━━ 🧱 TOP WHALE ASKS ━━━
{ask_lines.rstrip() if ask_lines else "   None above threshold"}

━━━━━━━━━━━━━━━━━━━━━━━━
🕐 {ts}
📡 Bybit Futures
"""


# ============================================================
# MESSAGE FORMATTERS — CRYPTO (Binance)
# ============================================================
def format_price_message(data: dict, symbol: str) -> str:
    d      = data["data"]
    price  = d["price"]["current"]
    change = d["stats24h"]["priceChangePercent"]
    high   = d["stats24h"]["high"]
    low    = d["stats24h"]["low"]
    volume = d["stats24h"]["volume"]

    ce = "🟢" if change >= 0 else "🔴"
    ca = "▲"  if change >= 0 else "▼"
    ts = datetime.utcnow().strftime("%H:%M UTC")
    em = "🪙" if symbol in ["BTC", "ETH"] else "💰"

    return f"""
{em} <b>{symbol}/USDT</b>
━━━━━━━━━━━━━━━━━━━━

💵 Price:  <b>${format_price(price)}</b>
{ce} Change: <b>{ca} {abs(change):.2f}%</b>
━━━━━━━━━━━━━━━━━━━━

📈 High:   <code>${format_price(high)}</code>
📉 Low:    <code>${format_price(low)}</code>
📊 Volume: <code>{format_number(volume)}</code>
━━━━━━━━━━━━━━━━━━━━

🕐 Updated: {ts}
🚀 Binance Futures
"""


def format_analysis_message(data: dict, symbol: str, interval: str) -> str:
    d     = data["data"]
    ind   = d["indicators"]
    sr    = d["supportResistance"]
    pa    = d["priceAction"]
    vol   = d["volume"]
    depth = d["marketDepth"]

    price   = d["price"]["current"]
    funding = d["price"]["lastFundingRate"]

    ema  = ind["ema"]
    macd = ind["macd"]
    rsi  = ind["rsi"]
    bb   = ind["bollingerBands"]
    sar  = ind["sar"]

    ema_emoji  = "🟢" if ema["trend"]  == "BULLISH" else "🔴"
    macd_emoji = "🟢" if macd["trend"] == "BULLISH" else "🔴"
    sar_emoji  = "🟢" if sar["trend"]  == "BULLISH" else "🔴"

    rsi_val = rsi["value"]
    if rsi_val > 70:
        rsi_emoji, rsi_status = "🔴", "Overbought"
    elif rsi_val < 30:
        rsi_emoji, rsi_status = "🟢", "Oversold"
    else:
        rsi_emoji, rsi_status = "🟡", "Neutral"

    bb_emoji  = ("🟢" if bb["position"] == "OVERSOLD"
                 else "🔴" if bb["position"] == "OVERBOUGHT"
                 else "🟡")
    vol_emoji = ("🟢" if vol["signal"] == "HIGH"
                 else "🔴" if vol["signal"] == "LOW"
                 else "🟡")

    if "BUY" in depth["marketPressure"]:
        depth_emoji = "🟢"
    elif "SELL" in depth["marketPressure"]:
        depth_emoji = "🔴"
    else:
        depth_emoji = "🟡"

    bullish_count = sum([
        ema["trend"]  == "BULLISH",
        macd["trend"] == "BULLISH",
        rsi_val < 50,
        sar["trend"]  == "BULLISH",
        bb["position"] == "OVERSOLD",
    ])
    if   bullish_count >= 4: overall = "🟢 STRONG BUY"
    elif bullish_count >= 3: overall = "🟢 BUY"
    elif bullish_count <= 1: overall = "🔴 STRONG SELL"
    elif bullish_count <= 2: overall = "🔴 SELL"
    else:                    overall = "🟡 NEUTRAL"

    supports    = sr.get("support",    [])[:3]
    resistances = sr.get("resistance", [])[:3]
    support_str    = " | ".join([f"${format_price(s)}" for s in supports])    or "N/A"
    resistance_str = " | ".join([f"${format_price(r)}" for r in resistances]) or "N/A"

    return f"""
📊 <b>{symbol}/USDT Analysis ({interval})</b>
━━━━━━━━━━━━━━━━━━━━━━━━

💵 <b>Price: ${format_price(price)}</b>
💰 Funding: {funding:.6f}

━━━ 📈 TREND INDICATORS ━━━

{ema_emoji} <b>EMA Trend:</b> {ema['trend']}
   ├ EMA 9:  <code>${format_price(ema['ema9'])}</code>
   ├ EMA 21: <code>${format_price(ema['ema21'])}</code>
   └ EMA 50: <code>${format_price(ema['ema50'])}</code>

{macd_emoji} <b>MACD:</b> {macd['trend']}
   ├ Line:   <code>{macd['line']:.4f}</code>
   ├ Signal: <code>{macd['signal']:.4f}</code>
   └ Hist:   <code>{macd['histogram']:.4f}</code>

{sar_emoji} <b>SAR:</b> {sar['trend']}
   └ Value:  <code>${format_price(sar['value'])}</code>

━━━ 📉 MOMENTUM ━━━

{rsi_emoji} <b>RSI:</b> {rsi_val:.1f} ({rsi_status})
   └ Signal: {rsi['signal']}

{bb_emoji} <b>Bollinger:</b> {bb['position']}
   ├ Upper:  <code>${format_price(bb['upper'])}</code>
   ├ Middle: <code>${format_price(bb['middle'])}</code>
   └ Lower:  <code>${format_price(bb['lower'])}</code>

━━━ 📊 VOLUME & DEPTH ━━━

{vol_emoji} <b>Volume:</b> {vol['signal']}
   └ Ratio: {vol['ratio']}x avg

{depth_emoji} <b>Market Pressure:</b> {depth['marketPressure']}
   └ Imbalance: {depth['imbalance']}

━━━ 🎯 SUPPORT / RESISTANCE ━━━

🟢 <b>Support:</b>
   └ {support_str}

🔴 <b>Resistance:</b>
   └ {resistance_str}

━━━ 📍 PRICE ACTION ━━━

📌 Pattern: <b>{pa['pattern']}</b>
📈 Trend:   {pa['trend']}
💪 Strength: {pa['strength']}%

━━━━━━━━━━━━━━━━━━━━━━━━

🎯 <b>OVERALL SIGNAL: {overall}</b>

━━━━━━━━━━━━━━━━━━━━━━━━
🕐 {datetime.utcnow().strftime("%H:%M:%S UTC")}
"""


def format_market_depth_message(data: dict, symbol: str) -> str:
    d     = data["data"]
    depth = d["marketDepth"]
    price = d["price"]["current"]

    if   "STRONG_BUY"  in depth["marketPressure"]: pressure_emoji, pressure_text = "🟢🟢", "STRONG BUYING"
    elif "BUY"         in depth["marketPressure"]: pressure_emoji, pressure_text = "🟢",   "BUYING"
    elif "STRONG_SELL" in depth["marketPressure"]: pressure_emoji, pressure_text = "🔴🔴", "STRONG SELLING"
    elif "SELL"        in depth["marketPressure"]: pressure_emoji, pressure_text = "🔴",   "SELLING"
    else:                                           pressure_emoji, pressure_text = "🟡",   "NEUTRAL"

    bids  = depth["bids"]
    asks  = depth["asks"]
    ratio = float(depth["bidAskRatio"])
    if ratio > 1:
        buy_pct  = min(int((ratio / (ratio + 1)) * 10), 10)
        sell_pct = 10 - buy_pct
    else:
        sell_pct = min(int((1 / (ratio + 1)) * 10), 10)
        buy_pct  = 10 - sell_pct

    ratio_bar = "🟢" * buy_pct + "🔴" * sell_pct

    return f"""
📊 <b>Market Depth — {symbol}/USDT</b>
━━━━━━━━━━━━━━━━━━━━━━━━

💵 <b>Price: ${format_price(price)}</b>

━━━ {pressure_emoji} MARKET PRESSURE ━━━

<b>Overall:</b>      {pressure_text}
<b>Bid/Ask Ratio:</b> {depth['bidAskRatio']}
<b>Imbalance:</b>    {depth['imbalance']}

{ratio_bar}
<code>Buyers            Sellers</code>

━━━ 🟢 BUY SIDE (BIDS) ━━━

💰 Total Volume: <code>{format_number(bids['totalVolume'])}</code>
💵 Total Value:  <code>${format_number(bids['totalValue'])}</code>

🧱 <b>Largest Buy Wall:</b>
   └ ${format_price(bids['largestWall']['price'])} ({format_number(bids['largestWall']['quantity'])} contracts)
   └ Value: <code>${format_number(bids['largestWall']['total'])}</code>

━━━ 🔴 SELL SIDE (ASKS) ━━━

💰 Total Volume: <code>{format_number(asks['totalVolume'])}</code>
💵 Total Value:  <code>${format_number(asks['totalValue'])}</code>

🧱 <b>Largest Sell Wall:</b>
   └ ${format_price(asks['largestWall']['price'])} ({format_number(asks['largestWall']['quantity'])} contracts)
   └ Value: <code>${format_number(asks['largestWall']['total'])}</code>

━━━ 📈 SPREAD ━━━

📏 Spread:   <code>${format_price(depth['spread']['price'])}</code>
📊 Spread %: <code>{depth['spread']['percentage']}</code>

━━━━━━━━━━━━━━━━━━━━━━━━
🕐 {datetime.utcnow().strftime("%H:%M:%S UTC")}
"""


def format_whale_activity_message(data: dict, symbol: str) -> str:
    d     = data["data"]
    whale = d.get("whaleActivity", {})
    price = d["price"]["current"]

    if not whale.get("detected", False):
        return f"""
🐋 <b>Whale Activity — {symbol}/USDT</b>
━━━━━━━━━━━━━━━━━━━━━━━━

💵 <b>Price: ${format_price(price)}</b>
🎯 Threshold: ${format_number(whale.get('threshold', 0))}

━━━━━━━━━━━━━━━━━━━━━━━━

❌ <b>No whale activity detected</b>

━━━━━━━━━━━━━━━━━━━━━━━━
🕐 {datetime.utcnow().strftime("%H:%M:%S UTC")}
"""

    summary = whale["summary"]
    walls   = whale["walls"]

    if   whale["sentiment"] == "BULLISH": se, st = "🟢🐋", "BULLISH (Whales Buying)"
    elif whale["sentiment"] == "BEARISH": se, st = "🔴🐋", "BEARISH (Whales Selling)"
    else:                                  se, st = "🟡🐋", "NEUTRAL"

    alert_text = f"\n⚠️ <b>{whale['alert']}</b>\n" if whale.get("alert") else ""

    top_orders_text = ""
    for i, order in enumerate(whale.get("topOrders", [])[:5], 1):
        side_emoji = "🟢 BUY" if order["side"] == "BUY" else "🔴 SELL"
        top_orders_text += (
            f"\n   {i}. {side_emoji}\n"
            f"      └ Price: ${format_price(order['price'])}\n"
            f"      └ Value: <code>${format_number(order['valueUSD'])}</code>\n"
            f"      └ Distance: {order['distanceFromPrice']}\n"
        )

    msg = f"""
🐋 <b>Whale Activity — {symbol}/USDT</b>
━━━━━━━━━━━━━━━━━━━━━━━━

💵 <b>Price: ${format_price(price)}</b>
🎯 Threshold: ${format_number(whale['threshold'])}
{alert_text}
━━━ {se} WHALE SENTIMENT ━━━

<b>Sentiment:</b> {st}
<b>Pressure:</b>  {whale['pressure']}

━━━ 📊 WHALE SUMMARY ━━━

🟢 <b>Buy Orders:</b>  {summary['buyOrders']}
   └ Volume: <code>${format_number(summary['totalBuyVolume'])}</code>

🔴 <b>Sell Orders:</b> {summary['sellOrders']}
   └ Volume: <code>${format_number(summary['totalSellVolume'])}</code>

💰 <b>Net Volume:</b> <code>${format_number(abs(summary['netVolume']))}</code>
   └ {'🟢 Buyers dominating' if summary['netVolume'] > 0 else '🔴 Sellers dominating'}

━━━ 🧱 WHALE WALLS ━━━
"""
    if walls.get("buyWall"):
        msg += (
            f"\n🟢 <b>Largest Buy Wall:</b>\n"
            f"   └ Price: ${format_price(walls['buyWall']['price'])}\n"
            f"   └ Value: <code>${format_number(walls['buyWall']['valueUSD'])}</code>\n"
        )
    if walls.get("sellWall"):
        msg += (
            f"\n🔴 <b>Largest Sell Wall:</b>\n"
            f"   └ Price: ${format_price(walls['sellWall']['price'])}\n"
            f"   └ Value: <code>${format_number(walls['sellWall']['valueUSD'])}</code>\n"
        )

    msg += f"\n━━━ 🔝 TOP WHALE ORDERS ━━━\n{top_orders_text}\n━━━━━━━━━━━━━━━━━━━━━━━━\n🕐 {datetime.utcnow().strftime('%H:%M:%S UTC')}\n"
    return msg


def format_alerts_message(alerts: list) -> str:
    if not alerts:
        return """
📋 <b>My Active Alerts</b>
━━━━━━━━━━━━━━━━━━━━

❌ No active alerts

Use <b>Set TP</b> or <b>Set SL</b> buttons to create alerts!
"""
    msg = "\n📋 <b>My Active Alerts</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, a in enumerate(alerts, 1):
        at  = "🎯 TP" if a["type"] == "TP" else "🛑 SL"
        sym = a["symbol"]
        pair = f"{sym}/USDT" if sym in SYMBOLS else f"{sym} (Bybit)"
        msg += (
            f"<b>{i}. {pair}</b>\n"
            f"   {at}: <code>${format_price(a['price'])}</code>\n"
            f"   Entry: <code>${format_price(a['entry'])}</code>\n\n"
        )
    msg += f"━━━━━━━━━━━━━━━━━━━━\n📊 Total: {len(alerts)} alerts"
    return msg


# ============================================================
# KEYBOARDS
# ============================================================
def get_main_keyboard(symbol: str = "BTC") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 15m",    callback_data=f"tf_{symbol}_15m"),
            InlineKeyboardButton("📊 1H",     callback_data=f"tf_{symbol}_1h"),
            InlineKeyboardButton("📊 4H",     callback_data=f"tf_{symbol}_4h"),
            InlineKeyboardButton("💵 Price",  callback_data=f"price_{symbol}"),
        ],
        [InlineKeyboardButton("🔬 Analyse",  callback_data=f"analyse_{symbol}_1h")],
        [
            InlineKeyboardButton("🎯 Set TP", callback_data=f"settp_{symbol}"),
            InlineKeyboardButton("🛑 Set SL", callback_data=f"setsl_{symbol}"),
        ],
        [
            InlineKeyboardButton("🐋 Whales", callback_data=f"whales_{symbol}"),
            InlineKeyboardButton("📊 Depth",  callback_data=f"depth_{symbol}"),
        ],
        [
            InlineKeyboardButton("📋 My Alerts", callback_data="myalerts"),
            InlineKeyboardButton("🔄 Refresh",   callback_data=f"refresh_{symbol}"),
        ],
        [
            InlineKeyboardButton("🔔 Whale Alerts", callback_data=f"whale_alert_{symbol}"),
            InlineKeyboardButton("⬇️ Back",          callback_data="collapse"),
        ],
    ])


def get_metal_keyboard(symbol: str) -> InlineKeyboardMarkup:
    """Keyboard shown when a metal (Gold/Silver) is selected."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("💵 Price",   callback_data=f"metal_{symbol}"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"metal_{symbol}"),
        ],
        [InlineKeyboardButton("🐋 Whales",     callback_data=f"metal_whale_{symbol}")],
        [
            InlineKeyboardButton("🎯 Set TP",  callback_data=f"metal_settp_{symbol}"),
            InlineKeyboardButton("🛑 Set SL",  callback_data=f"metal_setsl_{symbol}"),
        ],
        [InlineKeyboardButton("⬇️ Back",       callback_data="collapse")],
    ])


def get_symbol_keyboard() -> InlineKeyboardMarkup:
    """Full coin + metal selector keyboard."""
    keyboard = []
    row = []
    for symbol in SYMBOLS:
        em = "🪙" if symbol in ["BTC", "ETH"] else "💰"
        row.append(InlineKeyboardButton(f"{em} {symbol}", callback_data=f"symbol_{symbol}"))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    # Metals row at the bottom
    metals_row = []
    for ms in METAL_SYMBOLS:
        em   = METAL_EMOJI.get(ms, "💰")
        name = METAL_NAMES.get(ms, ms)
        metals_row.append(InlineKeyboardButton(f"{em} {name}", callback_data=f"metal_{ms}"))
    if metals_row:
        keyboard.append(metals_row)
    return InlineKeyboardMarkup(keyboard)


def get_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])


def get_back_keyboard(symbol: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Back",    callback_data=f"price_{symbol}")],
        [InlineKeyboardButton("🔄 Refresh", callback_data=f"whales_{symbol}")],
    ])


# ============================================================
# COMMAND HANDLERS
# ============================================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    welcome = """
🚀 <b>Crypto + Metals Futures Bot</b>
━━━━━━━━━━━━━━━━━━━━

<b>Crypto Commands (Binance Futures):</b>
/p [symbol]  — Price  (e.g. /p doge)
/a [symbol]  — Full Analysis
/w [symbol]  — Whale Activity
/d [symbol]  — Market Depth
/alerts      — My Active TP/SL Alerts

<b>Metals Commands (Bybit Futures):</b>
/p xau  — 🪙 Gold price
/p xag  — 🥈 Silver price
/w xau  — Gold whale activity
/w xag  — Silver whale activity

<b>Supported Coins:</b>
BTC · ETH · DOGE · LTC · BNB · SOL · XRP · AVAX
🪙 Gold (XAU) · 🥈 Silver (XAG)

━━━━━━━━━━━━━━━━━━━━

Select a coin or metal below 👇
"""
    await update.message.reply_text(welcome, parse_mode="HTML", reply_markup=get_symbol_keyboard())


async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else "BTC"

    # Metal handler
    if symbol in METAL_SYMBOLS:
        msg    = await update.message.reply_text("⏳ Fetching from Bybit...")
        ticker = fetch_bybit_ticker(symbol)
        if ticker.get("success"):
            await msg.edit_text(format_metal_price_message(ticker, symbol),
                                parse_mode="HTML", reply_markup=get_metal_keyboard(symbol))
        else:
            await msg.edit_text(f"❌ Bybit Error: {ticker.get('error', 'Unknown')}")
        return

    # Crypto handler
    if symbol not in SYMBOLS:
        all_syms = ", ".join(SYMBOLS) + ", " + ", ".join(METAL_SYMBOLS)
        await update.message.reply_text(f"❌ Symbol not supported.\nUse: {all_syms}")
        return

    msg  = await update.message.reply_text("⏳ Fetching data...")
    data = fetch_crypto_data(symbol)
    if data.get("success"):
        await msg.edit_text(format_price_message(data, symbol),
                            parse_mode="HTML", reply_markup=get_main_keyboard(symbol))
    else:
        await msg.edit_text(f"❌ Error: {data.get('error', 'Unknown error')}")


async def analyse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else "BTC"

    if symbol in METAL_SYMBOLS:
        await update.message.reply_text(
            "ℹ️ Technical analysis (EMA/MACD/RSI) is not available for metals.\n"
            f"Use /p {symbol.lower()} for price details or /w {symbol.lower()} for whale activity."
        )
        return

    if symbol not in SYMBOLS:
        await update.message.reply_text(f"❌ Symbol not supported. Use: {', '.join(SYMBOLS)}")
        return

    msg  = await update.message.reply_text("⏳ Analysing...")
    data = fetch_crypto_data(symbol, "1h")
    if data.get("success"):
        await msg.edit_text(format_analysis_message(data, symbol, "1H"),
                            parse_mode="HTML", reply_markup=get_main_keyboard(symbol))
    else:
        await msg.edit_text(f"❌ Error: {data.get('error', 'Unknown error')}")


async def whale_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else "BTC"

    # Metal whale handler
    if symbol in METAL_SYMBOLS:
        msg    = await update.message.reply_text("🐋 Detecting whale activity (Bybit)...")
        ticker = fetch_bybit_ticker(symbol)
        if not ticker.get("success"):
            await msg.edit_text(f"❌ Bybit Error: {ticker.get('error', 'Unknown')}")
            return
        ob = fetch_bybit_orderbook(symbol)
        if ob.get("success"):
            whale = analyze_metal_whales(symbol, ob["bids"], ob["asks"], ticker["price"])
        else:
            whale = {"detected": False, "threshold": METAL_WHALE_THRESHOLD.get(symbol, 100_000)}
        await msg.edit_text(format_metal_whale_message(ticker, whale, symbol),
                            parse_mode="HTML", reply_markup=get_metal_keyboard(symbol))
        return

    # Crypto whale handler
    if symbol not in SYMBOLS:
        await update.message.reply_text(f"❌ Symbol not supported. Use: {', '.join(SYMBOLS + METAL_SYMBOLS)}")
        return

    msg  = await update.message.reply_text("🐋 Detecting whale activity...")
    data = fetch_crypto_data(symbol)
    if data.get("success"):
        await msg.edit_text(format_whale_activity_message(data, symbol),
                            parse_mode="HTML", reply_markup=get_back_keyboard(symbol))
    else:
        await msg.edit_text(f"❌ Error: {data.get('error', 'Unknown error')}")


async def depth_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    symbol = context.args[0].upper() if context.args else "BTC"

    if symbol in METAL_SYMBOLS:
        await update.message.reply_text(
            "ℹ️ Detailed market depth view is for crypto only.\n"
            f"Use /w {symbol.lower()} for Bybit order book whale analysis."
        )
        return

    if symbol not in SYMBOLS:
        await update.message.reply_text(f"❌ Symbol not supported. Use: {', '.join(SYMBOLS)}")
        return

    msg  = await update.message.reply_text("📊 Fetching market depth...")
    data = fetch_crypto_data(symbol)
    if data.get("success"):
        await msg.edit_text(format_market_depth_message(data, symbol),
                            parse_mode="HTML", reply_markup=get_back_keyboard(symbol))
    else:
        await msg.edit_text(f"❌ Error: {data.get('error', 'Unknown error')}")


async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await update.message.reply_text(
        format_alerts_message(user_alerts.get(user_id, [])),
        parse_mode="HTML",
    )


# ============================================================
# BUTTON CALLBACK HANDLER
# ============================================================
async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    await query.answer()
    data    = query.data
    user_id = update.effective_user.id

    # ── CRYPTO SYMBOL SELECTED ──────────────────────────────
    if data.startswith("symbol_"):
        symbol   = data.split("_")[1]
        api_data = fetch_crypto_data(symbol)
        if api_data.get("success"):
            await query.edit_message_text(
                format_price_message(api_data, symbol),
                parse_mode="HTML", reply_markup=get_main_keyboard(symbol))
        else:
            await query.edit_message_text(f"❌ Error: {api_data.get('error')}")

    # ── METAL: PRICE / REFRESH ───────────────────────────────
    elif data.startswith("metal_") and not data.startswith("metal_whale_") \
         and not data.startswith("metal_settp_") and not data.startswith("metal_setsl_"):
        symbol = data.split("_")[1]
        ticker = fetch_bybit_ticker(symbol)
        if ticker.get("success"):
            await query.edit_message_text(
                format_metal_price_message(ticker, symbol),
                parse_mode="HTML", reply_markup=get_metal_keyboard(symbol))
        else:
            await query.edit_message_text(f"❌ Bybit Error: {ticker.get('error')}")

    # ── METAL: WHALE ─────────────────────────────────────────
    elif data.startswith("metal_whale_"):
        symbol = data.split("_")[2]
        ticker = fetch_bybit_ticker(symbol)
        if not ticker.get("success"):
            await query.edit_message_text(f"❌ Bybit Error: {ticker.get('error')}")
            return
        ob    = fetch_bybit_orderbook(symbol)
        whale = (analyze_metal_whales(symbol, ob["bids"], ob["asks"], ticker["price"])
                 if ob.get("success")
                 else {"detected": False, "threshold": METAL_WHALE_THRESHOLD.get(symbol, 100_000)})
        await query.edit_message_text(
            format_metal_whale_message(ticker, whale, symbol),
            parse_mode="HTML", reply_markup=get_metal_keyboard(symbol))

    # ── METAL: SET TP ────────────────────────────────────────
    elif data.startswith("metal_settp_"):
        symbol = data.split("_")[2]
        cp     = get_metal_current_price(symbol)
        context.user_data["pending_alert"] = {"symbol": symbol, "type": "TP", "entry": cp}
        name   = METAL_NAMES.get(symbol, symbol)
        await query.edit_message_text(
            f"🎯 <b>Set Take Profit — {name}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Current Price: <code>${format_price(cp)}</code>\n\n"
            f"📝 Enter your TP price:",
            parse_mode="HTML", reply_markup=get_cancel_keyboard())

    # ── METAL: SET SL ────────────────────────────────────────
    elif data.startswith("metal_setsl_"):
        symbol = data.split("_")[2]
        cp     = get_metal_current_price(symbol)
        context.user_data["pending_alert"] = {"symbol": symbol, "type": "SL", "entry": cp}
        name   = METAL_NAMES.get(symbol, symbol)
        await query.edit_message_text(
            f"🛑 <b>Set Stop Loss — {name}</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Current Price: <code>${format_price(cp)}</code>\n\n"
            f"📝 Enter your SL price:",
            parse_mode="HTML", reply_markup=get_cancel_keyboard())

    # ── TIMEFRAME ─────────────────────────────────────────────
    elif data.startswith("tf_"):
        parts    = data.split("_")
        symbol   = parts[1]
        interval = parts[2]
        api_data = fetch_crypto_data(symbol, interval)
        if api_data.get("success"):
            await query.edit_message_text(
                format_analysis_message(api_data, symbol, interval.upper()),
                parse_mode="HTML", reply_markup=get_main_keyboard(symbol))

    # ── PRICE / REFRESH ───────────────────────────────────────
    elif data.startswith("price_") or data.startswith("refresh_"):
        symbol   = data.split("_")[1]
        api_data = fetch_crypto_data(symbol)
        if api_data.get("success"):
            await query.edit_message_text(
                format_price_message(api_data, symbol),
                parse_mode="HTML", reply_markup=get_main_keyboard(symbol))

    # ── ANALYSE ───────────────────────────────────────────────
    elif data.startswith("analyse_"):
        parts    = data.split("_")
        symbol   = parts[1]
        interval = parts[2] if len(parts) > 2 else "1h"
        api_data = fetch_crypto_data(symbol, interval)
        if api_data.get("success"):
            await query.edit_message_text(
                format_analysis_message(api_data, symbol, interval.upper()),
                parse_mode="HTML", reply_markup=get_main_keyboard(symbol))

    # ── WHALE (CRYPTO) ────────────────────────────────────────
    elif data.startswith("whales_"):
        symbol   = data.split("_")[1]
        api_data = fetch_crypto_data(symbol)
        if api_data.get("success"):
            await query.edit_message_text(
                format_whale_activity_message(api_data, symbol),
                parse_mode="HTML", reply_markup=get_back_keyboard(symbol))

    # ── MARKET DEPTH ──────────────────────────────────────────
    elif data.startswith("depth_"):
        symbol   = data.split("_")[1]
        api_data = fetch_crypto_data(symbol)
        if api_data.get("success"):
            await query.edit_message_text(
                format_market_depth_message(api_data, symbol),
                parse_mode="HTML", reply_markup=get_back_keyboard(symbol))

    # ── WHALE ALERT SUBSCRIPTION ──────────────────────────────
    elif data.startswith("whale_alert_"):
        symbol = data.split("_")[2]
        if user_id not in whale_subscriptions:
            whale_subscriptions[user_id] = []
        if symbol in whale_subscriptions[user_id]:
            whale_subscriptions[user_id].remove(symbol)
            await query.answer(f"🔕 Whale alerts OFF for {symbol}", show_alert=True)
        else:
            whale_subscriptions[user_id].append(symbol)
            await query.answer(f"🔔 Whale alerts ON for {symbol}", show_alert=True)

    # ── SET TP (CRYPTO) ───────────────────────────────────────
    elif data.startswith("settp_"):
        symbol = data.split("_")[1]
        cp     = get_current_price(symbol)
        context.user_data["pending_alert"] = {"symbol": symbol, "type": "TP", "entry": cp}
        await query.edit_message_text(
            f"🎯 <b>Set Take Profit — {symbol}/USDT</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Current Price: <code>${format_price(cp)}</code>\n\n"
            f"📝 Enter your TP price:",
            parse_mode="HTML", reply_markup=get_cancel_keyboard())

    # ── SET SL (CRYPTO) ───────────────────────────────────────
    elif data.startswith("setsl_"):
        symbol = data.split("_")[1]
        cp     = get_current_price(symbol)
        context.user_data["pending_alert"] = {"symbol": symbol, "type": "SL", "entry": cp}
        await query.edit_message_text(
            f"🛑 <b>Set Stop Loss — {symbol}/USDT</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
            f"Current Price: <code>${format_price(cp)}</code>\n\n"
            f"📝 Enter your SL price:",
            parse_mode="HTML", reply_markup=get_cancel_keyboard())

    # ── MY ALERTS ─────────────────────────────────────────────
    elif data == "myalerts":
        await query.edit_message_text(
            format_alerts_message(user_alerts.get(user_id, [])),
            parse_mode="HTML", reply_markup=get_symbol_keyboard())

    # ── CANCEL ────────────────────────────────────────────────
    elif data == "cancel":
        context.user_data.pop("pending_alert", None)
        await query.edit_message_text("❌ Cancelled. Select a coin:", reply_markup=get_symbol_keyboard())

    # ── COLLAPSE ──────────────────────────────────────────────
    elif data == "collapse":
        await query.edit_message_text("Select a coin:", reply_markup=get_symbol_keyboard())


# ============================================================
# TEXT MESSAGE HANDLER  (TP/SL input + quick price lookup)
# ============================================================
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    pending = context.user_data.get("pending_alert")

    if not pending:
        sym = text.upper()
        # Quick price lookup by typing symbol
        if sym in METAL_SYMBOLS:
            ticker = fetch_bybit_ticker(sym)
            if ticker.get("success"):
                await update.message.reply_text(
                    format_metal_price_message(ticker, sym),
                    parse_mode="HTML", reply_markup=get_metal_keyboard(sym))
        elif sym in SYMBOLS:
            data = fetch_crypto_data(sym)
            if data.get("success"):
                await update.message.reply_text(
                    format_price_message(data, sym),
                    parse_mode="HTML", reply_markup=get_main_keyboard(sym))
        return

    # Parse TP/SL price input
    try:
        price = float(text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Invalid price. Please enter a number (e.g. 0.097).")
        return

    alert = {
        "symbol": pending["symbol"],
        "type":   pending["type"],
        "price":  price,
        "entry":  pending["entry"],
    }
    if user_id not in user_alerts:
        user_alerts[user_id] = []
    user_alerts[user_id].append(alert)
    context.user_data.pop("pending_alert", None)

    alert_type = "Take Profit 🎯" if alert["type"] == "TP" else "Stop Loss 🛑"
    direction  = "above" if alert["type"] == "TP" else "below"
    sym        = alert["symbol"]
    pair       = f"{sym}/USDT" if sym in SYMBOLS else METAL_NAMES.get(sym, sym)

    await update.message.reply_text(
        f"✅ <b>Alert Created!</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📌 Symbol: <b>{pair}</b>\n"
        f"📍 Type:   <b>{alert_type}</b>\n"
        f"💰 Entry:  <code>${format_price(alert['entry'])}</code>\n"
        f"🎯 Target: <code>${format_price(alert['price'])}</code>\n\n"
        f"I'll notify you when price goes {direction} ${format_price(price)}\n\nGood luck! 🍀",
        parse_mode="HTML", reply_markup=get_symbol_keyboard())


# ============================================================
# BACKGROUND JOBS
# ============================================================
async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Check TP/SL alerts every 30 seconds. Handles both crypto & metals."""
    for user_id, alerts in list(user_alerts.items()):
        to_remove = []
        for i, alert in enumerate(alerts):
            sym = alert["symbol"]
            # Get current price from correct source
            if sym in METAL_SYMBOLS:
                current_price = get_metal_current_price(sym)
            else:
                current_price = get_current_price(sym)

            if current_price == 0:
                continue

            is_tp = alert["type"] == "TP"
            hit   = (is_tp and current_price >= alert["price"]) or \
                    (not is_tp and current_price <= alert["price"])

            if not hit:
                continue

            to_remove.append(i)
            pair = f"{sym}/USDT" if sym in SYMBOLS else METAL_NAMES.get(sym, sym)

            if is_tp:
                profit_pct = ((alert["price"] - alert["entry"]) / alert["entry"]) * 100
                message    = (
                    f"🎉🎉🎉 <b>TAKE PROFIT HIT!</b> 🎉🎉🎉\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"🏆 <b>CONGRATULATIONS!</b> 🏆\n\n"
                    f"📌 Symbol:  <b>{pair}</b>\n"
                    f"💰 Entry:   <code>${format_price(alert['entry'])}</code>\n"
                    f"🎯 TP:      <code>${format_price(alert['price'])}</code>\n"
                    f"📈 Current: <code>${format_price(current_price)}</code>\n\n"
                    f"💵 Profit: <b>+{profit_pct:.2f}%</b>\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"👏 Great prediction! You're on fire! 🔥\nKeep up the good work! 💪"
                )
            else:
                loss_pct = ((alert["entry"] - alert["price"]) / alert["entry"]) * 100
                quote    = random.choice(MOTIVATIONAL_QUOTES)
                message  = (
                    f"😢 <b>STOP LOSS HIT</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"📌 Symbol:  <b>{pair}</b>\n"
                    f"💰 Entry:   <code>${format_price(alert['entry'])}</code>\n"
                    f"🛑 SL:      <code>${format_price(alert['price'])}</code>\n"
                    f"📉 Current: <code>${format_price(current_price)}</code>\n\n"
                    f"📊 Loss: <b>-{loss_pct:.2f}%</b>\n\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"💪 <i>\"{quote}\"</i>\n\nDon't give up! Analyse what went wrong and come back stronger! 📈"
                )
            try:
                await context.bot.send_message(chat_id=user_id, text=message, parse_mode="HTML")
            except Exception as e:
                print(f"[check_alerts] Error sending to {user_id}: {e}")

        for i in sorted(to_remove, reverse=True):
            user_alerts[user_id].pop(i)


async def check_whale_activity(context: ContextTypes.DEFAULT_TYPE):
    """Auto whale alert for subscribed symbols (crypto only for now)."""
    for user_id, symbols in list(whale_subscriptions.items()):
        for symbol in symbols:
            if symbol in METAL_SYMBOLS:
                continue  # metals whale subscription not implemented in background job
            try:
                data = fetch_crypto_data(symbol)
                if not data.get("success"):
                    continue

                whale = data["data"].get("whaleActivity", {})
                if not whale.get("detected"):
                    continue

                last_key     = f"{user_id}_{symbol}"
                last_summary = last_whale_cache.get(last_key, {})
                curr_summary = whale.get("summary", {})

                buy_change  = curr_summary.get("buyOrders",  0) - last_summary.get("buyOrders",  0)
                sell_change = curr_summary.get("sellOrders", 0) - last_summary.get("sellOrders", 0)

                if abs(buy_change) < 2 and abs(sell_change) < 2:
                    last_whale_cache[last_key] = curr_summary
                    continue

                price     = data["data"]["price"]["current"]
                direction = "🟢 BUYING" if buy_change > sell_change else "🔴 SELLING"
                emoji     = "🐋📈"      if buy_change > sell_change else "🐋📉"

                message = (
                    f"{emoji} <b>WHALE ALERT — {symbol}/USDT</b>\n━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"💵 Price: <code>${format_price(price)}</code>\n\n"
                    f"🐋 <b>Whales are {direction}!</b>\n\n"
                    f"📊 Buy Orders:  {curr_summary.get('buyOrders',0)} ({'+' if buy_change>=0 else ''}{buy_change})\n"
                    f"📊 Sell Orders: {curr_summary.get('sellOrders',0)} ({'+' if sell_change>=0 else ''}{sell_change})\n\n"
                    f"💰 Buy Vol:  ${format_number(curr_summary.get('totalBuyVolume',0))}\n"
                    f"💰 Sell Vol: ${format_number(curr_summary.get('totalSellVolume',0))}\n\n"
                    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
                    f"🕐 {datetime.utcnow().strftime('%H:%M:%S UTC')}\n\n"
                    f"Use /w {symbol.lower()} for details"
                )
                try:
                    await context.bot.send_message(chat_id=user_id, text=message, parse_mode="HTML")
                except Exception as e:
                    print(f"[whale_job] Error sending to {user_id}: {e}")

                last_whale_cache[last_key] = curr_summary

            except Exception as e:
                print(f"[whale_job] Error for {symbol}: {e}")


# ============================================================
# MAIN
# ============================================================
def main():
    print("🚀 Starting Crypto + Metals Futures Bot...")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("p",       price_command))
    app.add_handler(CommandHandler("pp",      price_command))
    app.add_handler(CommandHandler("a",       analyse_command))
    app.add_handler(CommandHandler("analyse", analyse_command))
    app.add_handler(CommandHandler("w",       whale_command))
    app.add_handler(CommandHandler("whale",   whale_command))
    app.add_handler(CommandHandler("d",       depth_command))
    app.add_handler(CommandHandler("depth",   depth_command))
    app.add_handler(CommandHandler("alerts",  alerts_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Background jobs
    app.job_queue.run_repeating(check_alerts,         interval=30, first=10)
    app.job_queue.run_repeating(check_whale_activity, interval=60, first=30)

    print("✅ Bot is running!")
    print("")
    print("  Crypto  : /p btc | /p doge | /a ltc | /w eth | /d sol")
    print("  Metals  : /p xau | /p xag  | /w xau | /w xag")
    print("  Alerts  : /alerts")
    print("")

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
