"""
╔══════════════════════════════════════════════════════════╗
║     CRYPTO FUTURES ANALYSIS BOT — Full Dashboard        ║
║     Binance Futures API | python-telegram-bot 21.x      ║
╚══════════════════════════════════════════════════════════╝

requirements.txt:
    python-telegram-bot[job-queue]==21.3
    requests==2.31.0
    flask==3.0.0
    apscheduler==3.10.4

Render env vars:
    BOT_TOKEN = your_bot_token
    PYTHON_VERSION = 3.11.0
"""

import os, time, logging, threading, requests, asyncio
from datetime import datetime, timezone
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, MessageHandler, filters
)

# ════════════════════════════════════════════
#                   CONFIG
# ════════════════════════════════════════════
BOT_TOKEN  = os.environ.get("BOT_TOKEN", "8224195294:AAEyyFVN4hzMOcKSQUov7eJQB3KRkDxBxPM")
PORT       = int(os.environ.get("PORT", 8080))
RENDER_URL = (
    os.environ.get("RENDER_EXTERNAL_URL") or
    os.environ.get("RENDER_EXTERNAL_HOSTNAME") or ""
)
if RENDER_URL and not RENDER_URL.startswith("http"):
    RENDER_URL = "https://" + RENDER_URL

WHALE_THRESHOLD = 500_000  # $500,000+

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger(__name__)

# ════════════════════════════════════════════
#              FLASK KEEP-ALIVE
# ════════════════════════════════════════════
flask_app = Flask(__name__)

@flask_app.route("/")
def home(): return "Crypto Futures Bot Running!", 200

@flask_app.route("/health")
def health(): return "OK", 200

def run_flask():
    flask_app.run(host="0.0.0.0", port=PORT, debug=False, use_reloader=False)

def keep_alive():
    if not RENDER_URL:
        return
    logger.info(f"Keep-alive: {RENDER_URL}/health")
    while True:
        try:
            requests.get(f"{RENDER_URL}/health", timeout=10)
        except:
            pass
        time.sleep(14)

# ════════════════════════════════════════════
#               COINS + EMOJI
# ════════════════════════════════════════════
COINS = {
    "btc":  "BTCUSDT",
    "ltc":  "LTCUSDT",
    "sol":  "SOLUSDT",
    "doge": "DOGEUSDT",
    "xau":  "XAUUSDT",
    "xag":  "XAGUSDT",
}
EMOJI = {
    "btc":"₿","ltc":"Ł","sol":"◎","doge":"🐕","xau":"🥇","xag":"🥈"
}

BASE_URL = "https://fapi.binance.com"

# ════════════════════════════════════════════
#               API FUNCTIONS
# ════════════════════════════════════════════
def api_get(path, params=None):
    try:
        r = requests.get(f"{BASE_URL}{path}", params=params, timeout=10)
        return r.json()
    except Exception as e:
        logger.error(f"API error {path}: {e}")
        return None

def get_ticker(symbol):
    data = api_get("/fapi/v1/ticker/24hr", {"symbol": symbol})
    if not data: return None
    return {
        "price":  float(data["lastPrice"]),
        "high":   float(data["highPrice"]),
        "low":    float(data["lowPrice"]),
        "change": float(data["priceChangePercent"]),
        "volume": float(data["volume"]),
        "time":   datetime.now(timezone.utc).strftime("%H:%M UTC"),
    }

def get_klines(symbol, interval, limit=100):
    data = api_get("/fapi/v1/klines", {
        "symbol": symbol, "interval": interval, "limit": limit
    })
    if not data: return []
    candles = []
    for k in data:
        candles.append({
            "open":  float(k[1]),
            "high":  float(k[2]),
            "low":   float(k[3]),
            "close": float(k[4]),
            "vol":   float(k[5]),
        })
    return candles

def get_orderbook(symbol, limit=50):
    data = api_get("/fapi/v1/depth", {"symbol": symbol, "limit": limit})
    if not data: return None
    bids = sum(float(b[1]) * float(b[0]) for b in data["bids"])
    asks = sum(float(a[1]) * float(a[0]) for a in data["asks"])
    return {"bids": bids, "asks": asks}

def get_recent_trades(symbol, limit=200):
    data = api_get("/fapi/v1/trades", {"symbol": symbol, "limit": limit})
    if not data: return []
    return data

# ════════════════════════════════════════════
#           TECHNICAL ANALYSIS
# ════════════════════════════════════════════
def calc_rsi(candles, period=14):
    if len(candles) < period + 1:
        return 50
    closes = [c["close"] for c in candles]
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i-1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0: return 100
    rs = avg_g / avg_l
    return round(100 - (100 / (1 + rs)), 2)

