# v31_live_bot.py
# V31 ELITE — Live WebSocket Bot (Bybit, 5m + HTF WS, Full Engine Stack, .env-based keys)
# Режимы:
#   🔍 Скринер  — только сигналы (trading_enabled = False)
#   🤖 Торговля — сигналы + торговля (trading_enabled = True)

import os
import sys
import time
import hmac
import hashlib
import requests
import asyncio
import json
import datetime
import aiohttp
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import deque
from typing import Any

from signal_storage import SignalStorage

from dotenv import load_dotenv
import websockets  # pip install websockets

from telegram_bot import TelegramNotifier

# Web-панель (FastAPI + WebNotifier)
from web_notifier import web_notifier, app as web_app, WEB_PANEL_PORT
import uvicorn

# =====================================================
#   Windows FIX — стабильный event loop
# =====================================================
if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# =====================================================
# AUTO RESTART MANAGER
# =====================================================

class AutoRestartManager:
    def __init__(self, polling_timeout=60, ws_timeout=60, heartbeat_timeout=90):
        self.last_polling = time.time()
        self.last_ws = time.time()
        self.last_heartbeat = time.time()

        self.polling_timeout = polling_timeout
        self.ws_timeout = ws_timeout
        self.heartbeat_timeout = heartbeat_timeout

        # флаг активности WS: начинаем контролировать только после старта WS
        self.ws_active = False

    def ping_polling(self):
        self.last_polling = time.time()

    def ping_ws(self):
        self.last_ws = time.time()

    def ping_heartbeat(self):
        self.last_heartbeat = time.time()

    async def monitor(self):
        while True:
            now = time.time()

            # Telegram больше не рестартит процесс
            # if now - self.last_polling > self.polling_timeout:
            #     print("[AUTO-RESTART] Telegram polling завис. Перезапуск процесса...")
            #     self.restart_process()

            # контролируем WS только если он реально активен
            if self.ws_active and now - self.last_ws > self.ws_timeout:
                print("[AUTO-RESTART] WebSocket завис. Перезапуск процесса...")
                self.restart_process()

            if now - self.last_heartbeat > self.heartbeat_timeout:
                print("[AUTO-RESTART] Event loop / heartbeat завис. Перезапуск процесса...")
                self.restart_process()

            await asyncio.sleep(5)

    def restart_process(self):
        print("[AUTO-RESTART] Выполняю полный рестарт процесса...")
        python = sys.executable
        os.execv(python, [python] + sys.argv)


# =====================================================
# LOAD .ENV
# =====================================================

load_dotenv()

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY")
BYBIT_API_SECRET = os.getenv("BYBIT_API_SECRET")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID")) if os.getenv("TELEGRAM_CHAT_ID") else None

if not BYBIT_API_KEY or not BYBIT_API_SECRET:
    raise RuntimeError("BYBIT_API_KEY / BYBIT_API_SECRET not found in .env")

# =====================================================
# CONFIG
# =====================================================

BYBIT_BASE_URL = "https://api.bybit.com"
BYBIT_WS_URL = "wss://stream.bybit.com/v5/public/linear"

SYMBOLS = [
    "WIFUSDT",
    "PENGUUSDT",
    "SPXUSDT",
    "PUMPUSDT",
    "MEWUSDT",
    "1000PEPEUSDT",
    "SUIUSDT",
    "OPUSDT",
    "PIPPINUSDT",
]

INITIAL_EQUITY = 10_000.0
DAILY_LOSS_LIMIT_PCT = 0.05  # 5%

TAKER_FEE = 0.00055
SLIPPAGE_ENTRY = 0.0002
SLIPPAGE_EXIT = 0.00015

MIN_SIGNAL_QUALITY = 0.55
MIN_HTF_ALIGNMENT = -0.05
MIN_BAR_GAP = 2

TRADES_LOG_PATH = Path("trades_log.jsonl")

auto = AutoRestartManager()

# глобальное состояние equity для REPL
EQUITY_STATE = {"equity": INITIAL_EQUITY}

# =====================================================
# ENGINES (V31 ELITE)
# =====================================================

from elite_structure_engine import EliteStructureEngine
from elite_regime_engine import EliteRegimeEngine
from elite_htf_sync import EliteHTFSync
from elite_trend_engine import EliteTrendEngine
from elite_signal_router import EliteSignalRouter
from risk_engine_v31 import RiskEngineV31
from elite_exit_engine import EliteExitEngine

# =====================================================
# BYBIT DOWNLOADER
# =====================================================

