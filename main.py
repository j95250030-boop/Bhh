"""
Crypto Futures Telegram Bot
============================
Features:
- Live price with indicators (EMA, MACD, RSI, Bollinger, SAR)
- Set TP (Take Profit) and SL (Stop Loss) for practice
- Auto notification when TP/SL hit
- Beautiful formatted messages with emojis

Setup:
1. pip install python-telegram-bot requests
2. Create bot via @BotFather and get token
3. Replace BOT_TOKEN with your token
4. Run: python telegram_bot.py
"""

import asyncio
import os
import sys
import requests
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    CallbackQueryHandler, 
    MessageHandler,
    ContextTypes,
    filters
)

# ============== CONFIGURATION ==============
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # Set this in GitHub Secrets / Environment Variables
if not BOT_TOKEN:
    print("❌ ERROR: BOT_TOKEN environment variable not set!")
    print("   GitHub par jao: Settings → Secrets → New secret → BOT_TOKEN")
    sys.exit(1)
API_URL = "https://v0-binance-futures-api-two.vercel.app/api/crypto"

# Supported symbols
SYMBOLS = ["BTC", "ETH", "DOGE", "LTC", "BNB", "SOL", "XRP", "AVAX"]

# Store user alerts (In production, use database)
user_alerts = {}  # {user_id: [{symbol, tp, sl, entry, type}]}

# Motivational quotes for SL hit
MOTIVATIONAL_QUOTES = [
    "Every loss is a lesson. Keep learning! 📚",
    "Markets reward patience. Stay strong! 💪",
    "Even the best traders have losses. You got this! 🌟",
    "Failure is the mother of success. Keep going! 🚀",
    "One loss doesn't define you. Tomorrow is a new day! 🌅",
    "Risk management is key. You're learning! 📈",
    "The market will always be there. Take a break if needed! ☕",
    "Losses are tuition fees for trading education! 🎓",
]

# ============== API FUNCTIONS ==============
def fetch_crypto_data(symbol: str, interval: str = "1h") -> dict:
    """Fetch crypto data from our API"""
    try:
        response = requests.get(f"{API_URL}?symbol={symbol}&interval={interval}", timeout=15)
        return response.json()
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_current_price(symbol: str) -> float:
    """Get current price for a symbol"""
    data = fetch_crypto_data(symbol)
    if data.get("success"):
        return data["data"]["price"]["current"]
    return 0

# ============== MESSAGE FORMATTERS ==============
def format_price_message(data: dict, symbol: str) -> str:
    """Format price message like screenshot"""
    d = data["data"]
    price = d["price"]["current"]
    change = d["stats24h"]["priceChangePercent"]
    high = d["stats24h"]["high"]
    low = d["stats24h"]["low"]
    volume = d["stats24h"]["volume"]
    
    # Change emoji based on positive/negative
    change_emoji = "🟢" if change >= 0 else "🔴"
    change_arrow = "▲" if change >= 0 else "▼"
    
    # Format volume
    if volume >= 1_000_000_000:
        vol_str = f"{volume/1_000_000_000:.2f}B"
    elif volume >= 1_000_000:
        vol_str = f"{volume/1_000_000:.2f}M"
    else:
        vol_str = f"{volume:,.0f}"
    
    timestamp = datetime.utcnow().strftime("%H:%M UTC")
    
    message = f"""
{'🪙' if symbol in ['BTC', 'ETH'] else '💰'} <b>{symbol}/USDT</b>
━━━━━━━━━━━━━━━━━━━━

💵 Price:  <b>${price:,.2f}</b>
{change_emoji} Change: <b>{change_arrow} {abs(change):.2f}%</b>
━━━━━━━━━━━━━━━━━━━━

📈 High:   <code>${high:,.2f}</code>
📉 Low:    <code>${low:,.2f}</code>
📊 Volume: <code>{vol_str}</code>
━━━━━━━━━━━━━━━━━━━━

🕐 Updated: {timestamp}
🚀 Gate.io Futures
"""
    return message

