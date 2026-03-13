"""
╔══════════════════════════════════════════════════════════╗
║     CRYPTO ANALYSIS BOT — CoinGecko API Version         ║
║     With TP/SL Alerts + Motivational Notifications!     ║
╚══════════════════════════════════════════════════════════╝

requirements.txt:
    python-telegram-bot[job-queue]==21.3
    requests==2.31.0
    flask==3.0.0
    apscheduler==3.10.4

.python-version:
    3.11

Secrets needed:
    BOT_TOKEN = your telegram bot token
"""

import os
import time
import random
import logging
import threading
import requests
from datetime import datetime, timezone
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ════════════════════════════════════════════════════════
#                        CONFIG
# ════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT      = int(os.environ.get("PORT", 8080))

COINGECKO_BASE = "https://api.coingecko.com/api/v3"
METALS_BASE    = "https://api.metals.live/v1"

WHALE_THRESHOLD = 500_000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════
#           MOTIVATIONAL LINES (Random Select)
# ════════════════════════════════════════════════════════
TP_MOTIVATIONS = [
    "🌟 Markets reward the patient — aap ne sahi waqt ka intezaar kiya!",
    "💡 Har winning trade ek seekh hai — isse aur bada karo!",
    "🔥 Yahi toh trading hai — analysis + patience = profit!",
    "🚀 Aaj ka profit kal ki investment hai — smart raho!",
    "💎 Diamonds are made under pressure — aur aap ne prove kar diya!",
    "🎯 Proper target set karna hi asli skill hai — well done!",
    "⚡ Ek trade ek step — keep climbing to the top!",
    "🏆 Winners don't quit — aur aap ne prove kar diya!",
]

SL_MOTIVATIONS = [
    "💪 Loss ek tuition fee hai — market ne sikhaya, agli baar jeeto!",
    "🌱 Har girawat ek naya mauka bhi laati hai — ready raho!",
    "🧠 Smart traders stop loss lagate hain kyunki capital bachana sabse zaroori hai!",
    "🔄 Ek trade se game khatam nahi hota — plan karo aur wapas aao!",
    "⚓ Stop loss lagana weakness nahi, yeh discipline hai — proud raho!",
    "📚 Market ka lesson costly tha, par seekh free mili — use karo!",
    "🌅 Har raat ke baad subah hoti hai — kal ka market fresh start hai!",
    "🎯 Professionals apna capital protect karte hain — aap ne sahi kiya!",
]

# ════════════════════════════════════════════════════════
#                    FLASK KEEP-ALIVE
# ════════════════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def home(): return "Crypto Analysis Bot Running!", 200

@flask_app.route("/health")
def health(): return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ════════════════════════════════════════════════════════
#                   SUPPORTED COINS
# ════════════════════════════════════════════════════════
COINS = {
    "btc":  "bitcoin",
    "ltc":  "litecoin",
    "sol":  "solana",
    "doge": "dogecoin",
    "xau":  "METAL",
    "xag":  "METAL",
}

EMOJI = {
    "btc":  "₿",
    "ltc":  "Ł",
    "sol":  "◎",
    "doge": "🐕",
    "xau":  "🥇",
    "xag":  "🥈",
}

CRYPTO_COINS = ["btc", "ltc", "sol", "doge"]
METAL_COINS  = ["xau", "xag"]

# ════════════════════════════════════════════════════════
#         TP/SL ALERT STORAGE (In-Memory)
# ════════════════════════════════════════════════════════
# Structure: alerts[chat_id][coin] = {"tp": price_or_None, "sl": price_or_None}
alerts: dict = {}

def get_alert(chat_id, coin):
    return alerts.get(str(chat_id), {}).get(coin, {"tp": None, "sl": None})

def set_alert(chat_id, coin, tp=None, sl=None):
    cid = str(chat_id)
    if cid not in alerts:
        alerts[cid] = {}
    if coin not in alerts[cid]:
        alerts[cid][coin] = {"tp": None, "sl": None}
    if tp is not None:
        alerts[cid][coin]["tp"] = tp
    if sl is not None:
        alerts[cid][coin]["sl"] = sl

