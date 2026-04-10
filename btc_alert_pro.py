import os
import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional, List, Tuple

import aiohttp
from zoneinfo import ZoneInfo
from telegram import Bot
from telegram.error import TelegramError


# =========================
# CONFIG
# =========================
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID", "@btcalertademo")

CHECK_INTERVAL = 30
REQUEST_TIMEOUT = 10

SIGNAL_COOLDOWN_SECONDS = 1800  # 30 min

TOP_MOVERS_EVERY_SECONDS = 4 * 60 * 60      # 4h
FEAR_GREED_EVERY_SECONDS = 8 * 60 * 60      # 8h
TRADE_SIGNAL_EVERY_SECONDS = 30 * 60        # 30m

MACRO_CACHE_TTL = 60 * 60                   # 1h
TOP_MOVERS_CACHE_TTL = 30 * 60              # 30m
FEAR_GREED_CACHE_TTL = 30 * 60              # 30m

PRICE_HISTORY_WINDOW_SECONDS = 20 * 60      # 20 minutes


# =========================
# LOGGING
# =========================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("btc_bot")


# =========================
# DATA CLASSES
# =========================
@dataclass
class MacroContext:
    trend_30d: str
    trend_1y: str
    ema_200: Optional[float]
    ema_status: str
    current_daily_price: float


@dataclass
class ShortBias:
    bias: str
    change_1m: float
    change_5m: float
    change_15m: float


@dataclass
class CacheItem:
    value: object = None
    expires_at: float = 0.0

    def valid(self) -> bool:
        return time.time() < self.expires_at


@dataclass
class BotState:
    price_history: List[Tuple[float, float]] = field(default_factory=list)
    last_signal_time: float = 0.0
    last_signal_sent: Optional[str] = None
    last_top_movers_sent: float = 0.0
    last_fear_greed_sent: float = 0.0
    last_trade_signal_check: float = 0.0
    macro_cache: CacheItem = field(default_factory=CacheItem)
    top_movers_cache: CacheItem = field(default_factory=CacheItem)
    fear_greed_cache: CacheItem = field(default_factory=CacheItem)