def format_analysis_message(data: dict, symbol: str, interval: str) -> str:
    """Format detailed analysis message with indicators"""
    d = data["data"]
    ind = d["indicators"]
    sr = d["supportResistance"]
    pa = d["priceAction"]
    vol = d["volume"]
    depth = d["marketDepth"]
    
    price = d["price"]["current"]
    funding = d["price"]["lastFundingRate"]
    
    # EMA Analysis
    ema = ind["ema"]
    ema_emoji = "🟢" if ema["trend"] == "BULLISH" else "🔴" if ema["trend"] == "BEARISH" else "🟡"
    
    # MACD Analysis  
    macd = ind["macd"]
    macd_emoji = "🟢" if macd["trend"] == "BULLISH" else "🔴"
    
    # RSI Analysis
    rsi = ind["rsi"]
    rsi_val = rsi["value"]
    if rsi_val > 70:
        rsi_emoji = "🔴"
        rsi_status = "Overbought"
    elif rsi_val < 30:
        rsi_emoji = "🟢"
        rsi_status = "Oversold"
    else:
        rsi_emoji = "🟡"
        rsi_status = "Neutral"
    
    # Bollinger Bands
    bb = ind["bollingerBands"]
    bb_emoji = "🟢" if bb["position"] == "OVERSOLD" else "🔴" if bb["position"] == "OVERBOUGHT" else "🟡"
    
    # SAR
    sar = ind["sar"]
    sar_emoji = "🟢" if sar["trend"] == "BULLISH" else "🔴"
    
    # Volume
    vol_emoji = "🟢" if vol["signal"] == "HIGH" else "🔴" if vol["signal"] == "LOW" else "🟡"
    
    # Market Depth
    depth_emoji = "🟢" if depth["marketPressure"] == "BUYING" else "🔴" if depth["marketPressure"] == "SELLING" else "🟡"
    
    # Overall Signal
    bullish_count = sum([
        ema["trend"] == "BULLISH",
        macd["trend"] == "BULLISH",
        rsi_val < 50,
        sar["trend"] == "BULLISH",
        bb["position"] == "OVERSOLD"
    ])
    
    if bullish_count >= 4:
        overall = "🟢 STRONG BUY"
    elif bullish_count >= 3:
        overall = "🟢 BUY"
    elif bullish_count <= 1:
        overall = "🔴 STRONG SELL"
    elif bullish_count <= 2:
        overall = "🔴 SELL"
    else:
        overall = "🟡 NEUTRAL"
    
    # Support/Resistance
    supports = sr.get("support", [])[:3]
    resistances = sr.get("resistance", [])[:3]
    
    support_str = " | ".join([f"${s:,.2f}" for s in supports]) if supports else "N/A"
    resistance_str = " | ".join([f"${r:,.2f}" for r in resistances]) if resistances else "N/A"
    
    message = f"""
📊 <b>{symbol}/USDT Analysis ({interval})</b>
━━━━━━━━━━━━━━━━━━━━━━━━

💵 <b>Price: ${price:,.2f}</b>
💰 Funding: {funding:.6f}

━━━ 📈 TREND INDICATORS ━━━

{ema_emoji} <b>EMA Trend:</b> {ema['trend']}
   ├ EMA 9:  <code>${ema['ema9']:,.2f}</code>
   ├ EMA 21: <code>${ema['ema21']:,.2f}</code>
   └ EMA 50: <code>${ema['ema50']:,.2f}</code>

{macd_emoji} <b>MACD:</b> {macd['trend']}
   ├ Line:   <code>{macd['line']:.2f}</code>
   ├ Signal: <code>{macd['signal']:.2f}</code>
   └ Hist:   <code>{macd['histogram']:.2f}</code>

{sar_emoji} <b>SAR:</b> {sar['trend']}
   └ Value:  <code>${sar['value']:,.2f}</code>

━━━ 📉 MOMENTUM ━━━

{rsi_emoji} <b>RSI:</b> {rsi_val:.1f} ({rsi_status})
   └ Signal: {rsi['signal']}

{bb_emoji} <b>Bollinger:</b> {bb['position']}
   ├ Upper:  <code>${bb['upper']:,.2f}</code>
   ├ Middle: <code>${bb['middle']:,.2f}</code>
   └ Lower:  <code>${bb['lower']:,.2f}</code>

━━━ 📊 VOLUME & DEPTH ━━━

{vol_emoji} <b>Volume:</b> {vol['signal']}
   └ Ratio: {vol['ratio']}x avg

{depth_emoji} <b>Market Pressure:</b> {depth['marketPressure']}
   └ Imbalance: {depth['imbalance']}

━━━ 🎯 SUPPORT/RESISTANCE ━━━

🟢 <b>Support:</b>
   └ {support_str}

🔴 <b>Resistance:</b>
   └ {resistance_str}

━━━ 📍 PRICE ACTION ━━━

📌 Pattern: <b>{pa['pattern']}</b>
📈 Trend: {pa['trend']}
💪 Strength: {pa['strength']}%

━━━━━━━━━━━━━━━━━━━━━━━━

🎯 <b>OVERALL SIGNAL: {overall}</b>

━━━━━━━━━━━━━━━━━━━━━━━━
🕐 {datetime.utcnow().strftime("%H:%M:%S UTC")}
"""
    return message

