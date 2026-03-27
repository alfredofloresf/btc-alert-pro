import os
import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import requests
from telegram import Bot


# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = "@btcalertademo"

CHECK_INTERVAL = 30
REQUEST_TIMEOUT = 10

# Signal cooldown
SIGNAL_COOLDOWN_SECONDS = 1800  # 30 minutes


# =========================
# STATE
# =========================
price_history = []
last_signal_time = 0
last_signal_sent = None


# =========================
# HELPERS
# =========================
def now_ts():
    return datetime.now().timestamp()


def current_time():
    ny_time = datetime.now(ZoneInfo("America/New_York"))
    return ny_time.strftime("%I:%M %p ET")


def format_price(price):
    return f"${price:,.2f}"


def get_btc_price():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        if "bitcoin" in data and "usd" in data["bitcoin"]:
            return float(data["bitcoin"]["usd"])

        print("Unexpected BTC API response:", data)
        return None

    except Exception as e:
        print("BTC API Error:", e)
        return None


def get_historical_prices(days):
    url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
    params = {
        "vs_currency": "usd",
        "days": days,
        "interval": "daily"
    }

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        prices = [item[1] for item in data.get("prices", [])]
        return prices if prices else None

    except Exception as e:
        print(f"Historical prices error ({days}d):", e)
        return None


def get_top_movers():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "price_change_percentage_24h_desc",
        "per_page": 10,
        "page": 1,
    }

    try:
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        valid_data = [
            coin for coin in data
            if coin.get("price_change_percentage_24h") is not None
        ]

        if not valid_data:
            return None, None

        gainers = sorted(
            valid_data,
            key=lambda x: x["price_change_percentage_24h"],
            reverse=True
        )[:3]

        losers = sorted(
            valid_data,
            key=lambda x: x["price_change_percentage_24h"]
        )[:1]

        return gainers, losers

    except Exception as e:
        print("Top movers error:", e)
        return None, None


def get_fear_and_greed():
    url = "https://api.alternative.me/fng/?limit=1"

    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()

        items = data.get("data", [])
        if not items:
            return None, None

        value = int(items[0]["value"])
        classification_en = items[0]["value_classification"]

        translation_map = {
            "Extreme Fear": "Miedo extremo",
            "Fear": "Miedo",
            "Neutral": "Neutral",
            "Greed": "Codicia",
            "Extreme Greed": "Codicia extrema",
        }

        classification_es = translation_map.get(classification_en, classification_en)
        return value, classification_es

    except Exception as e:
        print("Fear & Greed error:", e)
        return None, None


def add_price_to_history(price):
    global price_history

    ts = now_ts()
    price_history.append((ts, price))

    # keep only the last 20 minutes of short-term prices
    cutoff = ts - (20 * 60)
    price_history = [(t, p) for (t, p) in price_history if t >= cutoff]


def get_price_ago(seconds_ago):
    if not price_history:
        return None

    target = now_ts() - seconds_ago
    closest = min(price_history, key=lambda x: abs(x[0] - target))
    return closest[1]


def percent_change(current_price, previous_price):
    if previous_price is None or previous_price == 0:
        return None
    return ((current_price - previous_price) / previous_price) * 100


def can_send_signal():
    return (now_ts() - last_signal_time) >= SIGNAL_COOLDOWN_SECONDS


def classify_trend(prices):
    if not prices or len(prices) < 2:
        return "Neutral"

    first_price = prices[0]
    last_price = prices[-1]
    change = percent_change(last_price, first_price)

    if change is None:
        return "Neutral"
    if change > 5:
        return "Alcista"
    if change < -5:
        return "Bajista"
    return "Neutral"


def calculate_ema(prices, period=200):
    if not prices or len(prices) < period:
        return None

    k = 2 / (period + 1)
    ema = sum(prices[:period]) / period

    for price in prices[period:]:
        ema = (price * k) + (ema * (1 - k))

    return ema


def get_macro_context():
    prices_30d = get_historical_prices(30)
    prices_1y = get_historical_prices(365)

    if not prices_30d or not prices_1y:
        return None

    trend_30d = classify_trend(prices_30d)
    trend_1y = classify_trend(prices_1y)

    ema_200 = calculate_ema(prices_1y, 200)
    current_daily_price = prices_1y[-1]

    if ema_200 is None:
        ema_status = "Neutral"
    elif current_daily_price > ema_200:
        ema_status = "Precio arriba"
    elif current_daily_price < ema_200:
        ema_status = "Precio abajo"
    else:
        ema_status = "Neutral"

    return {
        "trend_30d": trend_30d,
        "trend_1y": trend_1y,
        "ema_200": ema_200,
        "ema_status": ema_status,
        "current_daily_price": current_daily_price,
    }