class BybitDownloaderAsync:
    BASE_URL = f"{BYBIT_BASE_URL}/v5/market/kline"

    INTERVAL_MAP = {
        "1m": "1",
        "3m": "3",
        "5m": "5",
        "15m": "15",
        "30m": "30",
        "1h": "60",
        "4h": "240",
        "1d": "D",
    }

    def __init__(self, max_retries=5, retry_delay=1.0, timeout=10, max_parallel=4):
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.timeout = timeout
        self.sem = asyncio.Semaphore(max_parallel)   # <<< ограничение параллелизма

    async def _fetch(self, session, params):
        """Один запрос к Bybit с retry + backoff + ограничением параллелизма."""
        async with self.sem:
            for attempt in range(1, self.max_retries + 1):
                try:
                    async with session.get(
                        self.BASE_URL,
                        params=params,
                        timeout=self.timeout
                    ) as resp:

                        if resp.status != 200:
                            raise Exception(f"HTTP {resp.status}")

                        data = await resp.json()
                        return data

                except Exception as e:
                    print(f"[Bybit] Error attempt {attempt}/{self.max_retries}: {e}")
                    await asyncio.sleep(self.retry_delay * attempt)

            print("[Bybit] FAILED after retries.")
            return None

    async def download(self, symbol: str, interval: str, total_needed: int):
        print(f"\n[Bybit] Async downloading {symbol} {interval} ...")

        if interval not in self.INTERVAL_MAP:
            print(f"[Bybit] Unsupported interval: {interval}")
            return []

        bybit_tf = self.INTERVAL_MAP[interval]
        all_data = []
        end_time = None

        async with aiohttp.ClientSession() as session:
            while len(all_data) < total_needed:
                params = {
                    "category": "linear",
                    "symbol": symbol.upper(),
                    "interval": bybit_tf,
                    "limit": 1000,
                }

                if end_time:
                    params["end"] = end_time

                data = await self._fetch(session, params)
                if not data:
                    break

                if data.get("retCode") != 0:
                    print(f"[Bybit] API error: {data.get('retMsg')}")
                    break

                list_data = data.get("result", {}).get("list", [])
                if not list_data:
                    break

                list_data = list(reversed(list_data))
                all_data = list_data + all_data

                oldest_ts = int(list_data[0][0])
                end_time = oldest_ts - 1

                print(f"[Bybit] {interval}: {len(all_data)} candles")

                await asyncio.sleep(0.02)  # лёгкая пауза, не блокирует event loop

        candles = []
        for k in all_data:
            try:
                candles.append({
                    "timestamp": int(k[0]),
                    "open": float(k[1]),
                    "high": float(k[2]),
                    "low": float(k[3]),
                    "close": float(k[4]),
                    "volume": float(k[5]) if len(k) > 5 else 0.0,
                })
            except:
                pass

        print(f"[Bybit] {interval} DONE. Total: {len(candles)} candles.")
        return candles

# =====================================================
# BYBIT BROKER
# =====================================================

class BybitBroker:
    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = BYBIT_BASE_URL,
        taker_fee: float = TAKER_FEE,
        slippage_entry: float = SLIPPAGE_ENTRY,
        slippage_exit: float = SLIPPAGE_EXIT,
    ):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.base_url = base_url
        self.taker_fee = taker_fee
        self.slippage_entry = slippage_entry
        self.slippage_exit = slippage_exit

    def _sign(self, params: Dict) -> str:
        sorted_params = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
        return hmac.new(self.api_secret, sorted_params.encode(), hashlib.sha256).hexdigest()

    def _private_post(self, path: str, body: Dict) -> Dict:
        url = self.base_url + path
        ts = int(time.time() * 1000)

        body["api_key"] = self.api_key
        body["timestamp"] = ts
        body["sign"] = self._sign(body)

        r = requests.post(url, json=body, timeout=10)
        r.raise_for_status()
        return r.json()

    async def market_order(
        self,
        symbol: str,
        side: str,
        size: float,
        price: float,
        is_entry: bool = True,
    ) -> Tuple[float, float]:
        side_lower = side.lower()
        if side_lower in ("long", "buy"):
            real_side = "Buy"
            slippage = self.slippage_entry if is_entry else self.slippage_exit
            fill_price = price * (1 + slippage)
        elif side_lower in ("short", "sell"):
            real_side = "Sell"
            slippage = self.slippage_entry if is_entry else self.slippage_exit
            fill_price = price * (1 - slippage)
        else:
            real_side = "Buy"
            slippage = self.slippage_entry if is_entry else self.slippage_exit
            fill_price = price * (1 + slippage)

        fee = fill_price * size * self.taker_fee

        body = {
            "category": "linear",
            "symbol": symbol,
            "side": real_side,
            "orderType": "Market",
            "qty": str(size),
            "timeInForce": "GoodTillCancel",
        }

        try:
            resp = self._private_post("/v5/order/create", body)
            if resp.get("retCode") != 0:
                print("[BybitBroker] Order error:", resp.get("retMsg"))
        except Exception as e:
            print("[BybitBroker] Exception while sending order:", e)

        return fill_price, fee

