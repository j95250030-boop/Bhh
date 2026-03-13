"""
╔══════════════════════════════════════════════════════════╗
║     CRYPTO ANALYSIS BOT — CoinGecko API Version         ║
║     GitHub Actions Compatible | No IP Block!            ║
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
    ContextTypes,
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
# CoinGecko IDs
COINS = {
    "btc":  "bitcoin",
    "ltc":  "litecoin",
    "sol":  "solana",
    "doge": "dogecoin",
    "xau":  "METAL",   # gold - metals api
    "xag":  "METAL",   # silver - metals api
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
    """Get gold/silver price from metals.live API."""
    try:
        data = api_get(f"{METALS_BASE}/latest")
        if not data or not isinstance(data, list):
            return None
        metals = {item["metal"]: item for item in data}
        key    = "gold" if coin == "xau" else "silver"
        if key not in metals:
            return None
        price = float(metals[key]["price"])
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
        logger.error(f"Metal ticker error: {e}")
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

    ema12    = ema(closes, 12)
    ema26    = ema(closes, 26)
    macd_line  = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
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
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{coin}"),
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

    rsi_s = "🔴 Overbought" if rsi >= 70 else ("🟢 Oversold" if rsi <= 30 else "🟡 Neutral")
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
        "📊 *Features:*\n"
        "• Live Price\n"
        "• Support & Resistance (15m/1H/4H)\n"
        "• RSI, MACD, SAR Analysis\n"
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


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    data = q.data

    if data.startswith("tf_"):
        parts    = data.split("_")
        coin, tf = parts[1], parts[2]
        candles  = get_klines(coin, tf, 100)
        await q.edit_message_text(
            build_sr(coin, tf.upper(), candles),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

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

    elif data.startswith("analyse_"):
        coin    = data[8:]
        candles = get_klines(coin, "1h", 100)
        await q.edit_message_text(
            build_analyse(coin, candles),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

    elif data.startswith("refresh_"):
        coin = data[8:]
        d    = get_ticker(coin)
        if not d:
            await q.answer("❌ Data nahi mila!", show_alert=True)
            return
        await q.edit_message_text(
            build_dashboard(coin, d),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

# ════════════════════════════════════════════════════════
#                        MAIN
# ════════════════════════════════════════════════════════
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask running on port {PORT}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("pp",    cmd_pp))
    app.add_handler(CallbackQueryHandler(handle_button))

    logger.info("✅ Crypto Analysis Bot chal raha hai!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