def clear_alert_tp(chat_id, coin):
    cid = str(chat_id)
    if cid in alerts and coin in alerts[cid]:
        alerts[cid][coin]["tp"] = None

def clear_alert_sl(chat_id, coin):
    cid = str(chat_id)
    if cid in alerts and coin in alerts[cid]:
        alerts[cid][coin]["sl"] = None

# ════════════════════════════════════════════════════════
#                     API FUNCTIONS
# ════════════════════════════════════════════════════════
def api_get(url, params=None):
    try:
        headers = {"accept": "application/json"}
        r = requests.get(url, params=params, headers=headers, timeout=10)
        return r.json()
    except Exception as e:
        logger.error(f"API error [{url}]: {e}")
        return None


def get_ticker(coin):
    """Get 24hr ticker data."""
    if coin in METAL_COINS:
        return get_metal_ticker(coin)

    cg_id = COINS[coin]
    data  = api_get(
        f"{COINGECKO_BASE}/coins/markets",
        params={
            "vs_currency": "usd",
            "ids": cg_id,
            "price_change_percentage": "24h",
        }
    )
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    d = data[0]
    try:
        return {
            "price":  float(d["current_price"]),
            "high":   float(d["high_24h"]),
            "low":    float(d["low_24h"]),
            "change": float(d["price_change_percentage_24h"]),
            "volume": float(d["total_volume"]),
            "time":   datetime.now(timezone.utc).strftime("%H:%M UTC"),
            "source": "CoinGecko",
        }
    except Exception as e:
        logger.error(f"Ticker parse error: {e}")
        return None


def get_metal_ticker(coin):
    """
    Get gold/silver price with 3 fallback sources:
      1. Yahoo Finance (GC=F / SI=F)  — most reliable, real-time
      2. metals.live                  — fallback
      3. CoinGecko PAXG/XAUT          — last resort (approx)
    """
    yahoo_symbol = "GC%3DF" if coin == "xau" else "SI%3DF"
    coingecko_id = "pax-gold" if coin == "xau" else "tether-gold"

    # ── Source 1: Yahoo Finance ───────────────────────
    try:
        url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={yahoo_symbol}"
        headers = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
        result = data["quoteResponse"]["result"]
        if result and len(result) > 0:
            q      = result[0]
            price  = float(q["regularMarketPrice"])
            high   = float(q.get("regularMarketDayHigh", price * 1.005))
            low    = float(q.get("regularMarketDayLow",  price * 0.995))
            change = float(q.get("regularMarketChangePercent", 0.0))
            volume = int(q.get("regularMarketVolume", 0))
            logger.info(f"[{coin.upper()}] Yahoo Finance OK: ${price}")
            return {
                "price":  price,
                "high":   high,
                "low":    low,
                "change": round(change, 2),
                "volume": volume,
                "time":   datetime.now(timezone.utc).strftime("%H:%M UTC"),
                "source": "Yahoo Finance",
            }
    except Exception as e:
        logger.warning(f"[{coin.upper()}] Yahoo Finance failed: {e}")

    # ── Source 2: metals.live ─────────────────────────
    try:
        r    = requests.get(f"{METALS_BASE}/latest",
                            headers={"accept": "application/json"}, timeout=10)
        data = r.json()
        if isinstance(data, list):
            metals = {}
            for item in data:
                if isinstance(item, dict):
                    if "metal" in item:
                        metals[item["metal"]] = item.get("price", 0)
                    else:
                        metals.update(item)
            key   = "gold" if coin == "xau" else "silver"
            price = float(metals.get(key, 0))
            if price > 0:
                logger.info(f"[{coin.upper()}] Metals.live OK: ${price}")
                return {
                    "price":  price,
                    "high":   price * 1.005,
                    "low":    price * 0.995,
                    "change": 0.0,
                    "volume": 0,
                    "time":   datetime.now(timezone.utc).strftime("%H:%M UTC"),
                    "source": "Metals.live",
                }
    except Exception as e:
        logger.warning(f"[{coin.upper()}] Metals.live failed: {e}")

    # ── Source 3: CoinGecko PAXG/XAUT (approx) ───────
    try:
        data = api_get(
            f"{COINGECKO_BASE}/coins/markets",
            params={"vs_currency": "usd", "ids": coingecko_id},
        )
        if data and isinstance(data, list) and len(data) > 0:
            d     = data[0]
            price = float(d["current_price"])
            logger.info(f"[{coin.upper()}] CoinGecko fallback OK: ${price}")
            return {
                "price":  price,
                "high":   float(d.get("high_24h", price * 1.005)),
                "low":    float(d.get("low_24h",  price * 0.995)),
                "change": float(d.get("price_change_percentage_24h", 0.0)),
                "volume": float(d.get("total_volume", 0)),
                "time":   datetime.now(timezone.utc).strftime("%H:%M UTC"),
                "source": f"CoinGecko (~{coin.upper()})",
            }
    except Exception as e:
        logger.warning(f"[{coin.upper()}] CoinGecko fallback failed: {e}")

    logger.error(f"[{coin.upper()}] ALL 3 sources failed!")
    return None