# =====================================================
# LOGGING
# =====================================================

def log_trade(record: dict):
    with TRADES_LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


# =====================================================
# HELPERS
# =====================================================

def get_atr_percentile(regime: Dict) -> float:
    if "atr_percentile" in regime:
        try:
            return float(regime.get("atr_percentile", 0.5))
        except Exception:
            return 0.5

    vol = regime.get("volatility")
    if isinstance(vol, dict):
        htf = vol.get("htf")
        if isinstance(htf, dict) and "level" in htf:
            try:
                return float(htf.get("level", 0.5))
            except Exception:
                pass
        if "level" in vol:
            try:
                return float(vol.get("level", 0.5))
            except Exception:
                pass

    return 0.5


def smart_filter_v4(signal: Optional[Dict], regime: Dict, htf: Dict) -> Optional[Dict]:
    if not signal:
        return None

    # --- базовый ATR-фильтр ---
    atr_pct = get_atr_percentile(regime)
    if atr_pct < 0.10:
        return None
    if atr_pct > 0.95:
        return None

    direction = signal.get("signal")
    htf_bias  = htf.get("bias")

    # --- базовый HTF bias-фильтр ---
    if htf_bias == "bullish" and direction == "short":
        return None
    if htf_bias == "bearish" and direction == "long":
        return None

    align = htf.get("alignment_score", 0.0)
    signed_strength = htf.get("signed_trend_strength", 0.0)
    quality = signal.get("quality", 0.0)

    # --- мягкий анти-флет фильтр ---
    if abs(signed_strength) < 0.10 and quality < 0.60:
        return None

    if abs(align) < 0.05 and quality < 0.58:
        return None

    return signal


def initial_sl(direction: str, entry_price: float, atr: Optional[float]) -> float:
    if not atr or atr <= 0:
        return entry_price
    atr_mult = 1.5
    if direction == "long":
        return entry_price - atr * atr_mult
    else:
        return entry_price + atr * atr_mult


# =====================================================
# ANALYZER
# =====================================================

def analyze_symbol_core(
    symbol: str,
    candles_5m: List[Dict],
    htf_15m: List[Dict],
    htf_1h: List[Dict],
    htf_4h: List[Dict],
    engines: Dict,
) -> Optional[Dict]:

    if len(candles_5m) < 600:
        return None

    current_bar = candles_5m[-1]
    price = float(current_bar["close"])
    ts = current_bar["timestamp"]

    struct_slice = candles_5m[-400:]
    regime_slice = candles_5m[-600:]
    atr_slice = candles_5m[-200:]

    structure = engines["structure"].analyze(struct_slice)

    try:
        regime = engines["regime"].detect(regime_slice, structure)
    except TypeError:
        regime = engines["regime"].detect(regime_slice)

    htf = engines["htf"].analyze(ts, htf_15m, htf_1h, htf_4h)

    if not structure or not regime or not htf:
        return None

    trend_signal = engines["trend"].evaluate(structure, regime, htf, symbol=symbol)
    reversal_signal = None  # reversal отключён

    signal = engines["router"].route(trend_signal, reversal_signal, regime, htf, symbol=symbol)

    # === SMART FILTER v4 ===
    signal = smart_filter_v4(signal, regime, htf)
    if not signal:
        return None

    # === BTC MODE (как в бэктестере) ===
    if symbol == "BTCUSDT":
        atr_pct = get_atr_percentile(regime)

        if atr_pct < 0.25:
            return None

        if signal.get("quality", 0.0) < 0.60:
            return None

        if htf.get("alignment_score", 0.0) < 0.55:
            return None

    # === ALTCOINS MODE ===
    else:
        q = signal.get("quality", 0.0)
        if q < MIN_SIGNAL_QUALITY:
            return None
        alignment = htf.get("alignment_score", 0.0)
        if alignment < MIN_HTF_ALIGNMENT:
            return None

    # === GAP FILTER (как в бэктестере) ===
    last_close = engines["last_trade_close_bar"].get(symbol, -999)

    if symbol == "BTCUSDT":
        min_gap = 5
    else:
        min_gap = MIN_BAR_GAP

    # если после закрытия прошло меньше min_gap баров → сигнал запрещён
    if (len(candles_5m) - last_close) < min_gap:
        return None

    # === ATR & SL ===
    atr = engines["regime"]._atr(atr_slice, 14)
    if not atr or atr <= 0:
        return None

    sl = initial_sl(signal["signal"], price, atr)

    return {
        "symbol": symbol,
        "direction": signal["signal"],
        "type": signal["type"],
        "quality": signal["quality"],
        "price": price,
        "sl": sl,
        "atr": atr,
        "regime": regime.get("regime", "UNKNOWN"),
        "htf_regime": htf.get("htf_regime", "UNKNOWN"),
    }


