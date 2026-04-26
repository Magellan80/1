import io
import os
import time
import asyncio
import datetime
from typing import List, Dict, Any, Optional

import aiohttp
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from fastapi import FastAPI, Depends, HTTPException, status
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBasic, HTTPBasicCredentials
import secrets

from dotenv import load_dotenv

import sqlite_storage as SqlStore

load_dotenv()

WEB_PANEL_PORT = int(os.getenv("WEB_PANEL_PORT", "8080"))
WEB_USER = os.getenv("WEB_USER", "admin")
WEB_PASS = os.getenv("WEB_PASS", "password")

app = FastAPI(title="V31 Web Panel")

security = HTTPBasic()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
#   BASIC AUTH
# ============================================================

def require_basic_auth(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, WEB_USER)
    correct_password = secrets.compare_digest(credentials.password, WEB_PASS)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )


# ============================================================
#   WEB NOTIFIER
# ============================================================

class WebNotifier:
    def __init__(self):
        self.signals_enabled: bool = True
        self.trading_enabled: bool = False

        self.telegram_enabled: bool = True

        self.last_direction: Dict[str, str] = {}
        self.last_quality: Dict[str, int] = {}

        self._funding_cache: Dict[str, tuple[float, float]] = {}
        self._funding_ttl: int = 60

        self._session: Optional[aiohttp.ClientSession] = None

        self.positions_ref: Optional[Dict[str, Dict[str, Any]]] = None
        self.closeall_callback = None

        # живые сигналы в памяти (для Live, не обязательно для истории)
        self.signals: List[Dict[str, Any]] = []
        self.max_signals: int = 200

    # ============================
    # FUNDING
    # ============================

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
            print(f"[WEB] Funding fetch error for {symbol}: {e}")
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

    # ============================
    # CHART
    # ============================

    def _make_chart_sync(self, candles):
        if not candles or len(candles) < 20:
            return None

        try:
            times = [datetime.datetime.fromtimestamp(c["timestamp"] / 1000) for c in candles]
            opens = [c["open"] for c in candles]
            highs = [c["high"] for c in candles]
            lows = [c["low"] for c in candles]
            closes = [c["close"] for c in candles]

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

            fig, ax = plt.subplots(2, 1, figsize=(10, 6),
                                   gridspec_kw={"height_ratios": [3, 1]})

            for i in range(len(candles)):
                color = "green" if closes[i] >= opens[i] else "red"
                ax[0].plot([times[i], times[i]], [lows[i], highs[i]], color=color)
                ax[0].plot([times[i], times[i]], [opens[i], closes[i]], color=color, linewidth=4)

            ax[0].set_title("15m Chart")
            ax[0].grid(True)
            ax[0].yaxis.tick_right()
            ax[0].yaxis.set_label_position("right")

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
            print(f"[WEB] Chart generation error: {e}")
            return None

    async def make_chart(self, candles):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._make_chart_sync, candles)

    # ============================
    # SEND SIGNAL
    # ============================

    async def send_signal(
        self,
        symbol: str,
        direction: str,
        signal_type: str,
        price: float,
        quality: int,
        htf_regime: str,
        candles_15m,
    ):
        if not self.signals_enabled:
            print(f"[WEB] Signal skipped (signals disabled): {symbol}")
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
                    print(f"[WEB] Signal skipped (quality not improved): {symbol}")
                    return
                header = f"{color}{symbol} {signal_type} {new_dir} (↑ качество)"

        self.last_direction[symbol] = direction
        self.last_quality[symbol] = quality

        chart_bytes = None
        if candles_15m and len(candles_15m) >= 20:
            chart_bytes = await self.make_chart(candles_15m)

        if candles_15m and "timestamp" in candles_15m[-1]:
            ts_dt = datetime.datetime.fromtimestamp(candles_15m[-1]["timestamp"] / 1000)
        else:
            ts_dt = datetime.datetime.utcnow()

        signal = {
            "symbol": symbol,
            "direction": direction,
            "signal_type": signal_type,
            "price": price,
            "quality": quality,
            "htf_regime": htf_regime,
            "funding": funding,
            "funding_color": f_color,
            "header": header,
            "has_chart": chart_bytes is not None,
            "chart_bytes": chart_bytes,
            "ts": ts_dt.isoformat(),
        }

        # сохраняем в SQLite
        try:
            SqlStore.cleanup_old(days=7)
        except Exception as e:
            print(f"[WEB] cleanup_old error: {e}")

        signal_id = SqlStore.save_signal(signal)
        signal["id"] = signal_id

        # живой список (для Live, если захочешь)
        self.signals.append(signal)
        if len(self.signals) > self.max_signals:
            self.signals = self.signals[-self.max_signals:]

    # ============================
    # CLOSE ALL
    # ============================

    async def close_all(self, force: bool = False):
        if not self.positions_ref or not self.closeall_callback:
            return

        to_close = []
        for sym, pos in list(self.positions_ref.items()):
            if force:
                to_close.append((sym, pos))
            else:
                if pos.get("status") == "OPEN":
                    to_close.append((sym, pos))

        print(f"[WEB] close_all: {len(to_close)} positions, force={force}")
        for sym, pos in to_close:
            try:
                await self.closeall_callback(sym, pos)
            except Exception as e:
                print(f"[WEB] close_all error on {sym}: {e}")