def evaluate_short_term_bias(current_price):
    """
    Uses 1m, 5m, and 15m price direction only for internal scoring.
    No visible momentum alerts are sent.
    """
    price_1m = get_price_ago(60)
    price_5m = get_price_ago(300)
    price_15m = get_price_ago(900)

    change_1m = percent_change(current_price, price_1m)
    change_5m = percent_change(current_price, price_5m)
    change_15m = percent_change(current_price, price_15m)

    if change_1m is None or change_5m is None or change_15m is None:
        return None

    bullish = sum([
        change_1m > 0,
        change_5m > 0,
        change_15m > 0,
    ])

    bearish = sum([
        change_1m < 0,
        change_5m < 0,
        change_15m < 0,
    ])

    if bullish >= 2:
        bias = "Alcista"
    elif bearish >= 2:
        bias = "Bajista"
    else:
        bias = "Neutral"

    return {
        "bias": bias,
        "change_1m": change_1m,
        "change_5m": change_5m,
        "change_15m": change_15m,
    }


def build_score(short_bias, trend_30d, trend_1y, ema_status):
    bullish_score = 0
    bearish_score = 0

    if short_bias == "Alcista":
        bullish_score += 1
    elif short_bias == "Bajista":
        bearish_score += 1

    if trend_30d == "Alcista":
        bullish_score += 2
    elif trend_30d == "Bajista":
        bearish_score += 2

    if trend_1y == "Alcista":
        bullish_score += 3
    elif trend_1y == "Bajista":
        bearish_score += 3

    if ema_status == "Precio arriba":
        bullish_score += 2
    elif ema_status == "Precio abajo":
        bearish_score += 2

    return bullish_score, bearish_score


def decide_signal(short_bias, trend_30d, trend_1y, ema_status):
    bullish_score, bearish_score = build_score(short_bias, trend_30d, trend_1y, ema_status)

    if bullish_score >= 5 and bullish_score > bearish_score:
        return "BUY", min(10, 5 + (bullish_score - bearish_score))
    elif bearish_score >= 5 and bearish_score > bullish_score:
        return "SELL", min(10, 5 + (bearish_score - bullish_score))
    else:
        return "WAIT", 6


def build_conclusion(decision, short_bias, trend_30d, trend_1y, ema_status):
    if decision == "BUY":
        if trend_1y == "Alcista" and trend_30d == "Alcista":
            return "Tendencia fuerte alcista en marcos amplios."
        if ema_status == "Precio arriba":
            return "El precio sigue por encima de la EMA 200, con sesgo positivo."
        return "Hay alineación suficiente al alza para considerar compra."

    if decision == "SELL":
        if trend_1y == "Bajista" and trend_30d == "Bajista":
            return "Tendencia bajista clara en marcos amplios."
        if ema_status == "Precio abajo":
            return "El precio está por debajo de la EMA 200, con sesgo negativo."
        return "La debilidad del mercado favorece una señal de venta."

    if short_bias == "Neutral" and trend_30d == "Neutral":
        return "No hay confirmación clara por ahora."
    if trend_1y == "Alcista" and short_bias == "Bajista":
        return "La tendencia macro sigue positiva, pero el corto plazo está débil."
    if trend_1y == "Bajista" and short_bias == "Alcista":
        return "Hay rebote de corto plazo, pero el contexto macro sigue débil."

    return "Las señales están mezcladas. Mejor esperar confirmación."


# =========================
# SENDERS
# =========================
async def send_top_movers(bot):
    gainers, losers = get_top_movers()

    if gainers is None:
        return

    message = "📊 CRYPTO TOP MOVERS\n\n"
    message += "🚀 Top Ganadores (24h)\n\n"

    medals = ["🥇", "🥈", "🥉"]

    for i, coin in enumerate(gainers):
        symbol = coin["symbol"].upper()
        change = coin["price_change_percentage_24h"]
        price = coin["current_price"]

        message += f"{medals[i]} {symbol} +{change:.2f}%\n"
        message += f"Precio: ${price:,.2f}\n\n"

    message += "📉 Top Perdedor\n\n"

    for coin in losers:
        symbol = coin["symbol"].upper()
        change = coin["price_change_percentage_24h"]
        price = coin["current_price"]

        message += f"{symbol} {change:.2f}%\n"
        message += f"Precio: ${price:,.2f}\n\n"

    btc_price = get_btc_price()
    if btc_price is not None:
        message += f"BTC Precio actual: {format_price(btc_price)}\n\n"

    message += f"Hora: {current_time()}\n\n"
    message += "👉 https://t.me/btcalertademo"

    await bot.send_message(chat_id=CHANNEL_ID, text=message)