# =====================================================
# SIGNAL DISPATCHER → TELEGRAM + WEB (с буферизацией)
# =====================================================

async def signal_dispatcher(signal_queue: asyncio.Queue, notifier: Optional[TelegramNotifier]):
    from collections import deque as _deque
    from signal_storage import SignalStorage

    pending = _deque()
    print("[DISPATCHER] started")

    while True:
        # --- если есть отложенные сигналы ---
        if pending:
            signal = pending[0]

            # сохраняем в JSON (если ещё не сохранён)
            SignalStorage.add(signal)

            try:
                # Telegram (если включён)
                if notifier is not None:
                    await notifier.send_signal(
                        symbol=signal["symbol"],
                        direction=signal["direction"],
                        signal_type=signal["type"],
                        price=signal["price"],
                        quality=signal["quality"],
                        htf_regime=signal["htf_regime"],
                        candles_15m=signal["candles_15m"],
                    )

                # Web — всегда
                await web_notifier.send_signal(
                    symbol=signal["symbol"],
                    direction=signal["direction"],
                    signal_type=signal["type"],
                    price=signal["price"],
                    quality=signal["quality"],
                    htf_regime=signal["htf_regime"],
                    candles_15m=signal["candles_15m"],
                )

                pending.popleft()

            except Exception as e:
                print(f"[Notifier] Error sending pending signal for {signal.get('symbol')}: {e}")
                await asyncio.sleep(5)

            continue

        # --- получаем новый сигнал ---
        signal = await signal_queue.get()

        # сохраняем в JSON
        SignalStorage.add(signal)

        try:
            # Telegram (если включён)
            if notifier is not None:
                await notifier.send_signal(
                    symbol=signal["symbol"],
                    direction=signal["direction"],
                    signal_type=signal["type"],
                    price=signal["price"],
                    quality=signal["quality"],
                    htf_regime=signal["htf_regime"],
                    candles_15m=signal["candles_15m"],
                )

            # Web — всегда
            await web_notifier.send_signal(
                symbol=signal["symbol"],
                direction=signal["direction"],
                signal_type=signal["type"],
                price=signal["price"],
                quality=signal["quality"],
                htf_regime=signal["htf_regime"],
                candles_15m=signal["candles_15m"],
            )

        except Exception as e:
            print(f"[Notifier] Error sending signal for {signal.get('symbol')}, buffering: {e}")
            pending.append(signal)

        finally:
            signal_queue.task_done()

# =====================================================
# WEBSOCKET LOOP (one-time history load, fast reconnect)
# =====================================================