def get_klines(coin, interval, limit=100):
    """Get OHLC candle data from CoinGecko."""
    if coin in METAL_COINS:
        return []

    cg_id   = COINS[coin]
    days_map = {"15m": 1, "1h": 7, "4h": 30}
    days     = days_map.get(interval, 7)

    data = api_get(
        f"{COINGECKO_BASE}/coins/{cg_id}/ohlc",
        params={"vs_currency": "usd", "days": days}
    )
    if not data or not isinstance(data, list):
        return []
    try:
        candles = [
            {
                "open":  float(k[1]),
                "high":  float(k[2]),
                "low":   float(k[3]),
                "close": float(k[4]),
                "vol":   0.0,
            }
            for k in data
        ]
        return candles[-limit:]
    except:
        return []

# ════════════════════════════════════════════════════════
#                  TECHNICAL ANALYSIS
# ════════════════════════════════════════════════════════
def calc_rsi(candles, period=14):
    if len(candles) < period + 1:
        return 50
    closes = [c["close"] for c in candles]
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    return round(100 - (100 / (1 + avg_gain / avg_loss)), 2)


def calc_macd(candles):
    if len(candles) < 26:
        return None, None, None
    closes = [c["close"] for c in candles]

    def ema(data, period):
        k = 2 / (period + 1)
        result = [data[0]]
        for v in data[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema12       = ema(closes, 12)
    ema26       = ema(closes, 26)
    macd_line   = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = ema(macd_line, 9)
    histogram   = macd_line[-1] - signal_line[-1]
    return round(macd_line[-1], 6), round(signal_line[-1], 6), round(histogram, 6)


def calc_sar(candles):
    if len(candles) < 5:
        return None, True
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    af, max_af = 0.02, 0.2
    bull = closes[1] > closes[0]
    ep   = highs[0] if bull else lows[0]
    sar  = lows[0]  if bull else highs[0]
    for i in range(2, len(candles)):
        prev_sar = sar
        if bull:
            sar = prev_sar + af * (ep - prev_sar)
            sar = min(sar, lows[i - 1], lows[i - 2])
            if closes[i] > ep:
                ep = closes[i]
                af = min(af + 0.02, max_af)
            if closes[i] < sar:
                bull = False; sar = ep; ep = closes[i]; af = 0.02
        else:
            sar = prev_sar + af * (ep - sar)
            sar = max(sar, highs[i - 1], highs[i - 2])
            if closes[i] < ep:
                ep = closes[i]
                af = min(af + 0.02, max_af)
            if closes[i] > sar:
                bull = True; sar = ep; ep = closes[i]; af = 0.02
    return round(sar, 8), bull


def calc_sr(candles):
    if len(candles) < 10:
        return [], []
    highs   = [c["high"]  for c in candles]
    lows    = [c["low"]   for c in candles]
    current = candles[-1]["close"]
    swing_highs, swing_lows = [], []
    for i in range(2, len(candles) - 2):
        if highs[i] == max(highs[i - 2:i + 3]):
            swing_highs.append(highs[i])
        if lows[i] == min(lows[i - 2:i + 3]):
            swing_lows.append(lows[i])

    def cluster(levels, tol=0.005):
        levels = sorted(set(round(l, 8) for l in levels))
        if not levels: return []
        clusters = []; group = [levels[0]]
        for l in levels[1:]:
            if abs(l - group[-1]) / group[-1] < tol: group.append(l)
            else: clusters.append(group); group = [l]
        if group: clusters.append(group)
        return [round(sum(g) / len(g), 8) for g in clusters]

    resistance = sorted([l for l in cluster(swing_highs) if l > current])[-3:]
    support    = sorted([l for l in cluster(swing_lows)  if l < current], reverse=True)[:3]
    return resistance, support


def vol_trend(candles):
    if len(candles) < 10:
        return "➡️ Stable"
    recent = sum(c["vol"] for c in candles[-5:]) / 5
    older  = sum(c["vol"] for c in candles[-10:-5]) / 5
    if older == 0: return "➡️ Stable"
    if recent > older * 1.2: return "📈 Increasing"
    if recent < older * 0.8: return "📉 Decreasing"
    return "➡️ Stable"


def price_struct(candles):
    if len(candles) < 6:
        return "↔️ Ranging"
    h = [c["high"] for c in candles[-6:]]
    l = [c["low"]  for c in candles[-6:]]
    if h[-1] > h[-3] > h[-5] and l[-1] > l[-3] > l[-5]:
        return "📈 Higher Highs / Higher Lows"
    if h[-1] < h[-3] < h[-5] and l[-1] < l[-3] < l[-5]:
        return "📉 Lower Highs / Lower Lows"
    return "↔️ Ranging"

# ════════════════════════════════════════════════════════
#                   FORMAT HELPERS
# ════════════════════════════════════════════════════════
def fmt(price):
    if price >= 10000: return f"${price:,.2f}"
    if price >= 100:   return f"${price:.2f}"
    if price >= 1:     return f"${price:.4f}"
    return f"${price:.6f}"

# ════════════════════════════════════════════════════════
#                     KEYBOARDS
# ════════════════════════════════════════════════════════
def main_kb(coin):
    alert = get_alert("_", coin)  # default check (no chat_id here — used in display only)
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 15m",   callback_data=f"tf_{coin}_15m"),
            InlineKeyboardButton("📊 1H",    callback_data=f"tf_{coin}_1h"),
            InlineKeyboardButton("📊 4H",    callback_data=f"tf_{coin}_4h"),
            InlineKeyboardButton("💵 Price", callback_data=f"price_{coin}"),
        ],
        [
            InlineKeyboardButton("🔬 Analyse", callback_data=f"analyse_{coin}"),
        ],
        [
            InlineKeyboardButton("🎯 Set TP",  callback_data=f"settp_{coin}"),
            InlineKeyboardButton("🔴 Set SL",  callback_data=f"setsl_{coin}"),
        ],
        [
            InlineKeyboardButton("📋 My Alerts", callback_data=f"alerts_{coin}"),
            InlineKeyboardButton("🔄 Refresh",   callback_data=f"refresh_{coin}"),
        ],
    ])