def format_alerts_message(alerts: list) -> str:
    """Format user's active alerts"""
    if not alerts:
        return """
📋 <b>My Active Alerts</b>
━━━━━━━━━━━━━━━━━━━━

❌ No active alerts

Use <b>Set TP</b> or <b>Set SL</b> buttons to create alerts!
"""
    
    message = """
📋 <b>My Active Alerts</b>
━━━━━━━━━━━━━━━━━━━━

"""
    for i, alert in enumerate(alerts, 1):
        alert_type = "🎯 TP" if alert["type"] == "TP" else "🛑 SL"
        message += f"""
<b>{i}. {alert['symbol']}/USDT</b>
   {alert_type}: <code>${alert['price']:,.2f}</code>
   Entry: <code>${alert['entry']:,.2f}</code>
   
"""
    
    message += f"\n━━━━━━━━━━━━━━━━━━━━\n📊 Total: {len(alerts)} alerts"
    return message

# ============== KEYBOARDS ==============
def get_main_keyboard(symbol: str = "BTC") -> InlineKeyboardMarkup:
    """Get main keyboard with all buttons"""
    keyboard = [
        [
            InlineKeyboardButton("📊 15m", callback_data=f"tf_{symbol}_15m"),
            InlineKeyboardButton("📊 1H", callback_data=f"tf_{symbol}_1h"),
            InlineKeyboardButton("📊 4H", callback_data=f"tf_{symbol}_4h"),
            InlineKeyboardButton("💵 Price", callback_data=f"price_{symbol}"),
        ],
        [
            InlineKeyboardButton("🔬 Analyse", callback_data=f"analyse_{symbol}_1h"),
        ],
        [
            InlineKeyboardButton("🎯 Set TP", callback_data=f"settp_{symbol}"),
            InlineKeyboardButton("🛑 Set SL", callback_data=f"setsl_{symbol}"),
        ],
        [
            InlineKeyboardButton("📋 My Alerts", callback_data="myalerts"),
            InlineKeyboardButton("🔄 Refresh", callback_data=f"refresh_{symbol}"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)

def get_symbol_keyboard() -> InlineKeyboardMarkup:
    """Get symbol selection keyboard"""
    keyboard = []
    row = []
    for i, symbol in enumerate(SYMBOLS):
        emoji = "🪙" if symbol in ["BTC", "ETH"] else "💰"
        row.append(InlineKeyboardButton(f"{emoji} {symbol}", callback_data=f"symbol_{symbol}"))
        if len(row) == 4:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    return InlineKeyboardMarkup(keyboard)

def get_cancel_keyboard() -> InlineKeyboardMarkup:
    """Cancel keyboard"""
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data="cancel")
    ]])

