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

# Momentum thresholds
MOMENTUM_1M_THRESHOLD = 0.05
MOMENTUM_5M_THRESHOLD = 0.15
MOMENTUM_15M_THRESHOLD = 0.30

STRONG_1M_THRESHOLD = 0.10
STRONG_5M_THRESHOLD = 0.25
STRONG_15M_THRESHOLD = 0.60

# Cooldown
ALERT_COOLDOWN_SECONDS = 300  # 5 min


# =========================
# STATE
# =========================
price_history = []   # [(timestamp, price)]
last_alert_time = 0
hour_prices = []


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

        print("API response inesperada:", data)
        return None

    except requests.RequestException as e:
        print("API Error:", e)
        return None
    except (ValueError, TypeError, KeyError) as e:
        print("Parse Error:", e)
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


def add_price_to_history(price):
    global price_history

    ts = now_ts()
    price_history.append((ts, price))

    cutoff = ts - (20 * 60)  # mantener últimos 20 minutos
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


def can_send_alert():
    return (now_ts() - last_alert_time) >= ALERT_COOLDOWN_SECONDS


def classify_bias(change):
    if change is None:
        return "Neutral"
    if change > 0.10:
        return "Alcista"
    if change < -0.10:
        return "Bajista"
    return "Neutral"


# =========================
# SENDERS
# =========================
async def send_hourly_summary(bot):
    global hour_prices

    if len(hour_prices) < 2:
        return

    current_price = hour_prices[-1]
    first_price = hour_prices[0]
    change = percent_change(current_price, first_price)

    high = max(hour_prices)
    low = min(hour_prices)
    bias = classify_bias(change)

    message = (
        "📊 BTC RESUMEN 1H\n\n"
        f"Precio actual: {format_price(current_price)}\n"
        f"Cambio última hora: {change:+.2f}%\n\n"
        f"Máximo 1h: {format_price(high)}\n"
        f"Mínimo 1h: {format_price(low)}\n\n"
        f"Sesgo del mercado: {bias}\n"
        f"Hora: {current_time()}\n\n"
        "BTC Alert Pro monitoreando mercado 24/7"
    )

    await bot.send_message(chat_id=CHANNEL_ID, text=message)
    hour_prices = []


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


async def maybe_send_momentum_alert(bot, current_price):
    global last_alert_time

    price_1m = get_price_ago(60)
    price_5m = get_price_ago(5 * 60)
    price_15m = get_price_ago(15 * 60)

    change_1m = percent_change(current_price, price_1m)
    change_5m = percent_change(current_price, price_5m)
    change_15m = percent_change(current_price, price_15m)

    if change_1m is None or change_5m is None or change_15m is None:
        return

    print(
        "Momentum debug:",
        f"1m={change_1m:.3f}%",
        f"5m={change_5m:.3f}%",
        f"15m={change_15m:.3f}%"
    )

    if not can_send_alert():
        return

    bullish_count = sum([
        change_1m >= MOMENTUM_1M_THRESHOLD,
        change_5m >= MOMENTUM_5M_THRESHOLD,
        change_15m >= MOMENTUM_15M_THRESHOLD,
    ])

    bearish_count = sum([
        change_1m <= -MOMENTUM_1M_THRESHOLD,
        change_5m <= -MOMENTUM_5M_THRESHOLD,
        change_15m <= -MOMENTUM_15M_THRESHOLD,
    ])

    strong_bullish = sum([
        change_1m >= STRONG_1M_THRESHOLD,
        change_5m >= STRONG_5M_THRESHOLD,
        change_15m >= STRONG_15M_THRESHOLD,
    ])

    strong_bearish = sum([
        change_1m <= -STRONG_1M_THRESHOLD,
        change_5m <= -STRONG_5M_THRESHOLD,
        change_15m <= -STRONG_15M_THRESHOLD,
    ])

    if strong_bullish >= 2:
        message = (
            "🚨 BTC MOMENTUM PRO\n\n"
            "Movimiento detectado: MOMENTUM ALCISTA FUERTE\n"
            f"Precio actual: {format_price(current_price)}\n"
            f"Cambio 1m: +{change_1m:.2f}%\n"
            f"Cambio 5m: +{change_5m:.2f}%\n"
            f"Cambio 15m: +{change_15m:.2f}%\n"
            f"Hora: {current_time()}\n"
            "Estado: Impulso comprador fuerte"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=message)
        last_alert_time = now_ts()
        return

    if strong_bearish >= 2:
        message = (
            "🚨 BTC MOMENTUM PRO\n\n"
            "Movimiento detectado: MOMENTUM BAJISTA FUERTE\n"
            f"Precio actual: {format_price(current_price)}\n"
            f"Cambio 1m: {change_1m:.2f}%\n"
            f"Cambio 5m: {change_5m:.2f}%\n"
            f"Cambio 15m: {change_15m:.2f}%\n"
            f"Hora: {current_time()}\n"
            "Estado: Presión vendedora fuerte"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=message)
        last_alert_time = now_ts()
        return

    if bullish_count >= 2:
        message = (
            "⚠️ BTC MOMENTUM PRO\n\n"
            "Movimiento detectado: MOMENTUM ALCISTA\n"
            f"Precio actual: {format_price(current_price)}\n"
            f"Cambio 1m: +{change_1m:.2f}%\n"
            f"Cambio 5m: +{change_5m:.2f}%\n"
            f"Cambio 15m: +{change_15m:.2f}%\n"
            f"Hora: {current_time()}\n"
            "Estado: Momentum alcista"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=message)
        last_alert_time = now_ts()
        return

    if bearish_count >= 2:
        message = (
            "⚠️ BTC MOMENTUM PRO\n\n"
            "Movimiento detectado: MOMENTUM BAJISTA\n"
            f"Precio actual: {format_price(current_price)}\n"
            f"Cambio 1m: {change_1m:.2f}%\n"
            f"Cambio 5m: {change_5m:.2f}%\n"
            f"Cambio 15m: {change_15m:.2f}%\n"
            f"Hora: {current_time()}\n"
            "Estado: Momentum bajista"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=message)
        last_alert_time = now_ts()
        return


# =========================
# MAIN
# =========================
async def main():
    global hour_prices

    if not BOT_TOKEN:
        raise ValueError("No se encontró TELEGRAM_BOT_TOKEN en variables de entorno.")

    bot = Bot(token=BOT_TOKEN)

    print("BTC MOMENTUM PRO iniciado")

    initial_price = get_btc_price()
    if initial_price is None:
        raise RuntimeError("No se pudo obtener el precio inicial de BTC.")

    add_price_to_history(initial_price)
    hour_prices.append(initial_price)

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=(
            "✅ BTC MOMENTUM PRO iniciado\n\n"
            f"Precio actual: {format_price(initial_price)}\n"
            f"Hora: {current_time()}\n"
            "Modo: Momentum + Resumen 1H + Top Movers"
        )
    )

    hourly_counter = 0
    top_movers_counter = 0

    while True:
        try:
            price = get_btc_price()

            if price is not None:
                print("BTC:", price)

                add_price_to_history(price)
                hour_prices.append(price)

                await maybe_send_momentum_alert(bot, price)

                hourly_counter += 1
                top_movers_counter += 1

                # 30s * 120 = 1 hora
                if hourly_counter >= 120:
                    await send_hourly_summary(bot)
                    hourly_counter = 0

                # 30s * 480 = 4 horas
                if top_movers_counter >= 480:
                    await send_top_movers(bot)
                    top_movers_counter = 0

            else:
                print("No se obtuvo precio en este ciclo.")

        except Exception as e:
            print("Error general:", e)

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
