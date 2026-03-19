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

# Momentum thresholds (más altos)
MOMENTUM_1M_THRESHOLD = 0.12
MOMENTUM_5M_THRESHOLD = 0.30
MOMENTUM_15M_THRESHOLD = 0.70

STRONG_1M_THRESHOLD = 0.25
STRONG_5M_THRESHOLD = 0.60
STRONG_15M_THRESHOLD = 1.20

# Cooldown
ALERT_COOLDOWN_SECONDS = 300  # 5 minutos


# =========================
# STATE
# =========================
price_history = []
last_alert_time = 0


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

        if "bitcoin" in data:
            return float(data["bitcoin"]["usd"])

        return None

    except Exception as e:
        print("API Error:", e)
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
        data = response.json()

        gainers = sorted(
            data,
            key=lambda x: x["price_change_percentage_24h"],
            reverse=True
        )[:3]

        losers = sorted(
            data,
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

    cutoff = ts - (20 * 60)
    price_history = [(t, p) for (t, p) in price_history if t >= cutoff]


def get_price_ago(seconds_ago):
    if not price_history:
        return None

    target = now_ts() - seconds_ago
    closest = min(price_history, key=lambda x: abs(x[0] - target))
    return closest[1]


def percent_change(current_price, previous_price):
    if previous_price is None:
        return None
    return ((current_price - previous_price) / previous_price) * 100


def can_send_alert():
    return (now_ts() - last_alert_time) >= ALERT_COOLDOWN_SECONDS


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

    message += f"Hora: {current_time()}\n\n"
    message += "👉 https://t.me/btcalertademo"

    await bot.send_message(chat_id=CHANNEL_ID, text=message)


async def maybe_send_momentum_alert(bot, current_price):
    global last_alert_time

    price_1m = get_price_ago(60)
    price_5m = get_price_ago(300)
    price_15m = get_price_ago(900)

    change_1m = percent_change(current_price, price_1m)
    change_5m = percent_change(current_price, price_5m)
    change_15m = percent_change(current_price, price_15m)

    if change_1m is None or change_5m is None or change_15m is None:
        return

    print(
        f"Momentum debug: 1m={change_1m:.3f}% 5m={change_5m:.3f}% 15m={change_15m:.3f}%"
    )

    if not can_send_alert():
        return

    bullish = sum([
        change_1m >= MOMENTUM_1M_THRESHOLD,
        change_5m >= MOMENTUM_5M_THRESHOLD,
        change_15m >= MOMENTUM_15M_THRESHOLD,
    ])

    bearish = sum([
        change_1m <= -MOMENTUM_1M_THRESHOLD,
        change_5m <= -MOMENTUM_5M_THRESHOLD,
        change_15m <= -MOMENTUM_15M_THRESHOLD,
    ])

    if bullish >= 2:
        message = (
            "🚨 BTC MOMENTUM\n\n"
            "Momentum alcista detectado\n"
            f"Precio: {format_price(current_price)}\n"
            f"1m: +{change_1m:.2f}% | 5m: +{change_5m:.2f}% | 15m: +{change_15m:.2f}%\n"
            f"Hora: {current_time()}"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=message)
        last_alert_time = now_ts()

    elif bearish >= 2:
        message = (
            "🚨 BTC MOMENTUM\n\n"
            "Momentum bajista detectado\n"
            f"Precio: {format_price(current_price)}\n"
            f"1m: {change_1m:.2f}% | 5m: {change_5m:.2f}% | 15m: {change_15m:.2f}%\n"
            f"Hora: {current_time()}"
        )
        await bot.send_message(chat_id=CHANNEL_ID, text=message)
        last_alert_time = now_ts()


# =========================
# MAIN
# =========================
async def main():

    if not BOT_TOKEN:
        raise ValueError("Falta TELEGRAM_BOT_TOKEN")

    bot = Bot(token=BOT_TOKEN)

    print("BOT ACTIVO")

    top_movers_counter = 0

    while True:
        try:
            price = get_btc_price()

            if price:
                add_price_to_history(price)
                await maybe_send_momentum_alert(bot, price)

                top_movers_counter += 1

                # cada 4 horas
                if top_movers_counter >= 480:
                    await send_top_movers(bot)
                    top_movers_counter = 0

        except Exception as e:
            print("Error:", e)

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