# ============== HANDLERS ==============
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command"""
    welcome = """
🚀 <b>Crypto Futures Bot</b>
━━━━━━━━━━━━━━━━━━━━

Welcome! I provide real-time crypto futures data with technical analysis.

<b>Commands:</b>
/p [symbol] - Get price (e.g., /p btc)
/a [symbol] - Full analysis
/alerts - View your alerts

<b>Features:</b>
• Live futures prices
• Technical indicators (EMA, MACD, RSI, SAR, Bollinger)
• Support/Resistance levels
• Set TP/SL for practice trading
• Auto notifications when TP/SL hit

━━━━━━━━━━━━━━━━━━━━

Select a coin below to start:
"""
    await update.message.reply_text(
        welcome, 
        parse_mode="HTML",
        reply_markup=get_symbol_keyboard()
    )

async def price_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /p command"""
    if context.args:
        symbol = context.args[0].upper()
        if symbol not in SYMBOLS:
            await update.message.reply_text(f"❌ Symbol not supported. Use: {', '.join(SYMBOLS)}")
            return
    else:
        symbol = "BTC"
    
    msg = await update.message.reply_text("⏳ Fetching data...")
    
    data = fetch_crypto_data(symbol)
    if data.get("success"):
        await msg.edit_text(
            format_price_message(data, symbol),
            parse_mode="HTML",
            reply_markup=get_main_keyboard(symbol)
        )
    else:
        await msg.edit_text(f"❌ Error: {data.get('error', 'Unknown error')}")

async def analyse_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /a command"""
    if context.args:
        symbol = context.args[0].upper()
        if symbol not in SYMBOLS:
            await update.message.reply_text(f"❌ Symbol not supported. Use: {', '.join(SYMBOLS)}")
            return
    else:
        symbol = "BTC"
    
    msg = await update.message.reply_text("⏳ Analysing...")
    
    data = fetch_crypto_data(symbol, "1h")
    if data.get("success"):
        await msg.edit_text(
            format_analysis_message(data, symbol, "1H"),
            parse_mode="HTML",
            reply_markup=get_main_keyboard(symbol)
        )
    else:
        await msg.edit_text(f"❌ Error: {data.get('error', 'Unknown error')}")

async def alerts_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /alerts command"""
    user_id = update.effective_user.id
    alerts = user_alerts.get(user_id, [])
    await update.message.reply_text(
        format_alerts_message(alerts),
        parse_mode="HTML"
    )

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle button callbacks"""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    user_id = update.effective_user.id
    
    # Symbol selection
    if data.startswith("symbol_"):
        symbol = data.split("_")[1]
        msg = await query.edit_message_text("⏳ Fetching data...")
        api_data = fetch_crypto_data(symbol)
        if api_data.get("success"):
            await query.edit_message_text(
                format_price_message(api_data, symbol),
                parse_mode="HTML",
                reply_markup=get_main_keyboard(symbol)
            )
        else:
            await query.edit_message_text(f"❌ Error: {api_data.get('error')}")
    
    # Timeframe change
    elif data.startswith("tf_"):
        parts = data.split("_")
        symbol = parts[1]
        interval = parts[2]
        
        api_data = fetch_crypto_data(symbol, interval)
        if api_data.get("success"):
            await query.edit_message_text(
                format_analysis_message(api_data, symbol, interval.upper()),
                parse_mode="HTML",
                reply_markup=get_main_keyboard(symbol)
            )
    
    # Price refresh
    elif data.startswith("price_") or data.startswith("refresh_"):
        symbol = data.split("_")[1]
        api_data = fetch_crypto_data(symbol)
        if api_data.get("success"):
            await query.edit_message_text(
                format_price_message(api_data, symbol),
                parse_mode="HTML",
                reply_markup=get_main_keyboard(symbol)
            )
    
    # Analysis
    elif data.startswith("analyse_"):
        parts = data.split("_")
        symbol = parts[1]
        interval = parts[2] if len(parts) > 2 else "1h"
        
        api_data = fetch_crypto_data(symbol, interval)
        if api_data.get("success"):
            await query.edit_message_text(
                format_analysis_message(api_data, symbol, interval.upper()),
                parse_mode="HTML",
                reply_markup=get_main_keyboard(symbol)
            )
    
    # Set TP
    elif data.startswith("settp_"):
        symbol = data.split("_")[1]
        current_price = get_current_price(symbol)
        context.user_data["pending_alert"] = {
            "symbol": symbol,
            "type": "TP",
            "entry": current_price
        }
        await query.edit_message_text(
            f"""