def calc_macd(candles):
    if len(candles) < 26: return None, None, None
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
    signal = ema(macd_line, 9)
    hist = macd_line[-1] - signal[-1]
    return round(macd_line[-1], 4), round(signal[-1], 4), round(hist, 4)

def calc_sar(candles):
    if len(candles) < 5: return None, None
    highs = [c["high"] for c in candles]
    lows  = [c["low"]  for c in candles]
    closes= [c["close"] for c in candles]
    af, max_af = 0.02, 0.2
    bull = closes[1] > closes[0]
    ep  = highs[0] if bull else lows[0]
    sar = lows[0]  if bull else highs[0]
    for i in range(2, len(candles)):
        prev_sar = sar
        if bull:
            sar = prev_sar + af * (ep - prev_sar)
            sar = min(sar, lows[i-1], lows[i-2])
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
            sar = max(sar, highs[i-1], highs[i-2])
            if closes[i] < ep:
                ep = closes[i]
                af = min(af + 0.02, max_af)
            if closes[i] > sar:
                bull = True
                sar  = ep
                ep   = closes[i]
                af   = 0.02
    return round(sar, 6), bull

def calc_support_resistance(candles):
    if len(candles) < 10: return [], []
    highs  = [c["high"]  for c in candles]
    lows   = [c["low"]   for c in candles]
    closes = [c["close"] for c in candles]
    current = closes[-1]

    # Swing highs & lows
    swing_h, swing_l = [], []
    for i in range(2, len(candles)-2):
        if highs[i] == max(highs[i-2:i+3]):
            swing_h.append(highs[i])
        if lows[i] == min(lows[i-2:i+3]):
            swing_l.append(lows[i])

    # Cluster nearby levels
    def cluster(levels, tol_pct=0.005):
        levels = sorted(set(round(l, 6) for l in levels))
        clusters = []
        group = [levels[0]] if levels else []
        for l in levels[1:]:
            if abs(l - group[-1]) / group[-1] < tol_pct:
                group.append(l)
            else:
                clusters.append(group)
                group = [l]
        if group: clusters.append(group)
        return [round(sum(g)/len(g), 6) for g in clusters]

    resistance = [l for l in cluster(swing_h) if l > current][-3:]
    support    = [l for l in cluster(swing_l)  if l < current][:3]
    return sorted(resistance), sorted(support, reverse=True)

def volume_trend(candles):
    if len(candles) < 10: return "Neutral"
    recent = sum(c["vol"] for c in candles[-5:]) / 5
    older  = sum(c["vol"] for c in candles[-10:-5]) / 5
    if recent > older * 1.2: return "📈 Increasing"
    if recent < older * 0.8: return "📉 Decreasing"
    return "➡️ Stable"

def price_structure(candles):
    if len(candles) < 6: return "Neutral"
    closes = [c["close"] for c in candles[-6:]]
    highs  = [c["high"]  for c in candles[-6:]]
    lows   = [c["low"]   for c in candles[-6:]]
    hh = highs[-1] > highs[-3] > highs[-5]
    hl = lows[-1]  > lows[-3]  > lows[-5]
    lh = highs[-1] < highs[-3] < highs[-5]
    ll = lows[-1]  < lows[-3]  < lows[-5]
    if hh and hl: return "📈 Higher Highs / Higher Lows"
    if lh and ll: return "📉 Lower Highs / Lower Lows"
    return "↔️ Ranging"

def get_whales(symbol):
    trades = get_recent_trades(symbol)
    if not trades: return [], {}
    whales = []
    for t in trades:
        val = float(t["price"]) * float(t["qty"])
        if val >= WHALE_THRESHOLD:
            whales.append({
                "side":  "SELL" if t["isBuyerMaker"] else "BUY",
                "qty":   float(t["qty"]),
                "price": float(t["price"]),
                "value": val,
                "time":  datetime.fromtimestamp(t["time"]/1000, tz=timezone.utc).strftime("%H:%M"),
            })
    buys  = [w for w in whales if w["side"] == "BUY"]
    sells = [w for w in whales if w["side"] == "SELL"]
    total = sum(w["value"] for w in whales)
    flow  = "🟢 Bullish" if len(buys) > len(sells) else ("🔴 Bearish" if len(sells) > len(buys) else "⚪ Neutral")
    summary = {
        "buys":  len(buys),
        "sells": len(sells),
        "flow":  flow,
        "total": total,
    }
    return whales[:5], summary

