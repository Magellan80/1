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
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from collections import deque

from dotenv import load_dotenv
import websockets  # pip install websockets

from telegram_bot import TelegramNotifier

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

    def ping_polling(self):
        self.last_polling = time.time()

    def ping_ws(self):
        self.last_ws = time.time()

    def ping_heartbeat(self):
        self.last_heartbeat = time.time()

    async def monitor(self):
        while True:
            now = time.time()

            if now - self.last_polling > self.polling_timeout:
                print("[AUTO-RESTART] Telegram polling завис. Перезапуск процесса...")
                self.restart_process()

            if now - self.last_ws > self.ws_timeout:
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
TELEGRAM_CHAT_ID = int(os.getenv("TELEGRAM_CHAT_ID"))

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

# =====================================================
# ENGINES (V31 ELITE)
# =====================================================

from elite_structure_engine import EliteStructureEngine
from elite_regime_engine import EliteRegimeEngine
from elite_htf_sync import EliteHTFSync
from elite_trend_engine import EliteTrendEngine
from elite_reversal_engine import EliteReversalEngine
from elite_signal_router import EliteSignalRouter
from risk_engine_v31 import RiskEngineV31
from elite_exit_engine import EliteExitEngine

# =====================================================
# BYBIT DOWNLOADER
# =====================================================

class BybitDownloader:
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

    def __init__(self, max_retries=5, retry_delay=1.0):
        self.max_retries = max_retries
        self.retry_delay = retry_delay

    def download(self, symbol: str, interval: str, total_needed: int):
        print(f"\n[Bybit] Downloading {symbol} {interval} ...")

        if interval not in self.INTERVAL_MAP:
            print(f"[Bybit] Unsupported interval: {interval}")
            return []

        bybit_tf = self.INTERVAL_MAP[interval]
        all_data = []
        end_time = None

        while len(all_data) < total_needed:
            params = {
                "category": "linear",
                "symbol": symbol.upper(),
                "interval": bybit_tf,
                "limit": 1000,
            }

            if end_time:
                params["end"] = end_time

            for attempt in range(self.max_retries):
                try:
                    r = requests.get(self.BASE_URL, params=params, timeout=10)
                    r.raise_for_status()
                    data = r.json()
                    break
                except Exception as e:
                    print(f"[Bybit] Error on {symbol} {interval}, attempt {attempt+1}/{self.max_retries}: {e}")
                    time.sleep(self.retry_delay)
            else:
                print(f"[Bybit] FAILED to download {symbol} {interval}. Returning what we have.")
                return all_data

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
            time.sleep(0.1)

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

    atr_pct = get_atr_percentile(regime)
    if atr_pct < 0.10:
        return None
    if atr_pct > 0.95:
        return None

    htf_bias = htf.get("bias")
    direction = signal.get("signal")

    if htf_bias == "strong_bull" and direction == "short":
        return None
    if htf_bias == "strong_bear" and direction == "long":
        return None

    align = htf.get("alignment_score", 0.0)
    if align < -0.20:
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
    reversal_signal = engines["reversal"].evaluate(structure, regime, htf, symbol=symbol)
    signal = engines["router"].route(trend_signal, reversal_signal, regime, htf, symbol=symbol)

    signal = smart_filter_v4(signal, regime, htf)
    if not signal:
        return None

    if symbol == "BTCUSDT":
        atr_pct = get_atr_percentile(regime)

        if atr_pct < 0.25:
            return None

        if signal.get("quality", 0.0) < 0.60:
            return None

        if htf.get("alignment_score", 0.0) < 0.55:
            return None
    else:
        q = signal.get("quality", 0.0)
        if q < MIN_SIGNAL_QUALITY:
            return None
        alignment = htf.get("alignment_score", 0.0)
        if alignment < MIN_HTF_ALIGNMENT:
            return None

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
# SIGNAL DISPATCHER → TELEGRAM
# =====================================================

async def signal_dispatcher(signal_queue: asyncio.Queue, notifier: TelegramNotifier):
    print("[DISPATCHER] started")
    while True:
        signal = await signal_queue.get()
        try:
            await notifier.send_signal(
                symbol=signal["symbol"],
                direction=signal["direction"],
                signal_type=signal["type"],
                price=signal["price"],
                quality=signal["quality"],
                htf_regime=signal["htf_regime"],
                candles_15m=signal["candles_15m"],
            )
        except Exception as e:
            print(f"[Notifier] Error sending signal for {signal.get('symbol')}: {e}")
        finally:
            signal_queue.task_done()

# =====================================================
# WEBSOCKET LOOP
# =====================================================

