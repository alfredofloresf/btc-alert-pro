import os
import asyncio
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from telegram import Bot

# =========================
# CONFIG
# =========================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = "@btcalertademo"

CHECK_INTERVAL = 30
REQUEST_TIMEOUT = 10

# Momentum thresholds
MOMENTUM_1M_THRESHOLD = 0.15
MOMENTUM_5M_THRESHOLD = 0.40
MOMENTUM_15M_THRESHOLD = 0.80

STRONG_1M_THRESHOLD = 0.25
STRONG_5M_THRESHOLD = 0.70
STRONG_15M_THRESHOLD = 1.20

# Cooldown to avoid spam
ALERT_COOLDOWN_SECONDS = 600  # 10 minutes

# =========================
# STATE
# =========================

price_history = []   # list of tuples: (timestamp, price)
last_alert_time = 0
hour_prices = []


# =========================
# HELPERS
# =========================

def get_btc_price():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"

    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        if "bitcoin" in data and "usd" in data["bitcoin"]:
            return float(data["bitcoin"]["usd"])

        print("API response inesperada:", data)
        return None

    except Exception as e:
        print("API Error:", e)
        return None


def format_price(price):
    return f"${price:,.2f}"


def current_time():
    ny_time = datetime.now(ZoneInfo("America/New_York"))
    return ny_time.strftime("%I:%M %p ET")


def now_ts():
    return datetime.now().timestamp()


def add_price_to_history(price):
    global price_history
    ts = now_ts()
    price_history.append((ts, price))

    # mantener solo últimos 20 minutos
    cutoff = ts - (20 * 60)
    price_history = [(t, p) for (t, p) in price_history if t >= cutoff]


def get_price_ago(seconds_ago):
    """
    Busca el precio más cercano a 'seconds_ago' segundos atrás.
    """
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


async def maybe_send_momentum_alert(bot, current_price):
    global last_alert_time

    price_1m = get_price_ago(60)
    price_5m = get_price_ago(5 * 60)
    price_15m = get_price_ago(15 * 60)

    change_1m = percent_change(current_price, price_1m)
    change_5m = percent_change(current_price, price_5m)
    change_15m = percent_change(current_price, price_15m)

    # No alert until enough history exists
    if change_1m is None or change_5m is None or change_15m is None:
        return

    if not can_send_alert():
        return

    # Strong bullish momentum
    if (
        change_1m >= STRONG_1M_THRESHOLD and
        change_5m >= STRONG_5M_THRESHOLD and
        change_15m >= STRONG_15M_THRESHOLD
    ):
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

    # Strong bearish momentum
    if (
        change_1m <= -STRONG_1M_THRESHOLD and
        change_5m <= -STRONG_5M_THRESHOLD and
        change_15m <= -STRONG_15M_THRESHOLD
    ):
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

    # Normal bullish momentum
    if (
        change_1m >= MOMENTUM_1M_THRESHOLD and
        change_5m >= MOMENTUM_5M_THRESHOLD and
        change_15m >= MOMENTUM_15M_THRESHOLD
    ):
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

    # Normal bearish momentum
    if (
        change_1m <= -MOMENTUM_1M_THRESHOLD and
        change_5m <= -MOMENTUM_5M_THRESHOLD and
        change_15m <= -MOMENTUM_15M_THRESHOLD
    ):
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
            "Modo: Monitoreo de momentum 1m / 5m / 15m"
        )
    )

    counter = 0

    while True:
        try:
            price = get_btc_price()

            if price is not None:
                print("BTC:", price)

                add_price_to_history(price)
                hour_prices.append(price)

                await maybe_send_momentum_alert(bot, price)

                counter += 1

                # 30s * 120 = 1 hour
                if counter >= 120:
                    await send_hourly_summary(bot)
                    counter = 0

            else:
                print("No se obtuvo precio en este ciclo.")

        except Exception as e:
            print("Error general:", e)

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