🎯 <b>Set Take Profit - {symbol}/USDT</b>
━━━━━━━━━━━━━━━━━━━━

Current Price: <code>${current_price:,.2f}</code>

📝 Enter your TP price:
(Just type the number, e.g., 75000)
""",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )
    
    # Set SL
    elif data.startswith("setsl_"):
        symbol = data.split("_")[1]
        current_price = get_current_price(symbol)
        context.user_data["pending_alert"] = {
            "symbol": symbol,
            "type": "SL",
            "entry": current_price
        }
        await query.edit_message_text(
            f"""
🛑 <b>Set Stop Loss - {symbol}/USDT</b>
━━━━━━━━━━━━━━━━━━━━

Current Price: <code>${current_price:,.2f}</code>

📝 Enter your SL price:
(Just type the number, e.g., 68000)
""",
            parse_mode="HTML",
            reply_markup=get_cancel_keyboard()
        )
    
    # My Alerts
    elif data == "myalerts":
        alerts = user_alerts.get(user_id, [])
        await query.edit_message_text(
            format_alerts_message(alerts),
            parse_mode="HTML",
            reply_markup=get_symbol_keyboard()
        )
    
    # Cancel
    elif data == "cancel":
        context.user_data.pop("pending_alert", None)
        await query.edit_message_text(
            "❌ Cancelled. Select a coin:",
            reply_markup=get_symbol_keyboard()
        )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages for TP/SL input"""
    user_id = update.effective_user.id
    
    # Check if user has pending alert
    pending = context.user_data.get("pending_alert")
    if not pending:
        # If no pending alert, treat as price query
        text = update.message.text.upper().strip()
        if text in SYMBOLS:
            data = fetch_crypto_data(text)
            if data.get("success"):
                await update.message.reply_text(
                    format_price_message(data, text),
                    parse_mode="HTML",
                    reply_markup=get_main_keyboard(text)
                )
        return
    
    # Parse price
    try:
        price = float(update.message.text.replace(",", "").replace("$", ""))
    except ValueError:
        await update.message.reply_text("❌ Invalid price. Please enter a number.")
        return
    
    # Create alert
    alert = {
        "symbol": pending["symbol"],
        "type": pending["type"],
        "price": price,
        "entry": pending["entry"]
    }
    
    if user_id not in user_alerts:
        user_alerts[user_id] = []
    user_alerts[user_id].append(alert)
    
    # Clear pending
    context.user_data.pop("pending_alert", None)
    
    alert_type = "Take Profit 🎯" if alert["type"] == "TP" else "Stop Loss 🛑"
    direction = "above" if alert["type"] == "TP" else "below"
    
    await update.message.reply_text(
        f"""
✅ <b>Alert Created!</b>
━━━━━━━━━━━━━━━━━━━━

📌 Symbol: <b>{alert['symbol']}/USDT</b>
📍 Type: <b>{alert_type}</b>
💰 Entry: <code>${alert['entry']:,.2f}</code>
🎯 Target: <code>${alert['price']:,.2f}</code>

I'll notify you when price goes {direction} ${price:,.2f}

Good luck! 🍀
""",
        parse_mode="HTML",
        reply_markup=get_symbol_keyboard()
    )