# ════════════════════════════════════════════
#              FORMAT HELPERS
# ════════════════════════════════════════════
def fmt(p):
    if p >= 10000: return f"${p:,.2f}"
    if p >= 100:   return f"${p:.2f}"
    if p >= 1:     return f"${p:.4f}"
    return f"${p:.6f}"

def bar(pct, length=10):
    filled = round(pct / 100 * length)
    return "🟩" * filled + "⬜" * (length - filled)

def bar_red(pct, length=10):
    filled = round(pct / 100 * length)
    return "🟥" * filled + "⬜" * (length - filled)

# ════════════════════════════════════════════
#              KEYBOARD
# ════════════════════════════════════════════
def main_kb(coin):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 15m", callback_data=f"tf_{coin}_15m"),
            InlineKeyboardButton("📊 1H",  callback_data=f"tf_{coin}_1h"),
            InlineKeyboardButton("📊 4H",  callback_data=f"tf_{coin}_4h"),
            InlineKeyboardButton("💵 Price", callback_data=f"price_{coin}"),
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

# ════════════════════════════════════════════
#              MESSAGE BUILDERS
# ════════════════════════════════════════════
def dashboard_msg(coin, d):
    e = EMOJI.get(coin, "💰")
    arrow = "🔼" if d["change"] >= 0 else "🔽"
    return (
        f"{e} *{coin.upper()}/USDT Futures*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💵 Price:  *{fmt(d['price'])}*\n"
        f"{arrow} Change: *{d['change']:+.2f}%*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📈 High:   {fmt(d['high'])}\n"
        f"📉 Low:    {fmt(d['low'])}\n"
        f"📊 Volume: {d['volume']:,.0f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🕐 Updated: {d['time']}\n"
        f"🚀 Market: Binance Futures"
    )

def sr_msg(coin, interval, candles):
    res, sup = calc_support_resistance(candles)
    current = candles[-1]["close"]
    lines = [f"📊 *{coin.upper()}/USDT ({interval})*\n━━━━━━━━━━━━━━━━━━"]
    lines.append("🔴 *RESISTANCE*")
    if res:
        for r in reversed(res[-3:]):
            dist = ((r - current) / current) * 100
            lines.append(f"   {fmt(r)}  ↑{dist:.2f}%")
    else:
        lines.append("   No clear level")
    lines.append(f"\n💵 *NOW: {fmt(current)}*\n")
    lines.append("🟢 *SUPPORT*")
    if sup:
        for s in sup[:3]:
            dist = ((current - s) / current) * 100
            lines.append(f"   {fmt(s)}  ↓{dist:.2f}%")
    else:
        lines.append("   No clear level")
    lines.append("━━━━━━━━━━━━━━━━━━")
    return "\n".join(lines)

def analyse_msg(coin, candles):
    rsi = calc_rsi(candles)
    macd_val, signal_val, hist = calc_macd(candles)
    sar_val, sar_bull = calc_sar(candles)
    vol_trend = volume_trend(candles)
    structure = price_structure(candles)
    current = candles[-1]["close"]

    # RSI
    if rsi >= 70:   rsi_status = "🔴 Overbought"
    elif rsi <= 30: rsi_status = "🟢 Oversold"
    else:           rsi_status = "🟡 Neutral"

    # MACD
    if hist is not None:
        macd_sig = "📈 Bullish Crossover" if hist > 0 else "📉 Bearish Crossover"
    else:
        macd_sig = "⚪ No Signal"

    # SAR
    sar_sig = f"🟢 Buy (dots below)" if sar_bull else "🔴 Sell (dots above)"

    # Confidence
    bullish_signals = 0
    total_signals = 4
    if rsi < 60 and rsi > 40: bullish_signals += 1
    elif rsi <= 40: bullish_signals += 1
    if hist and hist > 0: bullish_signals += 1
    if sar_bull: bullish_signals += 1
    if "Increasing" in vol_trend: bullish_signals += 1
    confidence = round((bullish_signals / total_signals) * 100)
    bias = "🟢 Bullish" if confidence >= 60 else ("🔴 Bearish" if confidence <= 40 else "🟡 Neutral")

    # Breakout detection
    res, sup = calc_support_resistance(candles)
    breakout = ""
    if res and current >= res[0] * 0.999:
        breakout = f"\n⚡ *BREAKOUT ALERT!*\nPrice testing resistance {fmt(res[0])}!"
    elif sup and current <= sup[0] * 1.001:
        breakout = f"\n⚡ *BREAKDOWN ALERT!*\nPrice testing support {fmt(sup[0])}!"

    return (
        f"🔬 *{coin.upper()}/USDT Technical Analysis*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📐 *RSI:* {rsi}\n"
        f"   Status: {rsi_status}\n\n"
        f"📈 *MACD*\n"
        f"   Signal: {macd_sig}\n\n"
        f"🎯 *Parabolic SAR*\n"
        f"   Signal: {sar_sig}\n\n"
        f"📊 *Volume*\n"
        f"   {vol_trend}\n\n"
        f"🏗️ *Price Structure*\n"
        f"   {structure}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🧠 *Market Bias*\n"
        f"   {bias}\n"
        f"   Confidence: *{confidence}%*"
        f"{breakout}"
    )