async def send_fear_and_greed(bot):
    value, sentiment = get_fear_and_greed()

    if value is None:
        return

    if value <= 24:
        emoji = "😨"
    elif value <= 44:
        emoji = "😟"
    elif value <= 55:
        emoji = "😐"
    elif value <= 74:
        emoji = "🤑"
    else:
        emoji = "🔥"

    message = (
        "📊 SENTIMIENTO DEL MERCADO\n\n"
        f"Índice Fear & Greed: {value} {emoji}\n"
        f"Estado: {sentiment}\n\n"
        f"Hora: {current_time()}\n\n"
        "👉 https://t.me/btcalertademo"
    )

    await bot.send_message(chat_id=CHANNEL_ID, text=message)


async def maybe_send_trade_signal(bot, current_price):
    global last_signal_time, last_signal_sent

    if not can_send_signal():
        return

    short_term = evaluate_short_term_bias(current_price)
    macro = get_macro_context()

    if short_term is None or macro is None:
        return

    short_bias = short_term["bias"]
    trend_30d = macro["trend_30d"]
    trend_1y = macro["trend_1y"]
    ema_status = macro["ema_status"]

    decision, confidence = decide_signal(short_bias, trend_30d, trend_1y, ema_status)

    # do not repeat the same signal
    if decision == last_signal_sent:
        return

    conclusion = build_conclusion(decision, short_bias, trend_30d, trend_1y, ema_status)

    emoji = {
        "BUY": "🟢",
        "SELL": "🔴",
        "WAIT": "🟡"
    }[decision]

    message = (
        f"{emoji} BTC SIGNAL\n\n"
        f"Decisión: {decision}\n"
        f"Precio actual: {format_price(current_price)}\n\n"
        "Contexto:\n"
        f"• Sesgo corto plazo: {short_bias}\n"
        f"• Tendencia 30d: {trend_30d}\n"
        f"• Tendencia 1y: {trend_1y}\n"
        f"• EMA 200: {ema_status}\n\n"
        "Conclusión:\n"
        f"{conclusion}\n\n"
        f"Confianza: {confidence}/10\n"
        f"Hora: {current_time()}"
    )

    await bot.send_message(chat_id=CHANNEL_ID, text=message)

    last_signal_time = now_ts()
    last_signal_sent = decision


# =========================
# MAIN
# =========================
async def main():
    if not BOT_TOKEN:
        raise ValueError("Missing TELEGRAM_BOT_TOKEN in environment variables.")

    bot = Bot(token=BOT_TOKEN)

    print("BTC DECISION BOT PRO started")

    initial_price = get_btc_price()
    if initial_price is None:
        raise RuntimeError("Could not get initial BTC price.")

    add_price_to_history(initial_price)

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=(
            "✅ BTC DECISION BOT PRO iniciado\n\n"
            f"Precio actual: {format_price(initial_price)}\n"
            f"Hora: {current_time()}\n"
            "Modo: BUY / SELL / WAIT + Top Movers + Fear & Greed + EMA 200"
        )
    )

    top_movers_counter = 0
    fear_greed_counter = 0
    trade_signal_counter = 0

    while True:
        try:
            price = get_btc_price()

            if price is not None:
                print("BTC:", price)

                add_price_to_history(price)

                top_movers_counter += 1
                fear_greed_counter += 1
                trade_signal_counter += 1

                # every 4 hours
                if top_movers_counter >= 480:
                    await send_top_movers(bot)
                    top_movers_counter = 0

                # every 8 hours
                if fear_greed_counter >= 960:
                    await send_fear_and_greed(bot)
                    fear_greed_counter = 0

                # check trade signal every 30 minutes
                if trade_signal_counter >= 60:
                    await maybe_send_trade_signal(bot, price)
                    trade_signal_counter = 0

            else:
                print("No BTC price received this cycle.")

        except Exception as e:
            print("General error:", e)

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