async def ws_trading_loop(
    symbols,
    engines,
    notifier: Optional[TelegramNotifier],
    signal_queue: asyncio.Queue,
    shared_positions: Dict[str, Dict],
):
    # --- общий стейт, который живёт между реконнектами ---
    downloader = BybitDownloaderAsync()
    broker = BybitBroker(BYBIT_API_KEY, BYBIT_API_SECRET)

    history_5m: Dict[str, deque] = {s: deque(maxlen=1000) for s in symbols}
    htf_15m: Dict[str, deque] = {s: deque(maxlen=1000) for s in symbols}
    htf_1h: Dict[str, deque] = {s: deque(maxlen=1000) for s in symbols}
    htf_4h: Dict[str, deque] = {s: deque(maxlen=1000) for s in symbols}

    positions: Dict[str, Dict] = shared_positions

    equity = EQUITY_STATE.get("equity", INITIAL_EQUITY)
    if equity <= 0:
        equity = INITIAL_EQUITY
    EQUITY_STATE["equity"] = equity

    day_start = datetime.date.today()
    day_start_equity = equity
    last_trade_close_bar: Dict[str, int] = {s: -999 for s in symbols}

    async def close_position_from_telegram(symbol, pos):
        direction = pos["direction"]
        size = pos["size"]
        entry = pos["entry"]

        side = "sell" if direction == "long" else "buy"

        try:
            exit_price, exit_fee = await broker.market_order(
                symbol,
                side,
                size,
                entry,
                is_entry=False,
            )
        except Exception as e:
            print(f"[CLOSEALL] Ошибка закрытия {symbol}: {e}")
            exit_price = entry
            exit_fee = 0.0

        if direction == "long":
            gross = (exit_price - entry) * size
        else:
            gross = (entry - exit_price) * size

        pnl = gross - (pos["entry_fee"] + exit_fee)
        print(f"[CLOSEALL] {symbol} закрыт вручную. PnL={pnl:.2f}")

        positions.pop(symbol, None)

        # === обновляем last_trade_close_bar (как в бэктестере) ===
        bar_index = len(history_5m[symbol])   
        last_trade_close_bar[symbol] = bar_index
        engines["last_trade_close_bar"][symbol] = bar_index

        if notifier is not None:
            notifier.positions_ref = positions
            notifier.closeall_callback = close_position_from_telegram

        web_notifier.positions_ref = positions
        web_notifier.closeall_callback = close_position_from_telegram

    # --- ОДНОКРАТНАЯ загрузка истории ---
    print("⏳ Loading history asynchronously (one-time)...")

    tasks = []
    for s in symbols:
        tasks.append(downloader.download(s, "5m", 800))
        tasks.append(downloader.download(s, "15m", 400))
        tasks.append(downloader.download(s, "1h", 400))
        tasks.append(downloader.download(s, "4h", 400))

    results = await asyncio.gather(*tasks)

    idx = 0
    for s in symbols:
        history_5m[s].extend(results[idx]); idx += 1
        htf_15m[s].extend(results[idx]); idx += 1
        htf_1h[s].extend(results[idx]); idx += 1
        htf_4h[s].extend(results[idx]); idx += 1

    print("🔥 One-time async history load complete.")

    state = {
        "equity": equity,
        "day_start": day_start,
        "day_start_equity": day_start_equity,
        "last_trade_close_bar": last_trade_close_bar,
    }

    delay = 1

    while True:
        print(f"[WS] Starting WS loop (delay={delay}s)...")
        try:
            await ws_trading_loop_once(
                symbols,
                engines,
                notifier,
                signal_queue,
                positions,
                history_5m,
                htf_15m,
                htf_1h,
                htf_4h,
                broker,
                state,
            )
        except Exception as e:
            print(f"[WS] ERROR in ws_trading_loop_once: {e}")
            print("[WS] Restarting WS loop without reloading history...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30)
            continue

        delay = 1
        print("[WS] ws_trading_loop_once finished unexpectedly, restarting...")
        await asyncio.sleep(1)


async def ws_trading_loop_once(
    symbols,
    engines,
    notifier,
    signal_queue,
    positions,
    history_5m,
    htf_15m,
    htf_1h,
    htf_4h,
    broker,
    state,
):
    equity = state["equity"]
    day_start = state["day_start"]
    day_start_equity = state["day_start_equity"]
    last_trade_close_bar = state["last_trade_close_bar"]

    topics = []
    for s in symbols:
        topics.append(f"kline.5.{s}")
        topics.append(f"kline.15.{s}")
        topics.append(f"kline.60.{s}")
        topics.append(f"kline.240.{s}")

    auto.ws_active = True
    last_heartbeat = time.time()

    async def keepalive(ws):
        while True:
            try:
                pong = await ws.ping()
                await asyncio.wait_for(pong, timeout=10)
            except Exception:
                print("[WS] keepalive failed — reconnecting...")
                return
            await asyncio.sleep(20)

    async with websockets.connect(
        BYBIT_WS_URL,
        ping_interval=None,
        ping_timeout=None,
        close_timeout=5,
        max_queue=None,
        max_size=2**23,
    ) as ws:

        # подписка
        await ws.send(json.dumps({"op": "subscribe", "args": topics}))
        print("[WS] Subscribed:", topics)

        # ручной keepalive
        asyncio.create_task(keepalive(ws))

        while True:
            # heartbeat
            if time.time() - last_heartbeat > 30:
                print(f"[WS] alive {datetime.datetime.now().strftime('%H:%M:%S')}")
                last_heartbeat = time.time()
                auto.ping_ws()
                auto.ping_heartbeat()

            # recv с таймаутом
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=30)
            except asyncio.TimeoutError:
                print("[WS] recv timeout — reconnecting...")
                raise
            except Exception as e:
                print(f"[WS] Disconnected inside loop: {e}")
                raise

            auto.ping_ws()
            auto.ping_heartbeat()

            # парсинг
            try:
                data = json.loads(msg)
            except:
                continue

            topic = data.get("topic", "")
            if not topic.startswith("kline."):
                continue

            parts = topic.split(".")
            if len(parts) != 3:
                continue

            tf = parts[1]
            symbol = parts[2]

            symbol_hist_5m = history_5m.get(symbol)
            if symbol_hist_5m is None:
                continue

            for k in data.get("data", []):
                ts = int(k["start"])
                o = float(k["open"])
                h = float(k["high"])
                l = float(k["low"])
                c = float(k["close"])
                vol = float(k.get("volume", 0.0))
                confirm = k.get("confirm", False)

                candle = {
                    "timestamp": ts,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": vol,
                }

                # обновление истории
                if tf == "5":
                    symbol_hist_5m.append(candle)
                elif tf == "15" and confirm:
                    htf_15m[symbol].append(candle)
                    continue
                elif tf == "60" and confirm:
                    htf_1h[symbol].append(candle)
                    continue
                elif tf == "240" and confirm:
                    htf_4h[symbol].append(candle)
                    continue
                else:
                    continue

                if tf != "5":
                    continue

                len_5m = len(symbol_hist_5m)
                if len_5m < 600:
                    continue

                # управление позицией
                if symbol in positions and confirm:
                    pos = positions[symbol]
                    last_price = c

                    last_600 = list(symbol_hist_5m)[-600:]
                    last_200 = last_600[-200:]

                    try:
                        regime_live = engines["regime"].detect(last_600)
                        atr_live = engines["regime"]._atr(last_200, 14) or 0.0
                    except:
                        regime_live = None
                        atr_live = 0.0

                    if regime_live:
                        try:
                            updated = engines["exit"].manage_position(
                                pos, last_price, atr_live,
                                regime_live["regime"], len_5m
                            )
                        except:
                            updated = pos

                        positions[symbol] = updated

                        if updated.get("status") == "CLOSED":
                            exit_price = updated["exit_price"]
                            size = updated["size"]
                            direction = updated["direction"]

                            side = "sell" if direction == "long" else "buy"

                            try:
                                _, exit_fee = await broker.market_order(
                                    symbol, side, size, exit_price, is_entry=False
                                )
                            except:
                                exit_fee = 0.0

                            if direction == "long":
                                gross = (exit_price - updated["entry"]) * size
                            else:
                                gross = (updated["entry"] - exit_price) * size

                            pnl = gross - (updated["entry_fee"] + exit_fee)
                            equity += pnl
                            EQUITY_STATE["equity"] = equity
                            state["equity"] = equity

                            positions.pop(symbol, None)

                if not confirm:
                    continue

                # анализ сигнала
                try:
                    signal = analyze_symbol_core(
                        symbol,
                        list(symbol_hist_5m),
                        list(htf_15m[symbol]),
                        list(htf_1h[symbol]),
                        list(htf_4h[symbol]),
                        engines,
                    )
                except:
                    continue

                if signal:
                    mode_trade = web_notifier.trading_enabled

                    print(
                        f"[SIGNAL] {signal['symbol']} {signal['direction']} "
                        f"type={signal['type']} q={int(signal['quality']*100)} "
                        f"price={signal['price']:.4f} htf={signal['htf_regime']} "
                        f"mode={'TRADE' if mode_trade else 'SCAN'}"
                    )

                    await signal_queue.put({
                        "symbol": signal["symbol"],
                        "direction": signal["direction"],
                        "type": signal["type"],
                        "price": signal["price"],
                        "quality": int(signal["quality"] * 100),
                        "htf_regime": signal["htf_regime"],
                        "candles_15m": list(htf_15m[symbol])[-200:],
                    })

                    if mode_trade and symbol not in positions:

                        # === MAX OPEN POSITIONS LIMIT ===
                        MAX_OPEN_POSITIONS = 5
                        if len(positions) >= MAX_OPEN_POSITIONS:
                            print(f"[LIMIT] Пропуск входа: достигнут лимит {MAX_OPEN_POSITIONS} открытых позиций.")
                            continue

                        # === GAP FILTER ===
                        min_gap = 5 if symbol == "BTCUSDT" else MIN_BAR_GAP
                        if (len_5m - last_trade_close_bar[symbol]) < min_gap:
                            continue

                        # === SPREAD FILTER ===
                        try:
                            orderbook = await broker.get_orderbook(symbol)
                            bid = orderbook["bid"]
                            ask = orderbook["ask"]
                            mid = (bid + ask) / 2
                            spread_pct = (ask - bid) / mid * 100
                        except:
                            continue

                        MAX_SPREAD = 0.05 if symbol in ("BTCUSDT", "ETHUSDT") else 0.25
                        if spread_pct > MAX_SPREAD:
                            print(f"[SPREAD] Пропуск входа: spread={spread_pct:.3f}% > {MAX_SPREAD}%")
                            continue

                        # === VOLATILITY SPIKE PROTECTION ===
                        c1 = candles_5m[-1]
                        atr = signal["atr"]
                        range1 = c1["high"] - c1["low"]
                        body1 = abs(c1["close"] - c1["open"])

                        if range1 > atr * 2.0:
                            print("[VOLA] Пропуск входа: range spike (памп).")
                            continue

                        if body1 > atr * 1.5:
                            print("[VOLA] Пропуск входа: body spike (памп).")
                            continue

                        # === RISK ENGINE ===
                        try:
                            size, risk_pct = engines["risk"].allocate(
                                equity=equity,
                                entry_price=signal["price"],
                                stop_price=signal["sl"],
                                regime=signal["regime"],
                                atr=signal["atr"],
                            )
                        except:
                            continue

                        if size > 0:
                            # === MARKET ORDER WITH TIMEOUT ===
                            try:
                                entry_price, entry_fee = await asyncio.wait_for(
                                    broker.market_order(
                                        symbol,
                                        signal["direction"],
                                        size,
                                        signal["price"],
                                        is_entry=True,
                                    ),
                                    timeout=3  # 3 секунды — оптимально для Bybit
                                )
                            except asyncio.TimeoutError:
                                print("[BROKER] market_order timeout — отмена входа.")
                                continue
                            except Exception as e:
                                print(f"[BROKER] Ошибка market_order: {e}")
                                continue

                            # === SLIPPAGE GUARD ===
                            slippage_pct = abs(entry_price - signal["price"]) / signal["price"] * 100
                            if slippage_pct > 0.3:
                                print(f"[SLIPPAGE] Слишком большое проскальзывание {slippage_pct:.3f}%. Закрываю вход.")
                                try:
                                    await broker.market_order(
                                        symbol,
                                        "sell" if signal["direction"] == "long" else "buy",
                                        size,
                                        entry_price,
                                        is_entry=False,
                                    )
                                except:
                                    pass
                                continue

                            # === CREATE POSITION ===
                            positions[symbol] = {
                                "symbol": symbol,
                                "direction": signal["direction"],
                                "entry": entry_price,
                                "sl": signal["sl"],
                                "sl_initial": signal["sl"],
                                "size": size,
                                "risk_pct": risk_pct,
                                "entry_fee": entry_fee,
                                "status": "OPEN",
                                "partial_taken": False,
                                "open_bar": len_5m,
                            }

                            print(
                                f"OPEN {symbol} {signal['direction']} | "
                                f"entry={entry_price:.4f} sl={signal['sl']:.4f} "
                                f"risk={risk_pct*100:.2f}% Equity={equity:.2f}"
                            )