async def ws_trading_loop_once(symbols, engines, notifier: TelegramNotifier, signal_queue: asyncio.Queue):
    downloader = BybitDownloader()
    broker = BybitBroker(BYBIT_API_KEY, BYBIT_API_SECRET)

    history_5m: Dict[str, deque] = {s: deque(maxlen=1000) for s in symbols}
    htf_15m: Dict[str, deque] = {s: deque(maxlen=1000) for s in symbols}
    htf_1h: Dict[str, deque] = {s: deque(maxlen=1000) for s in symbols}
    htf_4h: Dict[str, deque] = {s: deque(maxlen=1000) for s in symbols}

    positions: Dict[str, Dict] = {}
    equity = INITIAL_EQUITY
    day_start = datetime.date.today()
    day_start_equity = equity

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

    notifier.positions_ref = positions
    notifier.closeall_callback = close_position_from_telegram

    last_trade_close_bar: Dict[str, int] = {s: -999 for s in symbols}

    # ускоренная загрузка истории
    for s in symbols:
        history_5m[s].extend(
            await asyncio.to_thread(downloader.download, s, "5m", 800)
        )
        htf_15m[s].extend(
            await asyncio.to_thread(downloader.download, s, "15m", 400)
        )
        htf_1h[s].extend(
            await asyncio.to_thread(downloader.download, s, "1h", 400)
        )
        htf_4h[s].extend(
            await asyncio.to_thread(downloader.download, s, "4h", 400)
        )

    print("🔥 WS loop started (5m + HTF WS, V31 ELITE, /scan + /trade).")
    last_heartbeat = time.time()
    auto.ping_ws()
    auto.ping_heartbeat()

    broker_error_count = 0
    broker_error_limit = 5
    tech_kill_triggered = False

    topics = []
    for s in symbols:
        topics.append(f"kline.5.{s}")
        topics.append(f"kline.15.{s}")
        topics.append(f"kline.60.{s}")
        topics.append(f"kline.240.{s}")

    async with websockets.connect(BYBIT_WS_URL) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": topics}))
        print("[WS] Subscribed:", topics)

        while True:
            if time.time() - last_heartbeat > 30:
                print(f"[WS] alive {datetime.datetime.now().strftime('%H:%M:%S')}")
                last_heartbeat = time.time()
                auto.ping_ws()
                auto.ping_heartbeat()

            today = datetime.date.today()
            if today != day_start:
                day_start = today
                day_start_equity = equity

            day_dd = (day_start_equity - equity) / day_start_equity if day_start_equity > 0 else 0
            if day_dd >= DAILY_LOSS_LIMIT_PCT and notifier.trading_enabled:
                print(f"⛔ Daily loss limit reached: {day_dd*100:.2f}%. Trading paused (signals continue).")
                notifier.trading_enabled = False

            try:
                msg = await ws.recv()
                auto.ping_ws()
                auto.ping_heartbeat()
            except Exception as e:
                print(f"[WS] Disconnected inside loop: {e}")
                raise

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

                if tf == "5":
                    symbol_hist_5m.append(candle)
                elif tf == "15":
                    if confirm:
                        htf_15m[symbol].append(candle)
                    continue
                elif tf == "60":
                    if confirm:
                        htf_1h[symbol].append(candle)
                    continue
                elif tf == "240":
                    if confirm:
                        htf_4h[symbol].append(candle)
                    continue
                else:
                    continue

                if tf != "5":
                    continue

                len_5m = len(symbol_hist_5m)
                if len_5m < 600:
                    continue

                if symbol in positions and confirm:
                    pos = positions[symbol]
                    last_price = c

                    last_600 = list(symbol_hist_5m)[-600:]
                    last_200 = last_600[-200:]

                    try:
                        regime_live = engines["regime"].detect(last_600)
                        atr_live = engines["regime"]._atr(last_200, 14) or 0.0
                    except Exception as e:
                        print(f"[EXIT] Regime/ATR error on {symbol}: {e}")
                        regime_live = None
                        atr_live = 0.0

                    if regime_live:
                        try:
                            updated = engines["exit"].manage_position(
                                pos,
                                last_price,
                                atr_live,
                                regime_live["regime"],
                                len_5m,
                            )
                        except Exception as e:
                            print(f"[EXIT] manage_position error on {symbol}: {e}")
                            updated = pos

                        positions[symbol] = updated

                        if updated.get("status") == "CLOSED":
                            exit_price = updated["exit_price"]
                            size = updated["size"]
                            direction = updated["direction"]

                            side = "sell" if direction == "long" else "buy"
                            try:
                                _, exit_fee = await broker.market_order(
                                    symbol,
                                    side,
                                    size,
                                    exit_price,
                                    is_entry=False,
                                )
                            except Exception as e:
                                print(f"[Broker] Exit order failed for {symbol}: {e}")
                                exit_fee = 0.0

                                broker_error_count += 1
                                if broker_error_count >= broker_error_limit and not tech_kill_triggered:
                                    tech_kill_triggered = True
                                    notifier.trading_enabled = False
                                    print("⛔ TECH KILL SWITCH: слишком много ошибок брокера при выходе. Торговля остановлена.")

                            if direction == "long":
                                gross = (exit_price - updated["entry"]) * size
                            else:
                                gross = (updated["entry"] - exit_price) * size

                            fees = updated["entry_fee"] + exit_fee
                            pnl = gross - fees
                            equity += pnl

                            try:
                                engines["risk"].close_position(
                                    risk_pct=updated["risk_pct"],
                                    entry=updated["entry"],
                                    stop=updated["sl_initial"],
                                    exit_price=exit_price,
                                    direction=direction,
                                )
                            except Exception as e:
                                print(f"[Risk] close_position error on {symbol}: {e}")

                            print(
                                f"CLOSE {symbol} | dir={direction} "
                                f"entry={updated['entry']:.4f} exit={exit_price:.4f} "
                                f"gross={gross:.2f} fees={fees:.2f} PnL={pnl:.2f} "
                                f"Equity={equity:.2f} reason={updated.get('reason','')}"
                            )

                            log_trade({
                                "symbol": symbol,
                                "direction": direction,
                                "entry": updated["entry"],
                                "exit": exit_price,
                                "size": size,
                                "gross": gross,
                                "fees": fees,
                                "net": pnl,
                                "equity_after": equity,
                                "reason": updated.get("reason", ""),
                                "timestamp": int(time.time() * 1000),
                            })

                            last_trade_close_bar[symbol] = len_5m
                            positions.pop(symbol, None)

                if not confirm:
                    continue

                try:
                    signal = analyze_symbol_core(
                        symbol,
                        list(symbol_hist_5m),
                        list(htf_15m[symbol]),
                        list(htf_1h[symbol]),
                        list(htf_4h[symbol]),
                        engines,
                    )
                except Exception as e:
                    print(f"[ANALYZE] Error on {symbol}: {e}")
                    continue

                if signal:
                    print(
                        f"[SIGNAL] {signal['symbol']} "
                        f"{signal['direction']} "
                        f"type={signal['type']} "
                        f"q={int(signal['quality']*100)} "
                        f"price={signal['price']:.4f} "
                        f"htf={signal['htf_regime']} "
                        f"mode={'TRADE' if notifier.trading_enabled else 'SCAN'}"
                    )

                    try:
                        await signal_queue.put({
                            "symbol": signal["symbol"],
                            "direction": signal["direction"],
                            "type": signal["type"],
                            "price": signal["price"],
                            "quality": int(signal["quality"] * 100),
                            "htf_regime": signal["htf_regime"],
                            "candles_15m": list(htf_15m[symbol])[-200:],
                        })
                    except Exception as e:
                        print(f"[Notifier] Error queueing signal for {symbol}: {e}")

                    if notifier.trading_enabled and symbol not in positions:
                        if symbol == "BTCUSDT":
                            min_gap = 5
                        else:
                            min_gap = MIN_BAR_GAP

                        too_soon = (len_5m - last_trade_close_bar[symbol]) < min_gap
                        if too_soon:
                            continue

                        try:
                            size, risk_pct = engines["risk"].allocate(
                                equity=equity,
                                entry_price=signal["price"],
                                stop_price=signal["sl"],
                                regime=signal["regime"],
                                atr=signal["atr"],
                            )
                        except Exception as e:
                            print(f"[Risk] allocate error on {symbol}: {e}")
                            continue

                        if size > 0:
                            try:
                                entry_price, entry_fee = await broker.market_order(
                                    symbol,
                                    signal["direction"],
                                    size,
                                    signal["price"],
                                    is_entry=True,
                                )
                            except Exception as e:
                                print(f"[Broker] Entry order failed for {symbol}: {e}")

                                broker_error_count += 1
                                if broker_error_count >= broker_error_limit and not tech_kill_triggered:
                                    tech_kill_triggered = True
                                    notifier.trading_enabled = False
                                    print("⛔ TECH KILL SWITCH: слишком много ошибок брокера при входе. Торговля остановлена.")

                                continue

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

