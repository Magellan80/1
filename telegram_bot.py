import io
import os
import time
import asyncio
import datetime
import aiohttp

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from aiogram import Bot, Dispatcher, types, F
from aiogram.types import BufferedInputFile
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.exceptions import TelegramRetryAfter, TelegramNetworkError, TelegramServerError
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))


class TelegramNotifier:
    def __init__(self, token: str, chat_id: int):
        self.bot = Bot(token=token)
        self.dp = Dispatcher()
        self.chat_id = chat_id

        self.signals_enabled = True
        self.trading_enabled = False

        self.last_direction = {}
        self.last_quality = {}

        self._funding_cache = {}
        self._funding_ttl = 60

        self._session: aiohttp.ClientSession | None = None

        self.positions_ref = None
        self.closeall_callback = None

        # ============================
        #   ХЕНДЛЕРЫ КОМАНД
        # ============================
        self.dp.message.register(self.cmd_start, F.text == "/start")

        # ============================
        #   ГЛАВНОЕ МЕНЮ
        # ============================
        self.dp.callback_query.register(self.cb_toggle_signals, F.data == "toggle_signals")
        self.dp.callback_query.register(self.cb_mode_screener, F.data == "mode_screener")
        self.dp.callback_query.register(self.cb_trade_menu, F.data == "trade_menu")
        self.dp.callback_query.register(self.cb_status, F.data == "status")

        # ============================
        #   ВЛОЖЕННОЕ МЕНЮ ТОРГОВЛИ
        # ============================
        self.dp.callback_query.register(self.cb_trade_on, F.data == "trade_on")
        self.dp.callback_query.register(self.cb_trade_off, F.data == "trade_off")
        self.dp.callback_query.register(self.cb_closeall, F.data == "close_all")
        self.dp.callback_query.register(self.cb_back_main, F.data == "back_main")

    # ============================================================
    #   КОМАНДА /start
    # ============================================================

    async def cmd_start(self, message: types.Message):
        await message.answer("Бот запущен. Главное меню:", reply_markup=self.main_menu())

    # ============================================================
    #   МЕНЮ
    # ============================================================

    def main_menu(self):
        kb = InlineKeyboardBuilder()
        kb.button(text="▶️ Включить/Выключить сигналы", callback_data="toggle_signals")
        kb.button(text="🔍 Режим скринера", callback_data="mode_screener")
        kb.button(text="🤖 Режим торговли", callback_data="trade_menu")
        kb.button(text="📊 Статус", callback_data="status")
        kb.adjust(1)
        return kb.as_markup()

    def trade_menu(self):
        kb = InlineKeyboardBuilder()
        kb.button(text="🟢 Включить торговлю", callback_data="trade_on")
        kb.button(text="⛔ Выключить торговлю", callback_data="trade_off")
        kb.button(text="❌ Закрыть все позиции", callback_data="close_all")
        kb.button(text="⬅ Назад", callback_data="back_main")
        kb.adjust(1)
        return kb.as_markup()

    # ============================================================
    #   ХЕНДЛЕРЫ ГЛАВНОГО МЕНЮ
    # ============================================================

    async def cb_toggle_signals(self, call: types.CallbackQuery):
        await call.answer()
        self.signals_enabled = not self.signals_enabled
        state = "🟢 включены" if self.signals_enabled else "🔴 выключены"
        await call.message.edit_text(f"Сигналы теперь {state}", reply_markup=self.main_menu())

    async def cb_mode_screener(self, call: types.CallbackQuery):
        await call.answer()
        self.trading_enabled = False
        await call.message.edit_text("🔍 Режим скринера активирован", reply_markup=self.main_menu())

    async def cb_trade_menu(self, call: types.CallbackQuery):
        await call.answer()
        await call.message.edit_text("Выберите действие:", reply_markup=self.trade_menu())

    async def cb_status(self, call: types.CallbackQuery):
        await call.answer()
        s1 = "🟢 ВКЛ" if self.signals_enabled else "🔴 ВЫКЛ"
        s2 = "🤖 Торговля" if self.trading_enabled else "🔍 Скринер"
        txt = f"Статус:\n\nСигналы: {s1}\nРежим: {s2}\n"
        await call.message.edit_text(txt, reply_markup=self.main_menu())

    # ============================================================
    #   ХЕНДЛЕРЫ ВЛОЖЕННОГО МЕНЮ ТОРГОВЛИ
    # ============================================================

    async def cb_trade_on(self, call: types.CallbackQuery):
        await call.answer()
        self.trading_enabled = True
        await call.message.edit_text("🟢 Торговля включена.", reply_markup=self.trade_menu())

    async def cb_trade_off(self, call: types.CallbackQuery):
        await call.answer()
        self.trading_enabled = False
        await call.message.edit_text("⛔ Торговля выключена.", reply_markup=self.trade_menu())

    async def cb_closeall(self, call: types.CallbackQuery):
        await call.answer()

        if self.closeall_callback and self.positions_ref:
            for sym, pos in list(self.positions_ref.items()):
                await self.closeall_callback(sym, pos)

        self.trading_enabled = False
        await call.message.edit_text("❌ Все позиции закрыты.", reply_markup=self.trade_menu())

    async def cb_back_main(self, call: types.CallbackQuery):
        await call.answer()
        await call.message.edit_text("Главное меню:", reply_markup=self.main_menu())

    # ============================================================
    #   ЗАКРЫТИЕ ВСЕХ ПОЗИЦИЙ
    # ============================================================

    async def _close_all_positions(self):
        if not self.positions_ref:
            return

        for sym, pos in list(self.positions_ref.items()):
            try:
                await self.closeall_callback(sym, pos)
            except Exception as e:
                print(f"[TelegramNotifier] closeall error on {sym}: {e}")

    # ============================================================
    #   БЕЗОПАСНАЯ ОТПРАВКА
    # ============================================================

    async def safe_send_message(self, text: str):
        delay = 1
        for attempt in range(10):
            try:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=text[:4096],
                    reply_markup=self.main_menu()
                )
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except (TelegramNetworkError, TelegramServerError):
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
            except Exception as e:
                print(f"[TG] safe_send_message FAILED: {e}")
                return

    async def safe_send_photo(self, photo_bytes: bytes, caption: str):
        delay = 1
        photo_file = BufferedInputFile(photo_bytes, filename="chart.png")
        for attempt in range(10):
            try:
                await self.bot.send_photo(
                    chat_id=self.chat_id,
                    photo=photo_file,
                    caption=caption[:1024],
                    reply_markup=self.main_menu()
                )
                return
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
            except (TelegramNetworkError, TelegramServerError):
                photo_file = BufferedInputFile(photo_bytes, filename="chart.png")
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
            except Exception:
                await self.safe_send_message(caption)
                return

    # ============================================================
    #   ФАНДИНГ
    # ============================================================

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def _fetch_funding(self, symbol: str) -> float:
        url = f"https://api.bybit.com/v5/market/tickers?category=linear&symbol={symbol}"
        try:
            session = await self._get_session()
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=2)) as resp:
                data = await resp.json()
                funding = float(data["result"]["list"][0]["fundingRate"])
                return funding * 100
        except Exception as e:
            print(f"[TG] Funding fetch error for {symbol}: {e}")
            return 0.0

    async def get_funding(self, symbol: str) -> float:
        now = time.time()
        cached = self._funding_cache.get(symbol)

        if cached:
            ts, value = cached
            if now - ts < self._funding_ttl:
                return value

        value = await self._fetch_funding(symbol)
        self._funding_cache[symbol] = (now, value)
        return value

    def funding_color(self, f: float) -> str:
        if abs(f) < 0.01:
            return "🟢"
        if abs(f) < 0.03:
            return "🟠"
        return "🔴"

    # ============================================================
    #   ГРАФИК 15m
    # ============================================================

    def _make_chart_sync(self, candles):
        if not candles or len(candles) < 20:
            return None

        try:
            # --- подготовка данных ---
            times = [datetime.datetime.fromtimestamp(c["timestamp"] / 1000) for c in candles]
            opens = [c["open"] for c in candles]
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]
            closes = [c["close"] for c in candles]

            # ============================================================
            #   RSI 14 (классический)
            # ============================================================
            period = 14
            deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
            gains = [max(d, 0) for d in deltas]
            losses = [abs(min(d, 0)) for d in deltas]

            rsi = [None] * len(closes)

            if len(closes) > period:
                avg_gain = sum(gains[:period]) / period
                avg_loss = sum(losses[:period]) / period

                if avg_loss == 0:
                    rsi[period] = 100
                else:
                    rs = avg_gain / avg_loss
                    rsi[period] = 100 - (100 / (1 + rs))

                for i in range(period + 1, len(closes)):
                    avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
                    avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period

                    if avg_loss == 0:
                        rsi[i] = 100
                    else:
                        rs = avg_gain / avg_loss
                        rsi[i] = 100 - (100 / (1 + rs))

            # ============================================================
            #   РИСОВАНИЕ ГРАФИКА
            # ============================================================

            fig, ax = plt.subplots(2, 1, figsize=(10, 6),
                                   gridspec_kw={"height_ratios": [3, 1]})

            # --- свечи ---
            for i in range(len(candles)):
                color = "green" if closes[i] >= opens[i] else "red"
                ax[0].plot([times[i], times[i]], [lows[i], highs[i]], color=color)
                ax[0].plot([times[i], times[i]], [opens[i], closes[i]], color=color, linewidth=4)

            ax[0].set_title("15m Chart")
            ax[0].grid(True)

            # --- цена справа ---
            ax[0].yaxis.tick_right()
            ax[0].yaxis.set_label_position("right")

            # --- RSI ---
            ax[1].plot(times, rsi, color="purple", linewidth=1.5)
            ax[1].axhline(70, color="red", linestyle="--", linewidth=1)
            ax[1].axhline(30, color="green", linestyle="--", linewidth=1)
            ax[1].set_ylim(0, 100)
            ax[1].set_title("RSI 14")
            ax[1].grid(True)

            fig.autofmt_xdate()
            buf = io.BytesIO()
            plt.tight_layout()
            plt.savefig(buf, format="png")
            plt.close(fig)
            buf.seek(0)
            return buf.getvalue()

        except Exception as e:
            print(f"[TG] Chart generation error: {e}")
            return None

    async def make_chart(self, candles):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._make_chart_sync, candles)

    # ============================================================
    #   ОТПРАВКА СИГНАЛА
    # ============================================================

    async def send_signal(self, symbol: str, direction: str, signal_type: str,
                          price: float, quality: int, htf_regime: str,
                          candles_15m):

        if not self.signals_enabled:
            print(f"[TG] Signal skipped (signals disabled): {symbol}")
            return

        direction = direction.lower()
        new_dir = "Long" if direction == "long" else "Short"
        color = "🟢" if direction == "long" else "🔴"

        funding = await self.get_funding(symbol)
        f_color = self.funding_color(funding)

        prev_dir = self.last_direction.get(symbol)
        prev_q = self.last_quality.get(symbol, -1)

        if prev_dir is None:
            header = f"{color}{symbol} {signal_type} {new_dir}"
        else:
            if prev_dir != direction:
                old = "Long" if prev_dir == "long" else "Short"
                header = f"{color}{symbol} {signal_type} {old} → {new_dir}"
            else:
                if quality <= prev_q:
                    print(f"[TG] Signal skipped (quality not improved): {symbol} {direction} q={quality} prev_q={prev_q}")
                    return
                header = f"{color}{symbol} {signal_type} {new_dir} (↑ качество)"

        self.last_direction[symbol] = direction
        self.last_quality[symbol] = quality

        text = (
            f"{header}\n\n"
            f"Цена: {price}\n"
            f"Сила сигнала: {quality}/100\n"
            f"Фандинг: {f_color} {funding:.4f}%\n"
            f"HTF: {htf_regime}\n"
        )

        print(f"[TG] Sending signal: {symbol} {direction} q={quality}")

        if not candles_15m or len(candles_15m) < 20:
            await self.safe_send_message(text)
            return

        chart_bytes = await self.make_chart(candles_15m)
        if chart_bytes is None:
            await self.safe_send_message(text)
            return

        await self.safe_send_photo(chart_bytes, text)

    # ============================================================
    #   ПРАВИЛЬНЫЙ POLLING (вариант A)
    # ============================================================

    async def run(self):
        print("[TG] polling started")
        await self.dp.start_polling(self.bot)

    # ============================================================
    #   CLI-КОМАНДЫ (локальное управление ботом)
    # ============================================================

    async def cli_loop(self):
        print("[CLI] Локальное управление включено. Команды:")
        print("  trade on / trade off / trade closeall")
        print("  signals on / signals off")
        print("  status")
        print("  help")

        loop = asyncio.get_running_loop()

        while True:
            cmd = await loop.run_in_executor(None, input, "> ")
            cmd = cmd.strip().lower()

            # --- торговля ---
            if cmd == "trade on":
                self.trading_enabled = True
                print("[CLI] Торговля включена")

            elif cmd == "trade off":
                self.trading_enabled = False
                print("[CLI] Торговля выключена")

            elif cmd == "trade closeall":
                print("[CLI] Закрываю все позиции...")
                if self.positions_ref and self.closeall_callback:
                    for sym, pos in list(self.positions_ref.items()):
                        try:
                            await self.closeall_callback(sym, pos)
                        except Exception as e:
                            print(f"[CLI] Ошибка закрытия {sym}: {e}")
                self.trading_enabled = False
                print("[CLI] Все позиции закрыты")

            # --- сигналы ---
            elif cmd == "signals on":
                self.signals_enabled = True
                print("[CLI] Сигналы включены")

            elif cmd == "signals off":
                self.signals_enabled = False
                print("[CLI] Сигналы выключены")

            # --- статус ---
            elif cmd == "status":
                print("[CLI] Статус:")
                print(f"  Сигналы: {'ON' if self.signals_enabled else 'OFF'}")
                print(f"  Торговля: {'ON' if self.trading_enabled else 'OFF'}")
                if self.positions_ref:
                    print(f"  Открытых позиций: {len(self.positions_ref)}")
                else:
                    print("  Открытых позиций: 0")

            # --- помощь ---
            elif cmd == "help":
                print("Команды:")
                print("  trade on / trade off / trade closeall")
                print("  signals on / signals off")
                print("  status")

            else:
                print("[CLI] Неизвестная команда. help — список команд.")