# =====================================================
# TG HEARTBEAT
# =====================================================

async def tg_heartbeat():
    while True:
        print(f"[TG] alive {datetime.datetime.now().strftime('%H:%M:%S')}")
        auto.ping_heartbeat()
        await asyncio.sleep(30)

# =====================================================
# TG POLLING LIVENESS
# =====================================================

async def tg_polling_liveness():
    while True:
        auto.ping_polling()
        await asyncio.sleep(5)

# =====================================================
# TELEGRAM RUNNER (отдельная задача, с автоперезапуском)
# =====================================================

async def telegram_runner(notifier: TelegramNotifier):
    while True:
        try:
            await notifier.run()
        except Exception as e:
            print(f"[TG] notifier.run() crashed: {e}")
            await asyncio.sleep(5)
            print("[TG] restarting notifier.run()...")

# =====================================================
# REPL (консольное управление на VPS)
# =====================================================

async def repl(notifier: Optional[TelegramNotifier], positions: Dict[str, Dict]):
    print("[REPL] Console control ready. Commands: trade on/off, status, closeall, closeall force, help")
    while True:
        try:
            cmd = await asyncio.to_thread(input, "> ")
        except EOFError:
            await asyncio.sleep(1)
            continue

        cmd = cmd.strip()
        if not cmd:
            continue

        low = cmd.lower()

        if low in ("help", "h", "?"):
            print("Commands:")
            print("  trade on           - enable trading")
            print("  trade off          - disable trading (signals only)")
            print("  status             - show trading status and positions")
            print("  closeall           - close all OPEN positions (with confirm)")
            print("  closeall force     - close ALL positions known to bot (with confirm)")
            print("  scan               - info: scanning is continuous, no manual trigger needed")
            continue

        if low == "trade on":
            if notifier is not None:
                notifier.trading_enabled = True
            web_notifier.trading_enabled = True
            print("[REPL] Trading enabled.")
            continue

        if low == "trade off":
            if notifier is not None:
                notifier.trading_enabled = False
            web_notifier.trading_enabled = False
            print("[REPL] Trading disabled (SCAN mode).")
            continue

        if low == "status":
            eq = EQUITY_STATE.get("equity", None)
            print(f"[REPL] Trading: {'ON' if web_notifier.trading_enabled else 'OFF'}")
            if eq is not None:
                print(f"[REPL] Equity: {eq:.2f}")
            print(f"[REPL] Positions: {len(positions)}")
            if positions:
                print("[REPL] Open positions:")
                for sym, pos in positions.items():
                    direction = pos.get("direction", "?")
                    entry = pos.get("entry", 0.0)
                    size = pos.get("size", 0.0)
                    sl = pos.get("sl", 0.0)
                    status = pos.get("status", "?")
                    print(
                        f"  {sym} {direction} {size} @ {entry:.4f} SL={sl:.4f} status={status}"
                    )
            continue

        if low.startswith("closeall"):
            force = "force" in low
            if force:
                print("[REPL] Закрыть ВСЕ позиции (включая не-OPEN)? (yes/no)")
            else:
                print("[REPL] Закрыть все открытые позиции (status=OPEN)? (yes/no)")

            ans = await asyncio.to_thread(input, ">> ")
            ans = ans.strip().lower()
            if ans not in ("yes", "y"):
                print("[REPL] Отменено.")
                continue

            if notifier is None or not hasattr(notifier, "closeall_callback") or notifier.closeall_callback is None:
                print("[REPL] closeall_callback ещё не инициализирован (WS loop не запущен или Telegram отключён).")
                continue

            to_close = []
            for sym, pos in list(positions.items()):
                if force:
                    to_close.append((sym, pos))
                else:
                    if pos.get("status") == "OPEN":
                        to_close.append((sym, pos))

            if not to_close:
                print("[REPL] Нет позиций для закрытия.")
                continue

            print(f"[REPL] Закрываю {len(to_close)} позиций...")
            for sym, pos in to_close:
                try:
                    await notifier.closeall_callback(sym, pos)
                except Exception as e:
                    print(f"[REPL] Ошибка закрытия {sym}: {e}")
            print("[REPL] closeall завершён.")
            continue

        if low == "scan":
            print("[REPL] Сканирование рынка идёт постоянно. Отдельная команда не требуется.")
            continue

        print(f"[REPL] Unknown command: {cmd}. Type 'help' for list of commands.")

