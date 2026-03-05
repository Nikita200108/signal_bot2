import os
import ccxt
import pandas as pd
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

# --- КОНФИГУРАЦИЯ ---
TOKEN = os.getenv("TELEGRAM_TOKEN")
TOP_COINS = [
    'BTC/USDT', 'ETH/USDT', 'SOL/USDT', 'BNB/USDT', 'XRP/USDT',
    'ADA/USDT', 'AVAX/USDT', 'DOT/USDT', 'DOGE/USDT', 'LINK/USDT',
    'MATIC/USDT', 'NEAR/USDT', 'LTC/USDT', 'UNI/USDT', 'APT/USDT'
]

active_users = set()
last_alerts = {} 

# --- БЛОК АНАЛИЗА ---

def find_strong_levels(df):
    """Поиск сильных исторических уровней (фракталы 5 свечей)"""
    levels = []
    for i in range(5, len(df) - 5):
        if df['high'][i] == df['high'][i-5:i+6].max():
            levels.append({'price': df['high'][i], 'type': 'Resistance'})
        elif df['low'][i] == df['low'][i-5:i+6].min():
            levels.append({'price': df['low'][i], 'type': 'Support'})
    return levels

def get_level_strength(price, df):
    """Считает количество касаний уровня в зоне 0.5%"""
    hits = 0
    for i in range(len(df)):
        if abs(df['high'][i] - price) / price <= 0.005 or abs(df['low'][i] - price) / price <= 0.005:
            hits += 1
    return hits

async def get_btc_context(ex):
    """Определяет краткосрочный тренд BTC (1 час)"""
    try:
        btc_bars = ex.fetch_ohlcv('BTC/USDT', timeframe='1h', limit=2)
        return "📈 UP" if btc_bars[-1][4] > btc_bars[0][4] else "📉 DOWN"
    except:
        return "---"

def check_shadow_confirmation(df, side):
    """Анализ тени последней закрытой свечи"""
    last = df.iloc[-2] # Берем последнюю закрытую свечу
    body = abs(last['close'] - last['open'])
    if side == "LONG":
        tail = min(last['open'], last['close']) - last['low']
        return tail > body * 1.2
    else:
        tail = last['high'] - max(last['open'], last['close'])
        return tail > body * 1.2

# --- ОСНОВНОЙ ЦИКЛ ---

async def monitor_market(context: ContextTypes.DEFAULT_TYPE):
    ex = ccxt.binance({'enableRateLimit': True})
    print("Радар уровней 4H/1D запущен...")
    
    while True:
        btc_status = await get_btc_context(ex)
        
        for symbol in TOP_COINS:
            try:
                # Получаем данные 4H
                bars = ex.fetch_ohlcv(symbol, timeframe='4h', limit=150)
                df = pd.DataFrame(bars, columns=['time', 'open', 'high', 'low', 'close', 'vol'])
                
                current_price = df['close'].iloc[-1]
                avg_vol = df['vol'].tail(100).mean()
                rel_vol = df['vol'].iloc[-1] / avg_vol
                levels = find_strong_levels(df)
                
                for lvl in levels:
                    level_price = lvl['price']
                    diff = abs(current_price - level_price) / current_price
                    alert_key = (symbol, level_price)

                    # 1. ЗОНА ВНИМАНИЯ (1.5%)
                    if 0.005 < diff <= 0.015:
                        if alert_key not in last_alerts:
                            strength = get_level_strength(level_price, df)
                            msg = (f"👀 **ВНИМАНИЕ: {symbol}**\n"
                                   f"Подход к уровню: `{level_price}`\n"
                                   f"Дистанция: `{diff*100:.2f}%`\n"
                                   f"🛡 Сила: {strength} касаний\n\n"
                                   f"⛓ Контекст BTC: {btc_status}\n"
                                   f"⏳ Готовься к сделке.")
                            await broadcast(context, msg)
                            last_alerts[alert_key] = 'pre'

                    # 2. ЗОНА ВХОДА (0.5%)
                    elif diff <= 0.005:
                        if last_alerts.get(alert_key) != 'entry':
                            side = "LONG (Отскок)" if current_price >= level_price else "SHORT (Отскок)"
                            tp = current_price * 1.04 if "LONG" in side else current_price * 0.96
                            sl = level_price * 0.992 if "LONG" in side else level_price * 1.008
                            
                            vol_info = "🚀 ВЫСОКИЙ (Риск пробоя!)" if rel_vol > 1.8 else "✅ НОРМА"
                            
                            msg = (f"🎯 **СИГНАЛ ВХОДА: {symbol}**\n"
                                   f"Уровень: `{level_price}`\n"
                                   f"Текущая цена: `{current_price}`\n\n"
                                   f"📊 Объем (100): {rel_vol:.2f}x ({vol_info})\n"
                                   f"⛓ Контекст BTC: {btc_status}\n\n"
                                   f"✅ **Вход:** `{current_price}`\n"
                                   f"🎯 **TP:** `{tp:.4f}`\n"
                                   f"🛑 **SL:** `{sl:.4f}`\n\n"
                                   f"💡 *Если BTC {btc_status} идет против тебя — пропусти сделку!*")
                            
                            await broadcast(context, msg)
                            last_alerts[alert_key] = 'entry'

                    # 3. ПОДТВЕРЖДЕНИЕ ТЕНЬЮ (после касания)
                    if last_alerts.get(alert_key) == 'entry':
                        if check_shadow_confirmation(df, "LONG" if current_price >= level_price else "SHORT"):
                            msg = (f"🕯 **ПОДТВЕРЖДЕНО: {symbol}**\n"
                                   f"На уровне `{level_price}` появилась тень.\n"
                                   f"Защита уровня подтверждена. Держим позицию.")
                            await broadcast(context, msg)
                            last_alerts[alert_key] = 'confirmed'

                    # Сброс алерта при уходе цены
                    elif diff > 0.04:
                        last_alerts.pop(alert_key, None)

                await asyncio.sleep(2) # Защита от бана API
            except Exception as e:
                print(f"Ошибка {symbol}: {e}")
                await asyncio.sleep(5)
        
        await asyncio.sleep(60) # Пауза между кругами мониторинга

async def broadcast(context, text):
    for user_id in active_users:
        try: await context.bot.send_message(chat_id=user_id, text=text, parse_mode='Markdown')
        except: pass

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    active_users.add(update.effective_user.id)
    await update.message.reply_text("💎 **Снайпер 4H/1D активирован!**\n\nЯ анализирую уровни, объемы, тени и BTC.")

if __name__ == '__main__':
    app = ApplicationBuilder().token(TOKEN).build()
    app.job_queue.run_once(monitor_market, when=0)
    app.add_handler(CommandHandler("start", start))
    app.run_polling()