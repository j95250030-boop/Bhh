"""
╔══════════════════════════════════════════════════════════╗
║        CRYPTO FUTURES ANALYSIS BOT — FINAL VERSION      ║
║        Railway Deploy | python-telegram-bot 21.x        ║
╚══════════════════════════════════════════════════════════╝

requirements.txt:
    python-telegram-bot[job-queue]==21.3
    requests==2.31.0
    flask==3.0.0
    apscheduler==3.10.4

.python-version:
    3.11

Railway Environment Variables:
    BOT_TOKEN = your_telegram_bot_token

Commands:
    /start       - Welcome message
    /pp btc      - Bitcoin dashboard
    /pp ltc      - Litecoin dashboard
    /pp sol      - Solana dashboard
    /pp doge     - Dogecoin dashboard
    /pp xau      - Gold dashboard
    /pp xag      - Silver dashboard
    /subscribe   - Enable auto whale alerts
    /unsubscribe - Disable whale alerts
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
    MessageHandler,
    filters,
)

# ════════════════════════════════════════════════════════
#                        CONFIG
# ════════════════════════════════════════════════════════
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
PORT      = int(os.environ.get("PORT", 8080))

WHALE_THRESHOLD = 500_000  # $500,000+

FUTURES_BASE = "https://fapi.binance.com"
SPOT_BASE    = "https://api.binance.com"

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
def home():
    return "Crypto Futures Bot Running!", 200

@flask_app.route("/health")
def health():
    return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

# ════════════════════════════════════════════════════════
#                   SUPPORTED COINS
# ════════════════════════════════════════════════════════
COINS = {
    "btc":  "BTCUSDT",
    "ltc":  "LTCUSDT",
    "sol":  "SOLUSDT",
    "doge": "DOGEUSDT",
    "xau":  "XAUUSDT",
    "xag":  "XAGUSDT",
}

EMOJI = {
    "btc":  "₿",
    "ltc":  "Ł",
    "sol":  "◎",
    "doge": "🐕",
    "xau":  "🥇",
    "xag":  "🥈",
}

# ════════════════════════════════════════════════════════
#                     API FUNCTIONS
# ════════════════════════════════════════════════════════
def api_get(path, params=None, base=FUTURES_BASE):
    """Generic GET request with error handling."""
    try:
        r = requests.get(f"{base}{path}", params=params, timeout=8)
        data = r.json()
        return data
    except Exception as e:
        logger.error(f"API error [{base}{path}]: {e}")
        return None


def get_ticker(symbol):
    """Get 24hr ticker — Futures first, Spot fallback."""
    def parse(data, source):
        if not data or not isinstance(data, dict):
            return None
        if "lastPrice" not in data:
            return None
        try:
            return {
                "price":  float(data["lastPrice"]),
                "high":   float(data["highPrice"]),
                "low":    float(data["lowPrice"]),
                "change": float(data["priceChangePercent"]),
                "volume": float(data["volume"]),
                "time":   datetime.now(timezone.utc).strftime("%H:%M UTC"),
                "source": source,
            }
        except Exception as e:
            logger.error(f"parse ticker error: {e}")
            return None

    # Try Futures
    result = parse(
        api_get("/fapi/v1/ticker/24hr", {"symbol": symbol}, FUTURES_BASE),
        "Binance Futures"
    )
    if result:
        return result

    # Fallback to Spot
    result = parse(
        api_get("/api/v3/ticker/24hr", {"symbol": symbol}, SPOT_BASE),
        "Binance Spot"
    )
    return result


def get_klines(symbol, interval, limit=100):
    """Get candlestick data — Futures first, Spot fallback."""
    def parse(data):
        if not data or not isinstance(data, list):
            return []
        try:
            return [
                {
                    "open":  float(k[1]),
                    "high":  float(k[2]),
                    "low":   float(k[3]),
                    "close": float(k[4]),
                    "vol":   float(k[5]),
                }
                for k in data
            ]
        except:
            return []

    candles = parse(api_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit}, FUTURES_BASE))
    if candles:
        return candles

    candles = parse(api_get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit}, SPOT_BASE))
    return candles


def get_orderbook(symbol):
    """Get order book depth — Futures first, Spot fallback."""
    def parse(data):
        if not data or not isinstance(data, dict):
            return None
        if "bids" not in data or "asks" not in data:
            return None
        try:
            bids = sum(float(b[0]) * float(b[1]) for b in data["bids"])
            asks = sum(float(a[0]) * float(a[1]) for a in data["asks"])
            return {"bids": bids, "asks": asks}
        except:
            return None

    result = parse(api_get("/fapi/v1/depth", {"symbol": symbol, "limit": 50}, FUTURES_BASE))
    if result:
        return result

    result = parse(api_get("/api/v3/depth", {"symbol": symbol, "limit": 50}, SPOT_BASE))
    return result


def get_recent_trades(symbol, limit=200):
    """Get recent trades — Futures first, Spot fallback."""
    def parse(data):
        if data and isinstance(data, list):
            return data
        return []

    trades = parse(api_get("/fapi/v1/trades", {"symbol": symbol, "limit": limit}, FUTURES_BASE))
    if trades:
        return trades

    trades = parse(api_get("/api/v3/trades", {"symbol": symbol, "limit": limit}, SPOT_BASE))
    return trades

# ════════════════════════════════════════════════════════
#                  TECHNICAL ANALYSIS
# ════════════════════════════════════════════════════════
def calc_rsi(candles, period=14):
    """Calculate RSI."""
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
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def calc_macd(candles):
    """Calculate MACD, Signal, Histogram."""
    if len(candles) < 26:
        return None, None, None
    closes = [c["close"] for c in candles]

    def ema(data, period):
        k = 2 / (period + 1)
        result = [data[0]]
        for v in data[1:]:
            result.append(v * k + result[-1] * (1 - k))
        return result

    ema12 = ema(closes, 12)
    ema26 = ema(closes, 26)
    macd_line = [e12 - e26 for e12, e26 in zip(ema12, ema26)]
    signal_line = ema(macd_line, 9)
    histogram = macd_line[-1] - signal_line[-1]
    return round(macd_line[-1], 6), round(signal_line[-1], 6), round(histogram, 6)


def calc_sar(candles):
    """Calculate Parabolic SAR. Returns (sar_value, is_bullish)."""
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
                bull = False
                sar  = ep
                ep   = closes[i]
                af   = 0.02
        else:
            sar = prev_sar + af * (ep - sar)
            sar = max(sar, highs[i - 1], highs[i - 2])
            if closes[i] < ep:
                ep = closes[i]
                af = min(af + 0.02, max_af)
            if closes[i] > sar:
                bull = True
                sar  = ep
                ep   = closes[i]
                af   = 0.02
    return round(sar, 8), bull


def calc_sr(candles):
    """Calculate Support & Resistance zones."""
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
        if not levels:
            return []
        clusters = []
        group = [levels[0]]
        for l in levels[1:]:
            if abs(l - group[-1]) / group[-1] < tol:
                group.append(l)
            else:
                clusters.append(group)
                group = [l]
        if group:
            clusters.append(group)
        return [round(sum(g) / len(g), 8) for g in clusters]

    resistance = sorted([l for l in cluster(swing_highs) if l > current])[-3:]
    support    = sorted([l for l in cluster(swing_lows)  if l < current], reverse=True)[:3]
    return resistance, support


def vol_trend(candles):
    """Detect volume trend."""
    if len(candles) < 10:
        return "➡️ Stable"
    recent = sum(c["vol"] for c in candles[-5:]) / 5
    older  = sum(c["vol"] for c in candles[-10:-5]) / 5
    if recent > older * 1.2:
        return "📈 Increasing"
    if recent < older * 0.8:
        return "📉 Decreasing"
    return "➡️ Stable"


def price_struct(candles):
    """Detect price structure."""
    if len(candles) < 6:
        return "↔️ Ranging"
    h = [c["high"] for c in candles[-6:]]
    l = [c["low"]  for c in candles[-6:]]
    if h[-1] > h[-3] > h[-5] and l[-1] > l[-3] > l[-5]:
        return "📈 Higher Highs / Higher Lows"
    if h[-1] < h[-3] < h[-5] and l[-1] < l[-3] < l[-5]:
        return "📉 Lower Highs / Lower Lows"
    return "↔️ Ranging"


def get_whales(symbol):
    """Detect whale trades from recent trade data."""
    trades = get_recent_trades(symbol)
    if not trades:
        return [], {}
    whales = []
    for t in trades:
        try:
            val = float(t["price"]) * float(t["qty"])
            if val >= WHALE_THRESHOLD:
                whales.append({
                    "side":  "SELL" if t["isBuyerMaker"] else "BUY",
                    "qty":   float(t["qty"]),
                    "price": float(t["price"]),
                    "value": val,
                    "time":  datetime.fromtimestamp(
                        t["time"] / 1000, tz=timezone.utc
                    ).strftime("%H:%M"),
                })
        except:
            continue
    buys  = [w for w in whales if w["side"] == "BUY"]
    sells = [w for w in whales if w["side"] == "SELL"]
    if len(buys) > len(sells):
        flow = "🟢 Bullish"
    elif len(sells) > len(buys):
        flow = "🔴 Bearish"
    else:
        flow = "⚪ Neutral"
    summary = {
        "buys":  len(buys),
        "sells": len(sells),
        "flow":  flow,
        "total": sum(w["value"] for w in whales),
    }
    return whales[:5], summary

# ════════════════════════════════════════════════════════
#                   FORMAT HELPERS
# ════════════════════════════════════════════════════════
def fmt(price):
    """Format price based on magnitude."""
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
            InlineKeyboardButton("📊 15m",    callback_data=f"tf_{coin}_15m"),
            InlineKeyboardButton("📊 1H",     callback_data=f"tf_{coin}_1h"),
            InlineKeyboardButton("📊 4H",     callback_data=f"tf_{coin}_4h"),
            InlineKeyboardButton("💵 Price",  callback_data=f"price_{coin}"),
        ],
        [
            InlineKeyboardButton("🔬 Analyse", callback_data=f"analyse_{coin}"),
            InlineKeyboardButton("📖 Depth",   callback_data=f"depth_{coin}"),
        ],
        [
            InlineKeyboardButton("🐋 Whales", callback_data=f"whales_{coin}"),
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
    return (
        f"{e} *{coin.upper()}/USDT*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 Price:  *{fmt(d['price'])}*\n"
        f"{arrow} Change: *{d['change']:+.2f}%*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 High:   {fmt(d['high'])}\n"
        f"📉 Low:    {fmt(d['low'])}\n"
        f"📊 Volume: {d['volume']:,.0f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Updated: {d['time']}\n"
        f"🚀 {d['source']}"
    )


def build_sr(coin, tf, candles):
    res, sup = calc_sr(candles)
    current  = candles[-1]["close"]
    lines    = [
        f"📊 *{coin.upper()}/USDT ({tf})*\n━━━━━━━━━━━━━━━━━━",
        "🔴 *RESISTANCE*",
    ]
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
    rsi             = calc_rsi(candles)
    _, _, hist      = calc_macd(candles)
    sar_val, bull   = calc_sar(candles)
    vt              = vol_trend(candles)
    ps              = price_struct(candles)
    current         = candles[-1]["close"]

    # RSI status
    if rsi >= 70:
        rsi_s = "🔴 Overbought"
    elif rsi <= 30:
        rsi_s = "🟢 Oversold"
    else:
        rsi_s = "🟡 Neutral"

    # MACD signal
    if hist is not None:
        macd_s = "📈 Bullish Crossover" if hist > 0 else "📉 Bearish Crossover"
    else:
        macd_s = "⚪ No Signal"

    # SAR signal
    sar_s = "🟢 Buy (dots below)" if bull else "🔴 Sell (dots above)"

    # Confidence
    score = sum([
        rsi <= 50,
        hist is not None and hist > 0,
        bull,
        "Increasing" in vt,
    ])
    conf = round((score / 4) * 100)
    if conf >= 60:
        bias = "🟢 Bullish"
    elif conf <= 40:
        bias = "🔴 Bearish"
    else:
        bias = "🟡 Neutral"

    # Breakout alert
    res, sup = calc_sr(candles)
    breakout = ""
    if res and current >= res[0] * 0.999:
        breakout = f"\n\n⚡ *BREAKOUT ALERT!*\nPrice testing resistance {fmt(res[0])}!"
    elif sup and current <= sup[0] * 1.001:
        breakout = f"\n\n⚡ *BREAKDOWN ALERT!*\nPrice testing support {fmt(sup[0])}!"

    return (
        f"🔬 *{coin.upper()}/USDT Technical Analysis*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📐 *RSI:* {rsi}\n"
        f"   Status: {rsi_s}\n\n"
        f"📈 *MACD*\n"
        f"   {macd_s}\n\n"
        f"🎯 *Parabolic SAR*\n"
        f"   {sar_s}\n\n"
        f"📊 *Volume*\n"
        f"   {vt}\n\n"
        f"🏗️ *Price Structure*\n"
        f"   {ps}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🧠 *Market Bias:* {bias}\n"
        f"   Confidence: *{conf}%*"
        f"{breakout}"
    )


def build_depth(coin, depth):
    total   = depth["bids"] + depth["asks"]
    bid_pct = (depth["bids"] / total) * 100
    ask_pct = (depth["asks"] / total) * 100
    bid_bar = "🟩" * round(bid_pct / 10) + "⬜" * (10 - round(bid_pct / 10))
    ask_bar = "🟥" * round(ask_pct / 10) + "⬜" * (10 - round(ask_pct / 10))
    dom     = "🟢 Buyers Dominant" if bid_pct > ask_pct else "🔴 Sellers Dominant"
    return (
        f"📖 *{coin.upper()}/USDT Market Depth*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🟢 *Buyers* {bid_pct:.1f}%\n"
        f"{bid_bar}\n"
        f"${depth['bids']:,.0f}\n\n"
        f"🔴 *Sellers* {ask_pct:.1f}%\n"
        f"{ask_bar}\n"
        f"${depth['asks']:,.0f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{dom}\n"
        f"Pressure: *{max(bid_pct, ask_pct):.1f}%*"
    )


def build_whales(coin, whales, summary):
    if not whales:
        return (
            f"🐋 *{coin.upper()}/USDT Whale Activity*\n\n"
            f"Koi whale trade nahi mila!\n"
            f"Threshold: ${WHALE_THRESHOLD:,}"
        )
    lines = [f"🐋 *{coin.upper()}/USDT Whale Activity*\n━━━━━━━━━━━━━━━━━━"]
    for i, w in enumerate(whales, 1):
        arrow = "🟢" if w["side"] == "BUY" else "🔴"
        lines.append(
            f"{i}️⃣ {arrow} *{w['side']}*\n"
            f"   💰 ${w['value']:,.0f}\n"
            f"   📦 {w['qty']:,.0f} {coin.upper()}\n"
            f"   💵 {fmt(w['price'])} @ {w['time']}"
        )
    lines.append(
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 *Whale Summary*\n"
        f"🟢 Buy Whales:  {summary['buys']}\n"
        f"🔴 Sell Whales: {summary['sells']}\n"
        f"🌊 Net Flow: {summary['flow']}\n"
        f"💎 Total Value: ${summary['total']:,.0f}"
    )
    return "\n".join(lines)


def build_whale_alert(coin, w):
    e     = EMOJI.get(coin, "💰")
    arrow = "🟢" if w["side"] == "BUY" else "🔴"
    return (
        f"🚨 *WHALE ALERT!* 🚨\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e} Pair: *{coin.upper()}/USDT*\n\n"
        f"{arrow} Direction: *{w['side']}*\n"
        f"📦 Quantity: {w['qty']:,.0f} {coin.upper()}\n"
        f"💰 Value: *${w['value']:,.0f}*\n\n"
        f"💵 Price: {fmt(w['price'])}\n"
        f"🕐 Time: {w['time']}\n"
        f"🚀 Binance Futures"
    )

# ════════════════════════════════════════════════════════
#                    BOT HANDLERS
# ════════════════════════════════════════════════════════
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *Crypto Futures Analysis Bot*\n\n"
        "📌 *Commands:*\n"
        "`/pp btc`  — Bitcoin\n"
        "`/pp ltc`  — Litecoin\n"
        "`/pp sol`  — Solana\n"
        "`/pp doge` — Dogecoin\n"
        "`/pp xau`  — Gold 🥇\n"
        "`/pp xag`  — Silver 🥈\n\n"
        "📊 *Features:*\n"
        "• Live Futures Price\n"
        "• Support & Resistance (15m/1H/4H)\n"
        "• Technical Analysis (RSI, MACD, SAR)\n"
        "• Order Book Depth\n"
        "• 🐋 Whale Detection & Auto Alerts\n\n"
        "🐋 *Whale alerts ke liye:*\n"
        "`/subscribe` — alerts ON\n"
        "`/unsubscribe` — alerts OFF\n\n"
        "💪 *Happy Trading!*",
        parse_mode="Markdown",
    )


async def cmd_pp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "❌ Coin naam daalo!\nExample: `/pp ltc`",
            parse_mode="Markdown",
        )
        return

    coin = context.args[0].lower()
    if coin not in COINS:
        await update.message.reply_text(
            f"❌ Supported coins: {', '.join(c.upper() for c in COINS)}",
            parse_mode="Markdown",
        )
        return

    msg = await update.message.reply_text("⏳ Fetching data...", parse_mode="Markdown")
    d   = get_ticker(COINS[coin])

    if not d:
        await msg.edit_text("❌ Data nahi mila. Thodi der baad try karo.")
        return

    await msg.edit_text(
        build_dashboard(coin, d),
        parse_mode="Markdown",
        reply_markup=main_kb(coin),
    )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_users.add(update.effective_chat.id)
    await update.message.reply_text(
        "✅ *Whale Alerts ON!*\n\n"
        "🐋 $500,000+ ka koi bhi trade hone pe automatic alert aayega!\n\n"
        "`/unsubscribe` se band karo.",
        parse_mode="Markdown",
    )


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    subscribed_users.discard(update.effective_chat.id)
    await update.message.reply_text("✅ Whale alerts band kar diye!", parse_mode="Markdown")


async def handle_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    data = q.data

    # ── Timeframe S/R ──
    if data.startswith("tf_"):
        parts    = data.split("_")
        coin, tf = parts[1], parts[2]
        candles  = get_klines(COINS[coin], tf, 100)
        if not candles:
            await q.answer("❌ Data nahi mila!", show_alert=True)
            return
        await q.edit_message_text(
            build_sr(coin, tf.upper(), candles),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

    # ── Price ──
    elif data.startswith("price_"):
        coin = data[6:]
        d    = get_ticker(COINS[coin])
        if not d:
            await q.answer("❌ Data nahi mila!", show_alert=True)
            return
        await q.edit_message_text(
            build_dashboard(coin, d),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

    # ── Analyse ──
    elif data.startswith("analyse_"):
        coin    = data[8:]
        candles = get_klines(COINS[coin], "1h", 100)
        if not candles:
            await q.answer("❌ Data nahi mila!", show_alert=True)
            return
        await q.edit_message_text(
            build_analyse(coin, candles),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

    # ── Depth ──
    elif data.startswith("depth_"):
        coin  = data[6:]
        depth = get_orderbook(COINS[coin])
        if not depth:
            await q.answer("❌ Data nahi mila!", show_alert=True)
            return
        await q.edit_message_text(
            build_depth(coin, depth),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

    # ── Whales ──
    elif data.startswith("whales_"):
        coin        = data[7:]
        ws, summary = get_whales(COINS[coin])
        await q.edit_message_text(
            build_whales(coin, ws, summary),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

    # ── Refresh ──
    elif data.startswith("refresh_"):
        coin = data[8:]
        d    = get_ticker(COINS[coin])
        if not d:
            await q.answer("❌ Data nahi mila!", show_alert=True)
            return
        await q.edit_message_text(
            build_dashboard(coin, d),
            parse_mode="Markdown",
            reply_markup=main_kb(coin),
        )

# ════════════════════════════════════════════════════════
#              AUTOMATIC WHALE ALERT JOB
# ════════════════════════════════════════════════════════
subscribed_users: set = set()
seen_whale_ids:   set = set()


async def whale_monitor(context: ContextTypes.DEFAULT_TYPE):
    """Check recent trades for whale activity and alert subscribed users."""
    if not subscribed_users:
        return

    for coin, symbol in COINS.items():
        try:
            trades = get_recent_trades(symbol, 100)
            for t in trades:
                try:
                    tid = t["id"]
                    if tid in seen_whale_ids:
                        continue
                    val = float(t["price"]) * float(t["qty"])
                    if val < WHALE_THRESHOLD:
                        continue

                    seen_whale_ids.add(tid)
                    if len(seen_whale_ids) > 50000:
                        seen_whale_ids.clear()

                    w = {
                        "side":  "SELL" if t["isBuyerMaker"] else "BUY",
                        "qty":   float(t["qty"]),
                        "price": float(t["price"]),
                        "value": val,
                        "time":  datetime.fromtimestamp(
                            t["time"] / 1000, tz=timezone.utc
                        ).strftime("%H:%M"),
                    }
                    alert_msg = build_whale_alert(coin, w)
                    for uid in list(subscribed_users):
                        try:
                            await context.bot.send_message(
                                chat_id=uid,
                                text=alert_msg,
                                parse_mode="Markdown",
                            )
                        except Exception as ex:
                            logger.warning(f"Alert send failed to {uid}: {ex}")
                except:
                    continue
        except Exception as e:
            logger.error(f"Whale monitor error ({coin}): {e}")

# ════════════════════════════════════════════════════════
#                        MAIN
# ════════════════════════════════════════════════════════
def main():
    # Start Flask in background thread
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info(f"Flask running on port {PORT}")

    # Build Telegram app
    app = Application.builder().token(BOT_TOKEN).build()

    # Handlers
    app.add_handler(CommandHandler("start",       cmd_start))
    app.add_handler(CommandHandler("pp",          cmd_pp))
    app.add_handler(CommandHandler("subscribe",   cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CallbackQueryHandler(handle_button))

    # Whale monitor job — every 30 seconds
    app.job_queue.run_repeating(whale_monitor, interval=30, first=15)

    logger.info("✅ Crypto Futures Analysis Bot chal raha hai!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
