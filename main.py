import ccxt
import pandas as pd
import pandas_ta as ta
import telegram
import asyncio
from datetime import datetime
from pytz import timezone

# === CONFIG ===
TELEGRAM_TOKEN = '7896887265:AAFIMdZPyqWsd-EGjJcuABvxzeIwMOy0qYA'
CHAT_ID = '7717950904'

bot = telegram.Bot(token=TELEGRAM_TOKEN)
exchange = ccxt.binance({'enableRateLimit': True})

TIMEFRAMES = ['5m', '15m']
CANDLE_LIMIT = 150
LOOKBACK = 20
EXCLUDED_KEYWORDS = ['USDC', 'USDP', 'TUSD', 'XUSD', 'BUSD', 'DAI', 'USDN', 'FDUSD', 'LUSD', 'SUSD', 'EUR', 'PAX']

sent_signals = {}

def get_top_symbols():
    print("🔄 Fetching top USDT pairs...")
    markets = exchange.load_markets()
    tickers = exchange.fetch_tickers()

    pairs = []
    for symbol, ticker in tickers.items():
        if symbol.endswith('/USDT') and symbol in markets:
            market = markets[symbol]
            if market['spot'] and market.get('active', True):
                base = symbol.split('/')[0]
                if any(x in base.upper() for x in EXCLUDED_KEYWORDS):
                    continue
                volume = ticker.get('quoteVolume', 0)
                pairs.append((symbol, volume))

    sorted_pairs = sorted(pairs, key=lambda x: x[1], reverse=True)[:300]
    return [symbol for symbol, _ in sorted_pairs]

def fetch_ohlcv(symbol, tf):
    try:
        ohlcv = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=CANDLE_LIMIT)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    except Exception as e:
        print(f"❌ Error fetching {symbol} {tf}: {e}")
        return None

def confirm_two_closes(df, index, direction):
    try:
        if direction == "LONG":
            return df['close'].iloc[index + 1] > df['ema100'].iloc[index + 1] and df['close'].iloc[index + 2] > df['ema100'].iloc[index + 2]
        elif direction == "SHORT":
            return df['close'].iloc[index + 1] < df['ema100'].iloc[index + 1] and df['close'].iloc[index + 2] < df['ema100'].iloc[index + 2]
    except IndexError:
        return False
    return False

def ema_cross_signal(df, symbol, tf):
    df['ema9'] = ta.ema(df['close'], length=9)
    df['ema21'] = ta.ema(df['close'], length=21)
    df['ema100'] = ta.ema(df['close'], length=100)
    if len(df) < 110:
        return None
    entry = (df['high'].iloc[-2] + df['low'].iloc[-2]) / 2
    for i in range(-6, -2):
        p, c = i - 1, i
        if pd.isna(df['ema100'].iloc[c]):
            continue
        if df['ema9'].iloc[p] < df['ema100'].iloc[p] and df['ema9'].iloc[c] > df['ema100'].iloc[c]:
            if df['ema21'].iloc[p] < df['ema100'].iloc[p] and df['ema21'].iloc[c] > df['ema100'].iloc[c]:
                if not confirm_two_closes(df, c, "LONG") or entry < df['ema100'].iloc[c]:
                    continue
                return "LONG", "✅ STRONG LONG: EMA 9 & 21 crossed above EMA 100"
            if not confirm_two_closes(df, c, "LONG") or entry < df['ema100'].iloc[c]:
                continue
            return "LONG", "🟢 LONG: EMA 9 crossed above EMA 100"
        if df['ema9'].iloc[p] > df['ema100'].iloc[p] and df['ema9'].iloc[c] < df['ema100'].iloc[c]:
            if df['ema21'].iloc[p] > df['ema100'].iloc[p] and df['ema21'].iloc[c] < df['ema100'].iloc[c]:
                if not confirm_two_closes(df, c, "SHORT") or entry > df['ema100'].iloc[c]:
                    continue
                return "SHORT", "❌ STRONG SHORT: EMA 9 & 21 crossed below EMA 100"
            if not confirm_two_closes(df, c, "SHORT") or entry > df['ema100'].iloc[c]:
                continue
            return "SHORT", "🔴 SHORT: EMA 9 crossed below EMA 100"
    return None

def breakout_signal(df, symbol, tf):
    if len(df) < LOOKBACK + 3:
        return None
    recent_high = df['high'].iloc[-LOOKBACK-2:-2].max()
    recent_low = df['low'].iloc[-LOOKBACK-2:-2].min()
    c0, c1, c2 = df['close'].iloc[-3], df['close'].iloc[-2], df['close'].iloc[-1]
    entry = (df['high'].iloc[-2] + df['low'].iloc[-2]) / 2
    if df['high'].iloc[-3] <= recent_high and df['high'].iloc[-2] > recent_high:
        if c1 > recent_high and c2 > recent_high:
            return "LONG", entry, "🚀 Bullish breakout"
    if df['low'].iloc[-3] >= recent_low and df['low'].iloc[-2] < recent_low:
        if c1 < recent_low and c2 < recent_low:
            return "SHORT", entry, "🔻 Bearish breakout"
    return None

async def send_signal(symbol, tf, entry, text):
    timestamp = datetime.now(timezone('Asia/Karachi')).strftime('%Y-%m-%d %I:%M:%S %p')
    message = (
        f"🇵🇰 Technical Bethak\n"
        f"🕒 Time: {timestamp} (PKT)\n"
        f"📊 Signal Detected on {symbol} [{tf}]\n"
        f"💰 Entry Price: {entry:.6f}\n"
        f"📈 Details: {text}\n\n"
        f"📌 DYOR. Bot generated signal. Don't use if you have very little chart knowledge."
    )
    print(f"📤 {symbol} | {tf} | {text} | Entry: {entry:.8f}")
    try:
        await bot.send_message(chat_id=CHAT_ID, text=message)
    except Exception as e:
        print(f"❌ Telegram Error: {e}")

async def main():
    symbols = get_top_symbols()
    print(f"✅ Scanning {len(symbols)} pairs...")
    while True:
        for symbol in symbols:
            for tf in TIMEFRAMES:
                df = fetch_ohlcv(symbol, tf)
                if df is None:
                    continue
                ema_result = ema_cross_signal(df, symbol, tf)
                breakout_result = breakout_signal(df, symbol, tf)
                if ema_result and breakout_result:
                    ema_dir, ema_text = ema_result
                    bo_dir, entry, bo_text = breakout_result
                    if ema_dir == bo_dir:
                        key = f"{symbol}_{tf}_{ema_dir}"
                        if key not in sent_signals:
                            sent_signals[key] = datetime.now()
                            await send_signal(symbol, tf, entry, f"{bo_text} + {ema_text}")
        print("🔁 Scan cycle complete. Restarting...\n")

if __name__ == "__main__":
    asyncio.run(main())