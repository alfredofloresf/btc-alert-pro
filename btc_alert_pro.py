import os
import asyncio
from datetime import datetime

import requests
from telegram import Bot


# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = "@btcalertademo"

CHECK_INTERVAL = 30           # segundos
ALERT_THRESHOLD = 0.05        # porcentaje para pruebas
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
    """
    Obtiene el precio actual de BTC desde CoinGecko.
    Devuelve float o None si falla.
    """
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


def format_price(price):
    return f"${price:,.2f}"


def current_time():
    return datetime.now().strftime("%I:%M %p")


async def send_hourly_summary(bot):
    """
    Envía resumen de la última hora.
    """
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
        f"Hora: {current_time()} NY\n\n"
        "BTC Alert Pro sigue monitoreando el mercado."
    )

    await bot.send_message(chat_id=CHANNEL_ID, text=message)

    # reinicia acumulación de la siguiente hora
    hour_prices = []


# =========================
# MAIN
# =========================
async def main():
    global last_price, hour_prices

    if not BOT_TOKEN:
        raise ValueError("No se encontró TELEGRAM_BOT_TOKEN en las variables de entorno.")

    bot = Bot(token=BOT_TOKEN)

    print("BTC ALERTA PRO iniciado")

    # precio inicial
    initial_price = get_btc_price()
    if initial_price is None:
        raise RuntimeError("No se pudo obtener el precio inicial de BTC.")

    last_price = initial_price
    hour_prices.append(initial_price)

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=(
            "✅ BTC ALERTA PRO iniciado\n\n"
            f"Precio actual: {format_price(initial_price)}\n"
            f"Hora: {current_time()} NY"
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

                # PUMP
                if change > ALERT_THRESHOLD:
                    message = (
                        "🚨 BTC ALERTA PRO\n\n"
                        "Movimiento detectado: PUMP\n"
                        f"Precio actual: {format_price(price)}\n"
                        f"Cambio: +{change:.4f}%\n"
                        f"Hora: {current_time()} NY\n"
                        "Estado: Momentum alcista"
                    )

                    await bot.send_message(chat_id=CHANNEL_ID, text=message)
                    last_price = price

                # DUMP
                elif change < -ALERT_THRESHOLD:
                    message = (
                        "🚨 BTC ALERTA PRO\n\n"
                        "Movimiento detectado: DUMP\n"
                        f"Precio actual: {format_price(price)}\n"
                        f"Cambio: {change:.4f}%\n"
                        f"Hora: {current_time()} NY\n"
                        "Estado: Presión bajista"
                    )

                    await bot.send_message(chat_id=CHANNEL_ID, text=message)
                    last_price = price

                counter += 1

                # cada hora: 30s * 120 = 3600s
                if counter >= 120:
                    await send_hourly_summary(bot)
                    counter = 0

            else:
                print("No se obtuvo precio de BTC en este ciclo.")

        except Exception as e:
            print("Error general:", e)

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