# ============================================================
#   GLOBAL INSTANCE
# ============================================================

web_notifier = WebNotifier()


# ============================================================
#   HTML TEMPLATE
# ============================================================

def base_html(body: str) -> str:
    return f"""
<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8" />
  <title>V31 Web Panel</title>
  <style>
    body {{
      font-family: system-ui, sans-serif;
      background: #0b1120;
      color: #e5e7eb;
      margin: 0;
      padding: 16px;
    }}
    .card {{
      background: #020617;
      border-radius: 8px;
      padding: 16px;
      margin-bottom: 16px;
      border: 1px solid #1f2937;
    }}
    .btn {{
      display: inline-block;
      padding: 8px 12px;
      margin: 4px 4px 4px 0;
      border-radius: 6px;
      border: none;
      cursor: pointer;
      font-size: 14px;
      text-decoration: none;
      color: #e5e7eb;
      background: #1d4ed8;
    }}
    .btn.red {{ background: #b91c1c; }}
    .btn.green {{ background: #15803d; }}
    .btn.gray {{ background: #4b5563; }}
    .signal {{
      border-bottom: 1px solid #1f2937;
      padding: 8px 0;
    }}
    .mono {{
      font-family: monospace;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h2>V31 Web Panel</h2>
    <div>
      <a class="btn" href="/">Главная</a>
      <a class="btn" href="/signals">Сигналы</a>
      <a class="btn" href="/positions">Позиции</a>
      <a class="btn" href="/status">Статус</a>
    </div>
  </div>
  {body}
</body>
</html>
"""


# ============================================================
#   ROUTES
# ============================================================

@app.get("/", response_class=HTMLResponse)
async def index(_=Depends(require_basic_auth)):
    wn = web_notifier
    body = f"""
<div class="card">
  <h3>Управление</h3>
  <form method="post" action="/api/signals/toggle">
    <button class="btn {'green' if wn.signals_enabled else 'red'}" type="submit">
      Сигналы: {'ON' if wn.signals_enabled else 'OFF'}
    </button>
  </form>
  <form method="post" action="/api/tg/toggle">
    <button class="btn {'green' if wn.telegram_enabled else 'red'}" type="submit">
      Telegram: {'ON' if wn.telegram_enabled else 'OFF'}
    </button>
  </form>
  <form method="post" action="/api/trade/on">
    <button class="btn green" type="submit">Включить торговлю</button>
  </form>
  <form method="post" action="/api/trade/off">
    <button class="btn gray" type="submit">Выключить торговлю</button>
  </form>
  <form method="post" action="/api/closeall">
    <button class="btn red" type="submit">Закрыть все OPEN позиции</button>
  </form>
  <form method="post" action="/api/closeall_force">
    <button class="btn red" type="submit">Закрыть ВСЕ позиции (force)</button>
  </form>
</div>
"""
    return base_html(body)


@app.get("/status", response_class=HTMLResponse)
async def status_page(_=Depends(require_basic_auth)):
    wn = web_notifier
    pos_count = len(wn.positions_ref) if wn.positions_ref else 0
    body = f"""
<div class="card">
  <h3>Статус</h3>
  <p>Сигналы: {'🟢 ON' if wn.signals_enabled else '🔴 OFF'}</p>
  <p>Режим: {'🤖 Торговля' if wn.trading_enabled else '🔍 Скринер'}</p>
  <p>Telegram: {'🟢 ON' if wn.telegram_enabled else '🔴 OFF'}</p>
  <p>Открытых позиций: {pos_count}</p>
</div>
"""
    return base_html(body)