def alert_kb(coin):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🎯 Set TP",   callback_data=f"settp_{coin}"),
            InlineKeyboardButton("🔴 Set SL",   callback_data=f"setsl_{coin}"),
        ],
        [
            InlineKeyboardButton("❌ Clear TP", callback_data=f"cleartp_{coin}"),
            InlineKeyboardButton("❌ Clear SL", callback_data=f"clearsl_{coin}"),
        ],
        [
            InlineKeyboardButton("🔙 Back",     callback_data=f"price_{coin}"),
        ],
    ])

# ════════════════════════════════════════════════════════
#                   MESSAGE BUILDERS
# ════════════════════════════════════════════════════════
def build_dashboard(coin, d):
    e     = EMOJI.get(coin, "💰")
    arrow = "🔼" if d["change"] >= 0 else "🔽"
    vol_str = f"{d['volume']:,.0f}" if d["volume"] > 0 else "N/A"
    return (
        f"{e} *{coin.upper()}/USDT*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 Price:  *{fmt(d['price'])}*\n"
        f"{arrow} Change: *{d['change']:+.2f}%*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 High:   {fmt(d['high'])}\n"
        f"📉 Low:    {fmt(d['low'])}\n"
        f"📊 Volume: {vol_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Updated: {d['time']}\n"
        f"🚀 {d['source']}"
    )