async def ws_trading_loop(symbols, engines, notifier: TelegramNotifier, signal_queue: asyncio.Queue):
    while True:
        try:
            await ws_trading_loop_once(symbols, engines, notifier, signal_queue)
        except Exception as e:
            print(f"[WS] crashed: {e}")
            await asyncio.sleep(3)
            print("[WS] Restarting WebSocket...")
            continue

# =====================================================
# TG HEARTBEAT
# =====================================================

async def tg_heartbeat():
    while True:
        print(f"[TG] alive {datetime.datetime.now().strftime('%H:%M:%S')}")
        auto.ping_heartbeat()
        await asyncio.sleep(30)

# =====================================================
# TG POLLING LIVENESS (вариант A)
# =====================================================

async def tg_polling_liveness():
    while True:
        auto.ping_polling()
        await asyncio.sleep(5)

# =====================================================
# ENTRY POINT
# =====================================================

async def main():
    engines = {
        "structure": EliteStructureEngine(),
        "regime": EliteRegimeEngine(),
        "htf": EliteHTFSync(),
        "trend": EliteTrendEngine(),
        "reversal": EliteReversalEngine(),
        "router": EliteSignalRouter(),
        "risk": RiskEngineV31(),
        "exit": EliteExitEngine(),
    }

    notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
    signal_queue = asyncio.Queue()

    asyncio.create_task(auto.monitor())
    asyncio.create_task(tg_heartbeat())
    asyncio.create_task(tg_polling_liveness())
    asyncio.create_task(signal_dispatcher(signal_queue, notifier))
    asyncio.create_task(ws_trading_loop(SYMBOLS, engines, notifier, signal_queue))

    await notifier.run()

if __name__ == "__main__":
    asyncio.run(main())
