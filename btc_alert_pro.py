import asyncio
import requests
from telegram import Bot
from datetime import datetime

BOT_TOKEN = "8737159926:AAGEAPNigIKy2hPcTgZGajN6PQh9MHncVso"
CHANNEL_ID = "@btcalertademo"

CHECK_INTERVAL = 30
ALERT_THRESHOLD = 0.05  # para pruebas

last_price = None
hour_prices = []


def get_btc_price():
    url = "https://api.coingecko.com/api/v3/simple/price?ids=bitcoin&vs_currencies=usd"
    r = requests.get(url, timeout=10)
    return float(r.json()["bitcoin"]["usd"])


def format_price(price):
    return f"${price:,.2f}"


def current_time():
    return datetime.now().strftime("%I:%M %p")


async def send_hourly_summary(bot):
    global hour_prices

    if len(hour_prices) == 0:
        return

    current_price = hour_prices[-1]
    first_price = hour_prices[0]

    change = ((current_price - first_price) / first_price) * 100

    high = max(hour_prices)
    low = min(hour_prices)

    if change > 0.1:
        bias = "Alcista"
    elif change < -0.1:
        bias = "Bajista"
    else:
        bias = "Neutral"

    message = f"""
📊 BTC RESUMEN 1H

Precio actual: {format_price(current_price)}
Cambio última hora: {change:+.2f}%

Máximo 1h: {format_price(high)}
Mínimo 1h: {format_price(low)}

Sesgo del mercado: {bias}
Hora: {current_time()} NY

BTC Alert Pro sigue monitoreando el mercado.
"""

    await bot.send_message(chat_id=CHANNEL_ID, text=message)

    hour_prices = []


async def main():
    global last_price, hour_prices

    bot = Bot(token=BOT_TOKEN)

    print("BTC ALERTA PRO iniciado")

    price = get_btc_price()
    last_price = price
    hour_prices.append(price)

    await bot.send_message(
        chat_id=CHANNEL_ID,
        text=f"✅ BTC ALERTA PRO iniciado\n\nPrecio actual: {format_price(price)}"
    )

    counter = 0

    while True:

        try:

            price = get_btc_price()
            print("BTC:", price)

            hour_prices.append(price)

            change = ((price - last_price) / last_price) * 100

            if change > ALERT_THRESHOLD:

                message = f"""
🚨 BTC ALERTA PRO

Movimiento detectado: PUMP
Precio actual: {format_price(price)}
Cambio: +{change:.4f}%
Hora: {current_time()} NY
Estado: Momentum alcista
"""

                await bot.send_message(chat_id=CHANNEL_ID, text=message)

                last_price = price

            elif change < -ALERT_THRESHOLD:

                message = f"""
🚨 BTC ALERTA PRO

Movimiento detectado: DUMP
Precio actual: {format_price(price)}
Cambio: {change:.4f}%
Hora: {current_time()} NY
Estado: Presión bajista
"""

                await bot.send_message(chat_id=CHANNEL_ID, text=message)

                last_price = price

            counter += 1

            # cada hora (30s * 120 = 1 hora)
            if counter >= 120:
                await send_hourly_summary(bot)
                counter = 0

        except Exception as e:
            print("Error:", e)

        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())