# =====================================================
# WEB PANEL RUNNER
# =====================================================

async def run_web_panel():
    config = uvicorn.Config(web_app, host="0.0.0.0", port=WEB_PANEL_PORT, log_level="info")
    server = uvicorn.Server(config)
    await server.serve()

# =====================================================
# ENTRY POINT
# =====================================================

async def main():
    engines = {
        "structure": EliteStructureEngine(),
        "regime": EliteRegimeEngine(),
        "htf": EliteHTFSync(),
        "trend": EliteTrendEngine(),
        "router": EliteSignalRouter(),
        "risk": RiskEngineV31(),
        "exit": EliteExitEngine(),
        "last_trade_close_bar": {s: -999 for s in SYMBOLS},
    }

    # --- Telegram включён? ---
    from telegram_bot import (
        TelegramNotifier,
        TELEGRAM_ENABLED,
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_CHAT_ID,
    )

    notifier: Optional[TelegramNotifier] = None
    if TELEGRAM_ENABLED and TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        print("[TG] Telegram включён (TELEGRAM_ENABLED=true).")
    else:
        print("[TG] Telegram отключён (нет токена или TELEGRAM_ENABLED=false).")

    signal_queue = asyncio.Queue()

    # общая ссылка на позиции для REPL и WS
    shared_positions: Dict[str, Dict] = {}

    # --- Запуск фоновых задач ---
    asyncio.create_task(auto.monitor())
    asyncio.create_task(tg_heartbeat())
    asyncio.create_task(tg_polling_liveness())

    # Диспетчер сигналов — ВСЕГДА, notifier может быть None
    asyncio.create_task(signal_dispatcher(signal_queue, notifier))

    if notifier:
        asyncio.create_task(telegram_runner(notifier))
        asyncio.create_task(repl(notifier, shared_positions))
    else:
        asyncio.create_task(repl(None, shared_positions))

    # WS loop всегда работает
    asyncio.create_task(
        ws_trading_loop(
            SYMBOLS,
            engines,
            notifier,
            signal_queue,
            shared_positions
        )
    )

    # Web-панель всегда работает
    asyncio.create_task(run_web_panel())

    # --- Синхронизация флагов ---
    async def sync_flags():
        while True:
            if notifier:
                notifier.trading_enabled = web_notifier.trading_enabled
                notifier.signals_enabled = web_notifier.signals_enabled
                notifier.telegram_enabled = web_notifier.telegram_enabled

                web_notifier.trading_enabled = notifier.trading_enabled
                web_notifier.signals_enabled = notifier.signals_enabled
                web_notifier.telegram_enabled = notifier.telegram_enabled
            else:
                web_notifier.telegram_enabled = False

            await asyncio.sleep(1)

    asyncio.create_task(sync_flags())

    # вечный цикл
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