@app.get("/positions", response_class=HTMLResponse)
async def positions_page(_=Depends(require_basic_auth)):
    wn = web_notifier
    rows = ""
    if wn.positions_ref:
        for sym, pos in wn.positions_ref.items():
            direction = pos.get("direction", "?")
            entry = pos.get("entry", 0.0)
            size = pos.get("size", 0.0)
            sl = pos.get("sl", 0.0)
            status = pos.get("status", "?")
            rows += f"""
<div class="signal mono">
  {sym} {direction} {size} @ {entry:.4f} SL={sl:.4f} status={status}
</div>
"""
    else:
        rows = "<p>Нет открытых позиций.</p>"

    body = f"""
<div class="card">
  <h3>Позиции</h3>
  {rows}
</div>
"""
    return base_html(body)


# ============================================================
#   СИГНАЛЫ (ИСТОРИЯ ИЗ SQLITE)
# ============================================================

@app.get("/signals", response_class=HTMLResponse)
async def signals_page(_=Depends(require_basic_auth)):
    saved = SqlStore.load_signals(limit=200)

    items = ""
    for s in saved:
        img_html = ""
        if s.get("has_chart"):
            img_html = f"""
  <div>
    <img src="/chart/{s['id']}" style="max-width:320px;border:1px solid #1f2937;border-radius:6px;margin-top:6px;">
  </div>
"""
        items += f"""
<div class="signal">
  <div class="mono">{s['symbol']} {s['direction']} q={s['quality']} type={s['signal_type']}</div>
  <div class="mono">Цена: {s['price']} | HTF: {s['htf_regime']} | Funding: {s['funding']:.4f}%</div>
  <div class="mono" style="font-size:12px;color:#9ca3af;">{s.get('ts','')}</div>
  {img_html}
  <form method="post" action="/api/signals/delete/{s['id']}">
    <button class="btn red" type="submit">Удалить</button>
  </form>
</div>
"""

    if not items:
        items = "<p>Сигналов пока нет.</p>"

    body = f"""
<div class="card">
  <h3>Сигналы (история, SQLite)</h3>
  <form method="post" action="/api/signals/clear">
    <button class="btn red" type="submit">Очистить ВСЕ сигналы</button>
  </form>
  <br>
  {items}
</div>
"""
    return base_html(body)


# ============================================================
#   ОТДАЧА ГРАФИКА ИЗ SQLITE
# ============================================================

@app.get("/chart/{signal_id}")
async def chart(signal_id: int, _=Depends(require_basic_auth)):
    data = SqlStore.get_chart(signal_id)
    if not data:
        raise HTTPException(status_code=404, detail="Chart not found")
    return StreamingResponse(io.BytesIO(data), media_type="image/png")


# ============================================================
#   API: управление сигналами (SQLite)
# ============================================================

@app.post("/api/signals/delete/{signal_id}")
async def api_delete_signal(signal_id: int, _=Depends(require_basic_auth)):
    SqlStore.delete_signal(signal_id)
    return RedirectResponse(url="/signals", status_code=303)


@app.post("/api/signals/clear")
async def api_clear_signals(_=Depends(require_basic_auth)):
    SqlStore.clear_all()
    return RedirectResponse(url="/signals", status_code=303)


# ============================================================
#   API ACTIONS (остальные)
# ============================================================

@app.post("/api/trade/on")
async def api_trade_on(_=Depends(require_basic_auth)):
    web_notifier.trading_enabled = True
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/trade/off")
async def api_trade_off(_=Depends(require_basic_auth)):
    web_notifier.trading_enabled = False
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/signals/toggle")
async def api_signals_toggle(_=Depends(require_basic_auth)):
    web_notifier.signals_enabled = not web_notifier.signals_enabled
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/tg/toggle")
async def api_tg_toggle(_=Depends(require_basic_auth)):
    web_notifier.telegram_enabled = not web_notifier.telegram_enabled
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/closeall")
async def api_closeall(_=Depends(require_basic_auth)):
    await web_notifier.close_all(force=False)
    web_notifier.trading_enabled = False
    return RedirectResponse(url="/", status_code=303)


@app.post("/api/closeall_force")
async def api_closeall_force(_=Depends(require_basic_auth)):
    await web_notifier.close_all(force=True)
    web_notifier.trading_enabled = False
    return RedirectResponse(url="/", status_code=303)