def depth_msg(coin, depth):
    total = depth["bids"] + depth["asks"]
    bid_pct = (depth["bids"] / total) * 100
    ask_pct = (depth["asks"] / total) * 100
    bid_bars = round(bid_pct / 10)
    ask_bars = round(ask_pct / 10)
    return (
        f"📖 *{coin.upper()}/USDT Market Depth*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🟢 *Buyers*  {bid_pct:.1f}%\n"
        f"{'🟩' * bid_bars}{'⬜' * (10 - bid_bars)}\n"
        f"${depth['bids']:,.0f}\n\n"
        f"🔴 *Sellers* {ask_pct:.1f}%\n"
        f"{'🟥' * ask_bars}{'⬜' * (10 - ask_bars)}\n"
        f"${depth['asks']:,.0f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{'🟢 Buyers Dominant' if bid_pct > ask_pct else '🔴 Sellers Dominant'}\n"
        f"Pressure: *{max(bid_pct, ask_pct):.1f}%*"
    )

def whales_msg(coin, whales, summary):
    if not whales:
        return f"🐋 *{coin.upper()}/USDT Whale Activity*\n\nNo whale trades detected recently.\n(Threshold: ${WHALE_THRESHOLD:,})"
    lines = [f"🐋 *{coin.upper()}/USDT Whale Activity*\n━━━━━━━━━━━━━━━━━━"]
    for i, w in enumerate(whales[:5], 1):
        emoji = "🟢" if w["side"] == "BUY" else "🔴"
        lines.append(
            f"{i}️⃣ {emoji} *{w['side']}*\n"
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

def whale_alert_msg(coin, w):
    e = EMOJI.get(coin, "💰")
    emoji = "🟢" if w["side"] == "BUY" else "🔴"
    return (
        f"🚨 *WHALE ALERT!* 🚨\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{e} Pair: *{coin.upper()}/USDT*\n\n"
        f"{emoji} Direction: *{w['side']}*\n"
        f"📦 Quantity: {w['qty']:,.0f} {coin.upper()}\n"
        f"💰 Value: *${w['value']:,.0f}*\n\n"
        f"💵 Price: {fmt(w['price'])}\n"
        f"🕐 Time: {w['time']}\n"
        f"🚀 Market: Binance Futures"
    )

# ════════════════════════════════════════════
#              BOT HANDLERS
# ════════════════════════════════════════════
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🚀 *Crypto Futures Analysis Bot*\n\n"
        "📌 *Commands:*\n"
        "`/pp btc`  — Bitcoin\n"
        "`/pp ltc`  — Litecoin\n"
        "`/pp sol`  — Solana\n"
        "`/pp doge` — Dogecoin\n"
        "`/pp xau`  — Gold\n"
        "`/pp xag`  — Silver\n\n"
        "📊 *Features:*\n"
        "• Live Futures Price\n"
        "• 15m / 1H / 4H Support & Resistance\n"
        "• Technical Analysis (RSI, MACD, SAR)\n"
        "• Order Book Depth\n"
        "• 🐋 Whale Detection & Auto Alerts\n\n"
        "💪 *Happy Trading!*",
        parse_mode="Markdown"
    )

async def pp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ Coin daalo! Example: `/pp ltc`", parse_mode="Markdown")
        return
    coin = context.args[0].lower()
    if coin not in COINS:
        await update.message.reply_text(
            f"❌ Supported: {', '.join(c.upper() for c in COINS)}", parse_mode="Markdown")
        return
    msg = await update.message.reply_text("⏳ Fetching data...", parse_mode="Markdown")
    d = get_ticker(COINS[coin])
    if not d:
        await msg.edit_text("❌ Data nahi mila. Baad mein try karo.")
        return
    await msg.edit_text(
        dashboard_msg(coin, d),
        parse_mode="Markdown",
        reply_markup=main_kb(coin)
    )