def build_alerts_msg(chat_id, coin):
    a = get_alert(chat_id, coin)
    e = EMOJI.get(coin, "💰")
    tp_str = fmt(a["tp"]) if a["tp"] else "❌ Set nahi hai"
    sl_str = fmt(a["sl"]) if a["sl"] else "❌ Set nahi hai"
    d = get_ticker(coin)
    price_str = fmt(d["price"]) if d else "N/A"
    return (
        f"{e} *{coin.upper()} — Aapke Alerts*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 Current Price: *{price_str}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Take Profit: *{tp_str}*\n"
        f"🔴 Stop Loss:   *{sl_str}*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Bot har 10 sec mein check karta hai\n"
        f"📲 Price hit hone par notification aayega!"
    )


def build_sr(coin, tf, candles):
    if not candles:
        return f"📊 *{coin.upper()}/USDT ({tf})*\n\n⚠️ Data available nahi hai!\nCoinGecko OHLC sirf crypto coins ke liye hai."
    res, sup = calc_sr(candles)
    current  = candles[-1]["close"]
    lines    = [f"📊 *{coin.upper()}/USDT ({tf})*\n━━━━━━━━━━━━━━━━━━", "🔴 *RESISTANCE*"]
    if res:
        for r in reversed(res):
            dist = ((r - current) / current) * 100
            lines.append(f"   {fmt(r)}  ↑{dist:.2f}%")
    else:
        lines.append("   No clear level")
    lines.append(f"\n💵 *NOW: {fmt(current)}*\n")
    lines.append("🟢 *SUPPORT*")
    if sup:
        for s in sup:
            dist = ((current - s) / current) * 100
            lines.append(f"   {fmt(s)}  ↓{dist:.2f}%")
    else:
        lines.append("   No clear level")
    lines.append("━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)


def build_analyse(coin, candles):
    if not candles:
        return f"🔬 *{coin.upper()}/USDT Analysis*\n\n⚠️ Candle data nahi mila!"
    rsi           = calc_rsi(candles)
    _, _, hist    = calc_macd(candles)
    sar_val, bull = calc_sar(candles)
    vt            = vol_trend(candles)
    ps            = price_struct(candles)
    current       = candles[-1]["close"]

    rsi_s  = "🔴 Overbought" if rsi >= 70 else ("🟢 Oversold" if rsi <= 30 else "🟡 Neutral")
    macd_s = ("📈 Bullish Crossover" if hist and hist > 0 else "📉 Bearish Crossover") if hist else "⚪ No Signal"
    sar_s  = "🟢 Buy (dots below)" if bull else "🔴 Sell (dots above)"

    score = sum([rsi <= 50, hist is not None and hist > 0, bull, "Increasing" in vt])
    conf  = round((score / 4) * 100)
    bias  = "🟢 Bullish" if conf >= 60 else ("🔴 Bearish" if conf <= 40 else "🟡 Neutral")

    res, sup = calc_sr(candles)
    breakout = ""
    if res and current >= res[0] * 0.999:
        breakout = f"\n\n⚡ *BREAKOUT ALERT!*\nResistance test: {fmt(res[0])}"
    elif sup and current <= sup[0] * 1.001:
        breakout = f"\n\n⚡ *BREAKDOWN ALERT!*\nSupport test: {fmt(sup[0])}"

    return (
        f"🔬 *{coin.upper()}/USDT Technical Analysis*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📐 *RSI:* {rsi}\n   {rsi_s}\n\n"
        f"📈 *MACD*\n   {macd_s}\n\n"
        f"🎯 *Parabolic SAR*\n   {sar_s}\n\n"
        f"📊 *Volume*\n   {vt}\n\n"
        f"🏗️ *Structure*\n   {ps}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🧠 *Bias:* {bias}\n"
        f"   Confidence: *{conf}%*"
        f"{breakout}"
    )

# ════════════════════════════════════════════════════════
#              TP/SL BACKGROUND MONITOR JOB
# ════════════════════════════════════════════════════════
async def check_tp_sl_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Runs every 60 seconds — checks all active TP/SL alerts."""
    if not alerts:
        return

    for chat_id, coins_data in list(alerts.items()):
        for coin, alert_data in list(coins_data.items()):
            tp = alert_data.get("tp")
            sl = alert_data.get("sl")
            if not tp and not sl:
                continue

            try:
                ticker = get_ticker(coin)
                if not ticker:
                    continue
                current_price = ticker["price"]
                e = EMOJI.get(coin, "💰")

                # ── TP HIT ──────────────────────────────
                if tp and current_price >= tp:
                    motivation = random.choice(TP_MOTIVATIONS)
                    msg = (
                        f"🎉🎉 *CONGRATULATIONS!* 🎉🎉\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"{e} *{coin.upper()} — TAKE PROFIT HIT!*\n\n"
                        f"🎯 Aapka TP: *{fmt(tp)}*\n"
                        f"💵 Current Price: *{fmt(current_price)}*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"👏 Bahut khoob! Profit book ho gaya!\n\n"
                        f"💬 _{motivation}_"
                    )
                    await context.bot.send_message(
                        chat_id=int(chat_id),
                        text=msg,
                        parse_mode="Markdown",
                    )
                    clear_alert_tp(chat_id, coin)
                    logger.info(f"TP hit for {coin} | chat_id={chat_id}")

                # ── SL HIT ──────────────────────────────
                elif sl and current_price <= sl:
                    motivation = random.choice(SL_MOTIVATIONS)
                    msg = (
                        f"😢 *Stop Loss Hit Ho Gaya...*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"{e} *{coin.upper()} — STOP LOSS TRIGGERED*\n\n"
                        f"🔴 Aapka SL: *{fmt(sl)}*\n"
                        f"💵 Current Price: *{fmt(current_price)}*\n"
                        f"━━━━━━━━━━━━━━━━━━\n"
                        f"🛡️ Capital protect ho gaya — sahi decision tha!\n\n"
                        f"💬 _{motivation}_"
                    )
                    await context.bot.send_message(
                        chat_id=int(chat_id),
                        text=msg,
                        parse_mode="Markdown",
                    )
                    clear_alert_sl(chat_id, coin)
                    logger.info(f"SL hit for {coin} | chat_id={chat_id}")

            except Exception as e:
                logger.error(f"Alert check error [{coin}|{chat_id}]: {e}")

# ════════════════════════════════════════════════════════
#                    BOT HANDLERS
# ════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *Crypto Analysis Bot*\n\n"
        "📌 *Commands:*\n"
        "`/pp btc`  — Bitcoin\n"
        "`/pp ltc`  — Litecoin\n"
        "`/pp sol`  — Solana\n"
        "`/pp doge` — Dogecoin\n"
        "`/pp xau`  — Gold 🥇\n"
        "`/pp xag`  — Silver 🥈\n\n"
        "`/alerts`  — Apne saare active alerts dekho\n\n"
        "📊 *Features:*\n"
        "• Live Price\n"
        "• Support & Resistance (15m/1H/4H)\n"
        "• RSI, MACD, SAR Analysis\n"
        "• 🎯 Take Profit Alert\n"
        "• 🔴 Stop Loss Alert\n"
        "• Market Bias & Confidence\n\n"
        "⚡ *Powered by CoinGecko API*\n\n"
        "💪 *Happy Trading!*",
        parse_mode="Markdown",
    )


async def cmd_pp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Example: `/pp ltc`", parse_mode="Markdown")
        return
    coin = context.args[0].lower()
    if coin not in COINS:
        await update.message.reply_text(
            f"❌ Supported: {', '.join(c.upper() for c in COINS)}",
            parse_mode="Markdown"
        )
        return
    msg = await update.message.reply_text("⏳ Fetching data...", parse_mode="Markdown")
    d   = get_ticker(coin)
    if not d:
        await msg.edit_text("❌ Data nahi mila. Thodi der baad try karo.")
        return
    await msg.edit_text(
        build_dashboard(coin, d),
        parse_mode="Markdown",
        reply_markup=main_kb(coin),
    )


async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all active alerts for this user."""
    chat_id = str(update.effective_chat.id)
    user_alerts = alerts.get(chat_id, {})

    if not user_alerts or all(
        not v.get("tp") and not v.get("sl") for v in user_alerts.values()
    ):
        await update.message.reply_text(
            "📋 *Koi active alerts nahi hain!*\n\n"
            "Kisi coin ke liye `/pp btc` type karo\n"
            "aur phir 🎯 Set TP ya 🔴 Set SL button dabao.",
            parse_mode="Markdown",
        )
        return

    lines = ["📋 *Aapke Active Alerts:*\n━━━━━━━━━━━━━━━━━━"]
    for coin, data in user_alerts.items():
        tp = data.get("tp")
        sl = data.get("sl")
        if not tp and not sl:
            continue
        e = EMOJI.get(coin, "💰")
        lines.append(f"\n{e} *{coin.upper()}*")
        if tp:
            lines.append(f"   🎯 TP: {fmt(tp)}")
        if sl:
            lines.append(f"   🔴 SL: {fmt(sl)}")
    lines.append("\n━━━━━━━━━━━━━━━━━━")
    lines.append("⚡ Har 10 sec mein check hota hai!")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q       = update.callback_query
    await q.answer()
    data    = q.data
    chat_id = str(q.message.chat_id)

    # ── Timeframe ─────────────────────────────────────
    if data.startswith("tf_"):
        parts    = data.split("_")
        coin, tf = parts[1], parts[2]
        candles  = get_klines(coin, tf, 100)
        await q.edit_message_text(
            build_sr(coin, tf.upper(), candles),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

    # ── Price ─────────────────────────────────────────
    elif data.startswith("price_"):
        coin = data[6:]
        d    = get_ticker(coin)
        if not d:
            await q.answer("❌ Data nahi mila!", show_alert=True)
            return
        await q.edit_message_text(
            build_dashboard(coin, d),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

    # ── Analyse ───────────────────────────────────────
    elif data.startswith("analyse_"):
        coin    = data[8:]
        candles = get_klines(coin, "1h", 100)
        await q.edit_message_text(
            build_analyse(coin, candles),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

    # ── Refresh ───────────────────────────────────────
    elif data.startswith("refresh_"):
        coin = data[8:]
        await q.answer("⏳ Fetching fresh data...")
        d = get_ticker(coin)
        if not d:
            await q.answer("❌ Data nahi mila!", show_alert=True)
            return
        try:
            await q.edit_message_text(
                build_dashboard(coin, d),
                parse_mode="Markdown",
                reply_markup=main_kb(coin),
            )
        except Exception:
            # If message content unchanged, just answer silently
            await q.answer("✅ Already up to date!", show_alert=False)

    # ── My Alerts ─────────────────────────────────────
    elif data.startswith("alerts_"):
        coin = data[7:]
        await q.edit_message_text(
            build_alerts_msg(chat_id, coin),
            parse_mode="Markdown",
            reply_markup=alert_kb(coin),
        )

    # ── Set TP ────────────────────────────────────────
    elif data.startswith("settp_"):
        coin = data[6:]
        d    = get_ticker(coin)
        price_hint = f"\n💵 Current price: *{fmt(d['price'])}*" if d else ""
        context.user_data["waiting_for"] = {"type": "tp", "coin": coin}
        await q.edit_message_text(
            f"🎯 *Take Profit Set Karo — {coin.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━━"
            f"{price_hint}\n\n"
            f"📝 Abhi apna TP price type karo:\n"
            f"_(Sirf number likhna hai, jaise: `85000`)_\n\n"
            f"❌ Cancel karne ke liye `/cancel` likhو",
            parse_mode="Markdown",
        )

    # ── Set SL ────────────────────────────────────────
    elif data.startswith("setsl_"):
        coin = data[6:]
        d    = get_ticker(coin)
        price_hint = f"\n💵 Current price: *{fmt(d['price'])}*" if d else ""
        context.user_data["waiting_for"] = {"type": "sl", "coin": coin}
        await q.edit_message_text(
            f"🔴 *Stop Loss Set Karo — {coin.upper()}*\n"
            f"━━━━━━━━━━━━━━━━━━"
            f"{price_hint}\n\n"
            f"📝 Abhi apna SL price type karo:\n"
            f"_(Sirf number likhna hai, jaise: `78000`)_\n\n"
            f"❌ Cancel karne ke liye `/cancel` likho",
            parse_mode="Markdown",
        )

    # ── Clear TP ──────────────────────────────────────
    elif data.startswith("cleartp_"):
        coin = data[8:]
        clear_alert_tp(chat_id, coin)
        await q.edit_message_text(
            build_alerts_msg(chat_id, coin),
            parse_mode="Markdown",
            reply_markup=alert_kb(coin),
        )
        await q.answer("✅ TP clear kar diya!", show_alert=False)

    # ── Clear SL ──────────────────────────────────────
    elif data.startswith("clearsl_"):
        coin = data[8:]
        clear_alert_sl(chat_id, coin)
        await q.edit_message_text(
            build_alerts_msg(chat_id, coin),
            parse_mode="Markdown",
            reply_markup=alert_kb(coin),
        )
        await q.answer("✅ SL clear kar diya!", show_alert=False)


async def handle_price_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text input when user is setting TP or SL price."""
    waiting = context.user_data.get("waiting_for")
    if not waiting:
        return  # Not waiting for input — ignore

    text    = update.message.text.strip().replace(",", "")
    chat_id = str(update.effective_chat.id)
    coin    = waiting["coin"]
    kind    = waiting["type"]  # "tp" or "sl"

    try:
        price = float(text)
        if price <= 0:
            raise ValueError("Price must be positive")
    except ValueError:
        await update.message.reply_text(
            "❌ *Invalid price!*\nSirf number likhو jaise: `85000` ya `0.15`\n\nDobara try karo:",
            parse_mode="Markdown",
        )
        return

    # Save alert
    if kind == "tp":
        set_alert(chat_id, coin, tp=price)
        e = EMOJI.get(coin, "💰")
        await update.message.reply_text(
            f"✅ *Take Profit Set Ho Gaya!*\n\n"
            f"{e} *{coin.upper()}*\n"
            f"🎯 TP: *{fmt(price)}*\n\n"
            f"⚡ Jaise hi price *{fmt(price)}* tak pahunche ga,\n"
            f"aapko notification aayega! 🔔",
            parse_mode="Markdown",
        )
    else:
        set_alert(chat_id, coin, sl=price)
        e = EMOJI.get(coin, "💰")
        await update.message.reply_text(
            f"✅ *Stop Loss Set Ho Gaya!*\n\n"
            f"{e} *{coin.upper()}*\n"
            f"🔴 SL: *{fmt(price)}*\n\n"
            f"⚡ Jaise hi price *{fmt(price)}* tak gire ga,\n"
            f"aapko notification aayega! 🔔",
            parse_mode="Markdown",
        )

    context.user_data.pop("waiting_for", None)


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.user_data.get("waiting_for"):
        context.user_data.pop("waiting_for", None)
        await update.message.reply_text("❌ *Cancel kar diya!*", parse_mode="Markdown")
    else:
        await update.message.reply_text("ℹ️ Kuch cancel karne ko nahi tha.", parse_mode="Markdown")

# ════════════════════════════════════════════════════════
#                        MAIN
# ════════════════════════════════════════════════════════
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask running on port {PORT}")

    app = Application.builder().token(BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start",  cmd_start))
    app.add_handler(CommandHandler("pp",     cmd_pp))
    app.add_handler(CommandHandler("alerts", cmd_alerts))
    app.add_handler(CommandHandler("cancel", cmd_cancel))

    # Button callbacks
    app.add_handler(CallbackQueryHandler(handle_button))

    # Text input (for TP/SL price entry)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_price_input))

    # ── Background job: Check TP/SL every 60 seconds ──
    job_queue = app.job_queue
    job_queue.run_repeating(
        check_tp_sl_alerts,
        interval=10,
        first=5,
        name="tp_sl_monitor",
    )

    logger.info("✅ Crypto Analysis Bot chal raha hai! (TP/SL monitoring active)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