# =========================
# BOT CLASS
# =========================
class BTCDecisionBot:
    def __init__(self, token: str, channel_id: str):
        if not token:
            raise ValueError("Missing TELEGRAM_BOT_TOKEN in environment variables.")

        self.bot = Bot(token=token)
        self.channel_id = channel_id
        self.state = BotState()
        self.session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        self.session = aiohttp.ClientSession(timeout=timeout)
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self.session:
            await self.session.close()

    # =========================
    # HELPERS
    # =========================
    @staticmethod
    def now_ts() -> float:
        return time.time()

    @staticmethod
    def current_time() -> str:
        ny_time = datetime.now(ZoneInfo("America/New_York"))
        return ny_time.strftime("%I:%M %p ET")

    @staticmethod
    def format_price(price: float) -> str:
        return f"${price:,.2f}"

    @staticmethod
    def percent_change(current_price: float, previous_price: Optional[float]) -> Optional[float]:
        if previous_price is None or previous_price == 0:
            return None
        return ((current_price - previous_price) / previous_price) * 100

    @staticmethod
    def classify_trend(prices: List[float]) -> str:
        if not prices or len(prices) < 2:
            return "Neutral"

        change = BTCDecisionBot.percent_change(prices[-1], prices[0])
        if change is None:
            return "Neutral"
        if change > 5:
            return "Alcista"
        if change < -5:
            return "Bajista"
        return "Neutral"

    @staticmethod
    def calculate_ema(prices: List[float], period: int = 200) -> Optional[float]:
        if not prices or len(prices) < period:
            return None

        k = 2 / (period + 1)
        ema = sum(prices[:period]) / period

        for price in prices[period:]:
            ema = (price * k) + (ema * (1 - k))

        return ema

    # =========================
    # HTTP
    # =========================
    async def fetch_json(self, url: str, params: dict | None = None, retries: int = 3) -> Optional[dict]:
        assert self.session is not None, "HTTP session not initialized"

        for attempt in range(1, retries + 1):
            try:
                async with self.session.get(url, params=params) as response:
                    response.raise_for_status()
                    return await response.json()
            except Exception as e:
                logger.warning("HTTP error on %s (attempt %s/%s): %s", url, attempt, retries, e)
                if attempt < retries:
                    await asyncio.sleep(1.5 * attempt)

        return None

    # =========================
    # API METHODS
    # =========================
    async def get_btc_price(self) -> Optional[float]:
        url = "https://api.coingecko.com/api/v3/simple/price"
        params = {"ids": "bitcoin", "vs_currencies": "usd"}
        data = await self.fetch_json(url, params=params)

        if not data:
            return None

        try:
            return float(data["bitcoin"]["usd"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Unexpected BTC price response: %s", data)
            return None

    async def get_historical_prices(self, days: int) -> Optional[List[float]]:
        url = "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
        params = {
            "vs_currency": "usd",
            "days": days,
            "interval": "daily",
        }
        data = await self.fetch_json(url, params=params)

        if not data:
            return None

        prices = [item[1] for item in data.get("prices", []) if len(item) >= 2]
        return prices or None

    async def get_top_movers(self):
        if self.state.top_movers_cache.valid():
            return self.state.top_movers_cache.value

        url = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency": "usd",
            "order": "price_change_percentage_24h_desc",
            "per_page": 10,
            "page": 1,
        }
        data = await self.fetch_json(url, params=params)

        if not data:
            return None

        valid_data = [
            coin for coin in data
            if coin.get("price_change_percentage_24h") is not None
        ]

        if not valid_data:
            return None

        gainers = sorted(
            valid_data,
            key=lambda x: x["price_change_percentage_24h"],
            reverse=True
        )[:3]

        losers = sorted(
            valid_data,
            key=lambda x: x["price_change_percentage_24h"]
        )[:1]

        result = (gainers, losers)
        self.state.top_movers_cache = CacheItem(
            value=result,
            expires_at=self.now_ts() + TOP_MOVERS_CACHE_TTL
        )
        return result

    async def get_fear_and_greed(self):
        if self.state.fear_greed_cache.valid():
            return self.state.fear_greed_cache.value

        url = "https://api.alternative.me/fng/"
        params = {"limit": 1}
        data = await self.fetch_json(url, params=params)

        if not data:
            return None

        items = data.get("data", [])
        if not items:
            return None

        try:
            value = int(items[0]["value"])
            classification_en = items[0]["value_classification"]
        except (KeyError, ValueError, TypeError):
            logger.warning("Unexpected Fear & Greed response: %s", data)
            return None

        translation_map = {
            "Extreme Fear": "Miedo extremo",
            "Fear": "Miedo",
            "Neutral": "Neutral",
            "Greed": "Codicia",
            "Extreme Greed": "Codicia extrema",
        }

        result = (value, translation_map.get(classification_en, classification_en))
        self.state.fear_greed_cache = CacheItem(
            value=result,
            expires_at=self.now_ts() + FEAR_GREED_CACHE_TTL
        )
        return result

    async def get_macro_context(self) -> Optional[MacroContext]:
        if self.state.macro_cache.valid():
            return self.state.macro_cache.value

        prices_30d, prices_1y = await asyncio.gather(
            self.get_historical_prices(30),
            self.get_historical_prices(365),
        )

        if not prices_30d or not prices_1y:
            return None

        trend_30d = self.classify_trend(prices_30d)
        trend_1y = self.classify_trend(prices_1y)

        ema_200 = self.calculate_ema(prices_1y, 200)
        current_daily_price = prices_1y[-1]

        if ema_200 is None:
            ema_status = "Neutral"
        elif current_daily_price > ema_200:
            ema_status = "Precio arriba"
        elif current_daily_price < ema_200:
            ema_status = "Precio abajo"
        else:
            ema_status = "Neutral"

        macro = MacroContext(
            trend_30d=trend_30d,
            trend_1y=trend_1y,
            ema_200=ema_200,
            ema_status=ema_status,
            current_daily_price=current_daily_price,
        )

        self.state.macro_cache = CacheItem(
            value=macro,
            expires_at=self.now_ts() + MACRO_CACHE_TTL
        )
        return macro

    # =========================
    # PRICE HISTORY
    # =========================
    def add_price_to_history(self, price: float) -> None:
        ts = self.now_ts()
        self.state.price_history.append((ts, price))

        cutoff = ts - PRICE_HISTORY_WINDOW_SECONDS
        self.state.price_history = [
            (t, p) for (t, p) in self.state.price_history if t >= cutoff
        ]

    def get_price_ago(self, seconds_ago: int) -> Optional[float]:
        if not self.state.price_history:
            return None

        target = self.now_ts() - seconds_ago
        closest = min(self.state.price_history, key=lambda x: abs(x[0] - target))
        return closest[1]

    def evaluate_short_term_bias(self, current_price: float) -> Optional[ShortBias]:
        price_1m = self.get_price_ago(60)
        price_5m = self.get_price_ago(300)
        price_15m = self.get_price_ago(900)

        change_1m = self.percent_change(current_price, price_1m)
        change_5m = self.percent_change(current_price, price_5m)
        change_15m = self.percent_change(current_price, price_15m)

        if change_1m is None or change_5m is None or change_15m is None:
            return None

        bullish = sum([change_1m > 0, change_5m > 0, change_15m > 0])
        bearish = sum([change_1m < 0, change_5m < 0, change_15m < 0])

        if bullish >= 2:
            bias = "Alcista"
        elif bearish >= 2:
            bias = "Bajista"
        else:
            bias = "Neutral"

        return ShortBias(
            bias=bias,
            change_1m=change_1m,
            change_5m=change_5m,
            change_15m=change_15m,
        )

    # =========================
    # SIGNAL LOGIC
    # =========================
    @staticmethod
    def build_score(short_bias: str, trend_30d: str, trend_1y: str, ema_status: str):
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

    def decide_signal(self, short_bias: str, trend_30d: str, trend_1y: str, ema_status: str):
        bullish_score, bearish_score = self.build_score(short_bias, trend_30d, trend_1y, ema_status)

        if bullish_score >= 5 and bullish_score > bearish_score:
            return "BUY", min(10, 5 + (bullish_score - bearish_score))
        elif bearish_score >= 5 and bearish_score > bullish_score:
            return "SELL", min(10, 5 + (bearish_score - bullish_score))
        else:
            return "WAIT", 6

    @staticmethod
    def build_conclusion(decision: str, short_bias: str, trend_30d: str, trend_1y: str, ema_status: str) -> str:
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

    def can_send_signal(self) -> bool:
        return (self.now_ts() - self.state.last_signal_time) >= SIGNAL_COOLDOWN_SECONDS

    # =========================
    # TELEGRAM
    # =========================
    async def send_message(self, text: str) -> None:
        try:
            await self.bot.send_message(chat_id=self.channel_id, text=text)
        except TelegramError as e:
            logger.error("Telegram send error: %s", e)

    async def send_top_movers(self):
        result = await self.get_top_movers()
        if not result:
            return

        gainers, losers = result
        medals = ["🥇", "🥈", "🥉"]

        message = "📊 CRYPTO TOP MOVERS\n\n"
        message += "🚀 Top Ganadores (24h)\n\n"

        for i, coin in enumerate(gainers):
            symbol = coin["symbol"].upper()
            change = coin["price_change_percentage_24h"]
            price = coin["current_price"]

            sign = "+" if change >= 0 else ""
            message += f"{medals[i]} {symbol} {sign}{change:.2f}%\n"
            message += f"Precio: ${price:,.2f}\n\n"

        message += "📉 Top Perdedor\n\n"

        for coin in losers:
            symbol = coin["symbol"].upper()
            change = coin["price_change_percentage_24h"]
            price = coin["current_price"]
            sign = "+" if change >= 0 else ""

            message += f"{symbol} {sign}{change:.2f}%\n"
            message += f"Precio: ${price:,.2f}\n\n"

        btc_price = await self.get_btc_price()
        if btc_price is not None:
            message += f"BTC Precio actual: {self.format_price(btc_price)}\n\n"

        message += f"Hora: {self.current_time()}\n\n"
        message += "👉 https://t.me/btcalertademo"

        await self.send_message(message)
        self.state.last_top_movers_sent = self.now_ts()

    async def send_fear_and_greed(self):
        result = await self.get_fear_and_greed()
        if not result:
            return

        value, sentiment = result

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
            f"Hora: {self.current_time()}\n\n"
            "👉 https://t.me/btcalertademo"
        )

        await self.send_message(message)
        self.state.last_fear_greed_sent = self.now_ts()

    async def maybe_send_trade_signal(self, current_price: float):
        if not self.can_send_signal():
            return

        short_term = self.evaluate_short_term_bias(current_price)
        macro = await self.get_macro_context()

        if short_term is None or macro is None:
            logger.info("Not enough data yet for trade signal.")
            return

        decision, confidence = self.decide_signal(
            short_term.bias,
            macro.trend_30d,
            macro.trend_1y,
            macro.ema_status,
        )

        if decision == self.state.last_signal_sent:
            logger.info("Skipping duplicate signal: %s", decision)
            return

        conclusion = self.build_conclusion(
            decision,
            short_term.bias,
            macro.trend_30d,
            macro.trend_1y,
            macro.ema_status,
        )

        emoji = {"BUY": "🟢", "SELL": "🔴", "WAIT": "🟡"}[decision]

        message = (
            f"{emoji} BTC SIGNAL\n\n"
            f"Decisión: {decision}\n"
            f"Precio actual: {self.format_price(current_price)}\n\n"
            "Contexto:\n"
            f"• Sesgo corto plazo: {short_term.bias}\n"
            f"• Tendencia 30d: {macro.trend_30d}\n"
            f"• Tendencia 1y: {macro.trend_1y}\n"
            f"• EMA 200: {macro.ema_status}\n\n"
            "Conclusión:\n"
            f"{conclusion}\n\n"
            f"Confianza: {confidence}/10\n"
            f"Hora: {self.current_time()}"
        )

        await self.send_message(message)

        self.state.last_signal_time = self.now_ts()
        self.state.last_signal_sent = decision
        self.state.last_trade_signal_check = self.now_ts()

    # =========================
    # SCHEDULING
    # =========================
    def should_send_top_movers(self) -> bool:
        return (self.now_ts() - self.state.last_top_movers_sent) >= TOP_MOVERS_EVERY_SECONDS

    def should_send_fear_greed(self) -> bool:
        return (self.now_ts() - self.state.last_fear_greed_sent) >= FEAR_GREED_EVERY_SECONDS

    def should_check_trade_signal(self) -> bool:
        return (self.now_ts() - self.state.last_trade_signal_check) >= TRADE_SIGNAL_EVERY_SECONDS

    # =========================
    # MAIN LOOP
    # =========================
    async def run(self):
        logger.info("BTC DECISION BOT PRO started")

        initial_price = await self.get_btc_price()
        if initial_price is None:
            raise RuntimeError("Could not get initial BTC price.")

        self.add_price_to_history(initial_price)

        await self.send_message(
            "✅ BTC DECISION BOT PRO iniciado\n\n"
            f"Precio actual: {self.format_price(initial_price)}\n"
            f"Hora: {self.current_time()}\n"
            "Modo: BUY / SELL / WAIT + Top Movers + Fear & Greed + EMA 200"
        )

        # allow periodic jobs to run relative to startup
        now = self.now_ts()
        self.state.last_top_movers_sent = now
        self.state.last_fear_greed_sent = now
        self.state.last_trade_signal_check = now - TRADE_SIGNAL_EVERY_SECONDS

        while True:
            try:
                price = await self.get_btc_price()

                if price is not None:
                    logger.info("BTC: %s", price)
                    self.add_price_to_history(price)

                    tasks = []

                    if self.should_send_top_movers():
                        tasks.append(self.send_top_movers())

                    if self.should_send_fear_greed():
                        tasks.append(self.send_fear_and_greed())

                    if self.should_check_trade_signal():
                        tasks.append(self.maybe_send_trade_signal(price))

                    if tasks:
                        await asyncio.gather(*tasks, return_exceptions=True)
                else:
                    logger.warning("No BTC price received this cycle.")

            except Exception as e:
                logger.exception("General error in main loop: %s", e)

            await asyncio.sleep(CHECK_INTERVAL)


# =========================
# ENTRY POINT
# =========================
async def main():
    async with BTCDecisionBot(BOT_TOKEN, CHANNEL_ID) as app:
        await app.run()


if __name__ == "__main__":
    asyncio.run(main())