async def btn(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data

    # ── Timeframe S/R ──
    if data.startswith("tf_"):
        _, coin, tf = data.split("_")
        interval_map = {"15m":"15m","1h":"1h","4h":"4h"}
        interval = interval_map.get(tf, "1h")
        candles = get_klines(COINS[coin], interval, 100)
        if not candles:
            await q.answer("❌ Data nahi mila!", show_alert=True); return
        await q.edit_message_text(
            sr_msg(coin, tf.upper(), candles),
            parse_mode="Markdown",
            reply_markup=main_kb(coin)
        )

    # ── Price ──
    elif data.startswith("price_"):
        coin = data[6:]
        d = get_ticker(COINS[coin])
        if not d:
            await q.answer("❌ Data nahi mila!", show_alert=True); return
        await q.edit_message_text(
            dashboard_msg(coin, d),
            parse_mode="Markdown",
            reply_markup=main_kb(coin)
        )

    # ── Analyse ──
    elif data.startswith("analyse_"):
        coin = data[8:]
        candles = get_klines(COINS[coin], "1h", 100)
        if not candles:
            await q.answer("❌ Data nahi mila!", show_alert=True); return
        await q.edit_message_text(
            analyse_msg(coin, candles),
            parse_mode="Markdown",
            reply_markup=main_kb(coin)
        )

    # ── Depth ──
    elif data.startswith("depth_"):
        coin = data[6:]
        depth = get_orderbook(COINS[coin])
        if not depth:
            await q.answer("❌ Data nahi mila!", show_alert=True); return
        await q.edit_message_text(
            depth_msg(coin, depth),
            parse_mode="Markdown",
            reply_markup=main_kb(coin)
        )

    # ── Whales ──
    elif data.startswith("whales_"):
        coin = data[7:]
        ws, summary = get_whales(COINS[coin])
        await q.edit_message_text(
            whales_msg(coin, ws, summary),
            parse_mode="Markdown",
            reply_markup=main_kb(coin)
        )

    # ── Refresh ──
    elif data.startswith("refresh_"):
        coin = data[8:]
        d = get_ticker(COINS[coin])
        if not d:
            await q.answer("❌ Data nahi mila!", show_alert=True); return
        await q.edit_message_text(
            dashboard_msg(coin, d),
            parse_mode="Markdown",
            reply_markup=main_kb(coin)
        )

# ════════════════════════════════════════════
#         WHALE ALERT JOB (Background)
# ════════════════════════════════════════════
seen_whale_trades = set()
subscribed_users = set()

async def subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribed_users.add(chat_id)
    await update.message.reply_text(
        "✅ *Whale Alert Subscribe ho gaye!*\n\n"
        "🐋 Koi bhi $500,000+ trade hone pe auto alert aayega!\n"
        "Use `/unsubscribe` to stop alerts.",
        parse_mode="Markdown"
    )

async def unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    subscribed_users.discard(chat_id)
    await update.message.reply_text("✅ Whale alerts band kar diye!", parse_mode="Markdown")

async def whale_monitor(context: ContextTypes.DEFAULT_TYPE):
    if not subscribed_users:
        return
    for coin, symbol in COINS.items():
        try:
            trades = get_recent_trades(symbol, 50)
            for t in trades:
                tid = t["id"]
                if tid in seen_whale_trades:
                    continue
                val = float(t["price"]) * float(t["qty"])
                if val >= WHALE_THRESHOLD:
                    seen_whale_trades.add(tid)
                    if len(seen_whale_trades) > 10000:
                        # Cleanup old
                        seen_whale_trades.clear()
                    w = {
                        "side":  "SELL" if t["isBuyerMaker"] else "BUY",
                        "qty":   float(t["qty"]),
                        "price": float(t["price"]),
                        "value": val,
                        "time":  datetime.fromtimestamp(t["time"]/1000, tz=timezone.utc).strftime("%H:%M"),
                    }
                    msg = whale_alert_msg(coin, w)
                    for uid in subscribed_users:
                        try:
                            await context.bot.send_message(uid, msg, parse_mode="Markdown")
                        except:
                            pass
        except Exception as e:
            logger.error(f"Whale monitor error ({coin}): {e}")

# ════════════════════════════════════════════
#                   MAIN
# ════════════════════════════════════════════
def main():
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=keep_alive, daemon=True).start()
    logger.info(f"Flask port {PORT} | URL: {RENDER_URL or 'local'}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pp", pp))
    app.add_handler(CommandHandler("subscribe", subscribe))
    app.add_handler(CommandHandler("unsubscribe", unsubscribe))
    app.add_handler(CallbackQueryHandler(btn))

    # Whale monitor every 30 seconds
    app.job_queue.run_repeating(whale_monitor, interval=30, first=15)

    logger.info("✅ Crypto Futures Analysis Bot chal raha hai!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
