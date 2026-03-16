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

NORMAL_THRESHOLD = 0.30
STRONG_THRESHOLD = 0.80

REQUEST_TIMEOUT = 10

# =========================
# STATE
# =========================

last_price = None
hour_prices = []


# =========================
# HELPERS
# =========================

def get_btc_price():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"

    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        data = r.json()

        if "bitcoin" in data:
            return float(data["bitcoin"]["usd"])
        else:
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


async def send_hourly_summary(bot):

    global hour_prices

    if len(hour_prices) < 2:
        return

    current_price = hour_prices[-1]
    first_price = hour_prices[0]

    change = ((current_price - first_price) / first_price) * 100
    high = max(hour_prices)
    low = min(hour_prices)

    if change > 0.10:
        bias = "Alcista"
    elif change < -0.10:
        bias = "Bajista"
    else:
        bias = "Neutral"

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


# =========================
# MAIN BOT
# =========================

async def main():

    global last_price
    global hour_prices

    bot = Bot(token=BOT_TOKEN)

    print("BTC ALERTA PRO iniciado")

    price = get_btc_price()

    if price is None:
        print("No se pudo obtener precio inicial")
        return

    last_price = price
    hour_prices.append(price)

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=(
            "✅ BTC ALERTA PRO iniciado\n\n"
            f"Precio actual: {format_price(price)}\n"
            f"Hora: {current_time()}"
        )
    )

    counter = 0

    while True:

        try:

            price = get_btc_price()

            if price is not None:

                print("BTC:", price)

                hour_prices.append(price)

                change = ((price - last_price) / last_price) * 100

                # 🚨 STRONG PUMP
                if change >= STRONG_THRESHOLD:

                    message = (
                        "🚨 BTC ALERTA PRO\n\n"
                        "Movimiento detectado: PUMP FUERTE\n"
                        f"Precio actual: {format_price(price)}\n"
                        f"Cambio: +{change:.4f}%\n"
                        f"Hora: {current_time()}\n"
                        "Estado: Momentum alcista fuerte"
                    )

                    await bot.send_message(chat_id=CHANNEL_ID, text=message)
                    last_price = price

                # 🚨 STRONG DUMP
                elif change <= -STRONG_THRESHOLD:

                    message = (
                        "🚨 BTC ALERTA PRO\n\n"
                        "Movimiento detectado: DUMP FUERTE\n"
                        f"Precio actual: {format_price(price)}\n"
                        f"Cambio: {change:.4f}%\n"
                        f"Hora: {current_time()}\n"
                        "Estado: Presión bajista fuerte"
                    )

                    await bot.send_message(chat_id=CHANNEL_ID, text=message)
                    last_price = price

                # ⚠️ NORMAL PUMP
                elif change >= NORMAL_THRESHOLD:

                    message = (
                        "⚠️ BTC ALERTA PRO\n\n"
                        "Movimiento detectado: PUMP\n"
                        f"Precio actual: {format_price(price)}\n"
                        f"Cambio: +{change:.4f}%\n"
                        f"Hora: {current_time()}\n"
                        "Estado: Momentum alcista"
                    )

                    await bot.send_message(chat_id=CHANNEL_ID, text=message)
                    last_price = price

                # ⚠️ NORMAL DUMP
                elif change <= -NORMAL_THRESHOLD:

                    message = (
                        "⚠️ BTC ALERTA PRO\n\n"
                        "Movimiento detectado: DUMP\n"
                        f"Precio actual: {format_price(price)}\n"
                        f"Cambio: {change:.4f}%\n"
                        f"Hora: {current_time()}\n"
                        "Estado: Presión bajista"
                    )

                    await bot.send_message(chat_id=CHANNEL_ID, text=message)
                    last_price = price

                counter += 1

                # resumen cada hora
                if counter >= 120:

                    await send_hourly_summary(bot)
                    counter = 0

            else:
                print("No se pudo obtener precio")

        except Exception as e:
            print("Error general:", e)

        await asyncio.sleep(CHECK_INTERVAL)


# =========================

if __name__ == "__main__":
    asyncio.run(main())