# ============== PRICE MONITOR ==============
async def check_alerts(context: ContextTypes.DEFAULT_TYPE):
    """Background task to check TP/SL alerts"""
    import random
    
    for user_id, alerts in list(user_alerts.items()):
        alerts_to_remove = []
        
        for i, alert in enumerate(alerts):
            current_price = get_current_price(alert["symbol"])
            if current_price == 0:
                continue
            
            hit = False
            is_tp = alert["type"] == "TP"
            
            # Check if TP/SL hit
            if is_tp and current_price >= alert["price"]:
                hit = True
            elif not is_tp and current_price <= alert["price"]:
                hit = True
            
            if hit:
                alerts_to_remove.append(i)
                
                if is_tp:
                    # TP Hit - Congratulations!
                    profit_pct = ((alert["price"] - alert["entry"]) / alert["entry"]) * 100
                    message = f"""
🎉🎉🎉 <b>TAKE PROFIT HIT!</b> 🎉🎉🎉
━━━━━━━━━━━━━━━━━━━━━━━━

🏆 <b>CONGRATULATIONS!</b> 🏆

📌 Symbol: <b>{alert['symbol']}/USDT</b>
💰 Entry: <code>${alert['entry']:,.2f}</code>
🎯 TP: <code>${alert['price']:,.2f}</code>
📈 Current: <code>${current_price:,.2f}</code>

💵 Profit: <b>+{profit_pct:.2f}%</b>

━━━━━━━━━━━━━━━━━━━━━━━━

👏 Great prediction! You're on fire! 🔥

Keep up the good work! 💪
"""
                else:
                    # SL Hit - Sad with motivation
                    loss_pct = ((alert["entry"] - alert["price"]) / alert["entry"]) * 100
                    quote = random.choice(MOTIVATIONAL_QUOTES)
                    message = f"""
😢 <b>STOP LOSS HIT</b>
━━━━━━━━━━━━━━━━━━━━━━━━

📌 Symbol: <b>{alert['symbol']}/USDT</b>
💰 Entry: <code>${alert['entry']:,.2f}</code>
🛑 SL: <code>${alert['price']:,.2f}</code>
📉 Current: <code>${current_price:,.2f}</code>

📊 Loss: <b>-{loss_pct:.2f}%</b>

━━━━━━━━━━━━━━━━━━━━━━━━

💪 <i>"{quote}"</i>

Don't give up! Analyse what went wrong 
and come back stronger! 📈
"""
                
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=message,
                        parse_mode="HTML"
                    )
                except Exception as e:
                    print(f"Error sending alert to {user_id}: {e}")
        
        # Remove triggered alerts (in reverse order)
        for i in sorted(alerts_to_remove, reverse=True):
            user_alerts[user_id].pop(i)

# ============== MAIN ==============
def main():
    """Start the bot"""
    print("🚀 Starting Crypto Futures Bot...")
    
    # Create application
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Add handlers
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("p", price_command))
    app.add_handler(CommandHandler("pp", price_command))  # Alias
    app.add_handler(CommandHandler("a", analyse_command))
    app.add_handler(CommandHandler("analyse", analyse_command))
    app.add_handler(CommandHandler("alerts", alerts_command))
    app.add_handler(CallbackQueryHandler(button_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Add job to check alerts every 30 seconds
    app.job_queue.run_repeating(check_alerts, interval=30, first=10)
    
    print("✅ Bot is running!")
    print("📱 Open Telegram and message your bot")
    
    # Start polling
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
