# v30_backtest_engine.py
# V33 Turbo Backtest Engine — ускоренная версия (2–3x), реализм сохранён
# Синхронизирован с v31_live_bot (TREND-ONLY, HTF bias, smart_filter_v4)

import sys
import os
import math
from typing import List, Dict, Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BOT_DIR = os.path.dirname(BASE_DIR)
sys.path.append(BOT_DIR)

from elite_structure_engine import EliteStructureEngine
from elite_regime_engine import EliteRegimeEngine
from elite_htf_sync import EliteHTFSync
from elite_trend_engine import EliteTrendEngine
from elite_signal_router import EliteSignalRouter
from risk_engine_v31 import RiskEngineV31

# ============================================================
#   РЕАЛИСТИЧНЫЕ ПАРАМЕТРЫ РЫНКА
# ============================================================

TAKER_FEE          = 0.00055
SLIPPAGE_ENTRY     = 0.0002
SLIPPAGE_EXIT      = 0.00015

FUNDING_INTERVAL_BARS = 96
DEFAULT_FUNDING_RATE  = 0.0001

MIN_SIGNAL_QUALITY  = 0.55
MIN_HTF_ALIGNMENT   = -0.05
MIN_BAR_GAP         = 2

BARS_PER_YEAR = 252 * 288


class V30BacktestEngine:

    def __init__(
        self,
        taker_fee: float = TAKER_FEE,
        slippage_entry: float = SLIPPAGE_ENTRY,
        slippage_exit: float = SLIPPAGE_EXIT,
        funding_rate: float = DEFAULT_FUNDING_RATE,
        funding_interval_bars: int = FUNDING_INTERVAL_BARS,
        min_signal_quality: float = MIN_SIGNAL_QUALITY,
        min_htf_alignment: float = MIN_HTF_ALIGNMENT,
        min_bar_gap: int = MIN_BAR_GAP,
        symbol: str = "BTCUSDT",
    ):
        self.structure_window = 400
        self.regime_window    = 600
        self.atr_window       = 200

        self.taker_fee        = taker_fee
        self.slippage_entry   = slippage_entry
        self.slippage_exit    = slippage_exit

        self.funding_rate          = funding_rate
        self.funding_interval_bars = funding_interval_bars

        self.min_signal_quality = min_signal_quality
        self.min_htf_alignment  = min_htf_alignment
        self.min_bar_gap        = min_bar_gap

        self.symbol = symbol

    # ============================================================
    #   ГЛАВНЫЙ ЦИКЛ
    # ============================================================

    def run(
        self,
        candles_5m: List[Dict],
        structure_engine: EliteStructureEngine,
        regime_engine: EliteRegimeEngine,
        htf_sync: EliteHTFSync,
        trend_engine: EliteTrendEngine,
        router: EliteSignalRouter,
        exit_engine,
        htf_15m,
        htf_1h,
        htf_4h,
        risk_engine: RiskEngineV31,
        initial_balance: float = 10000,
    ) -> Dict:

        equity = initial_balance
        peak   = equity
        max_dd = 0.0

        trades    = []
        position  = None

        equity_curve = [equity]
        trade_log    = []
        regime_log   = []
        signal_log   = []
        htf_log      = []
        atr_log      = []

        last_trade_close_bar = -999

        total_fees_paid    = 0.0
        total_funding_paid = 0.0

        total   = len(candles_5m)
        history = list(candles_5m[:600])

        atr_cache: Dict[int, float] = {}

        print(f"Total 5m candles: {total}")
        print(
            f"Costs → Taker fee: {self.taker_fee*100:.4f}% | "
            f"Slippage entry: {self.slippage_entry*100:.3f}% / "
            f"exit: {self.slippage_exit*100:.3f}%"
        )
        print(
            f"Funding: {self.funding_rate*100:.4f}% per 8h | "
            f"Filters: quality≥{self.min_signal_quality}, HTF≥{self.min_htf_alignment}, min_gap={self.min_bar_gap} bars"
        )
        print("Starting Turbo Institutional loop...\n")

        struct_win = self.structure_window
        regime_win = self.regime_window
        atr_win    = self.atr_window

        for i in range(250, total):

            current_bar = candles_5m[i]
            history.append(current_bar)

            if i % 1500 == 0 and i > 250:
                progress = round(i / total * 100, 1)
                print(
                    f"Progress: {progress}% | Trades: {len(trades)} | "
                    f"Equity: {round(equity, 2)} | "
                    f"Fees: ${round(total_fees_paid, 2)} | "
                    f"Funding: ${round(total_funding_paid, 2)}"
                )

            close_price = float(current_bar["close"])
            open_price  = float(current_bar["open"])
            ts          = current_bar["timestamp"]

            # --------------------------------------------------
            # АНАЛИЗ РЫНКА
            # --------------------------------------------------
            struct_slice = history[-struct_win:]
            regime_slice = history[-regime_win:]
            atr_slice    = history[-atr_win:]

            structure = structure_engine.analyze(struct_slice)

            regime = None
            try:
                regime = regime_engine.detect(regime_slice, structure)
            except TypeError:
                regime = regime_engine.detect(regime_slice)

            htf = htf_sync.analyze(ts, htf_15m, htf_1h, htf_4h)

            if not structure or not regime or not htf:
                if position:
                    unrealized = self._unrealized_pnl(position, close_price)
                    equity_now = equity + unrealized
                    peak = max(peak, equity_now)
                    dd = (peak - equity_now) / peak if peak > 0 else 0.0
                    max_dd = max(max_dd, dd)
                equity_curve.append(equity)
                continue

            regime_name = regime.get("regime", "UNKNOWN")
            regime_log.append(regime_name)
            htf_log.append({
                "alignment":       htf.get("alignment_score", 0.0),
                "bias":            htf.get("bias", "neutral"),
                "signed_strength": htf.get("signed_trend_strength", 0.0),
                "htf_regime":      htf.get("htf_regime", "UNKNOWN"),
            })

            if i not in atr_cache:
                atr_val = regime_engine._atr(atr_slice, 14)
                atr_cache[i] = atr_val if atr_val else 0.0
            atr_val = atr_cache[i]
            atr_log.append(atr_val)

            # --------------------------------------------------
            # DD по unrealized equity
            # --------------------------------------------------
            if position:
                unrealized = self._unrealized_pnl(position, close_price)
                equity_now = equity + unrealized
            else:
                equity_now = equity

            peak   = max(peak, equity_now)
            dd_now = (peak - equity_now) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd_now)

            # --------------------------------------------------
            # FUNDING RATE
            # --------------------------------------------------
            if position and (i % self.funding_interval_bars == 0):
                funding_delta = self._apply_funding(position)
                equity              += funding_delta
                total_funding_paid  -= funding_delta
                position["funding_paid"] = position.get("funding_paid", 0.0) + max(-funding_delta, 0.0)

            # --------------------------------------------------
            # УПРАВЛЕНИЕ ПОЗИЦИЕЙ
            # --------------------------------------------------
            if position:
                atr = atr_val
                position = exit_engine.manage_position(
                    position, close_price, atr, regime_name, i
                )

                if position and position.get("status") == "OPEN" and "pyramid_signal" in position:
                    self._apply_pyramid_add(position)
                    del position["pyramid_signal"]

                if position and position["status"] == "CLOSED":
                    raw_exit   = position["exit_price"]
                    exit_price = self._apply_slippage_exit(raw_exit, position["direction"])
                    position["exit_price"] = exit_price

                    pnl_gross    = self._calculate_pnl(position)
                    entry_fee    = position["size"] * position["entry"]   * self.taker_fee
                    exit_fee     = position["size"] * exit_price          * self.taker_fee
                    total_fees   = entry_fee + exit_fee
                    funding_paid = position.get("funding_paid", 0.0)
                    pnl_net      = pnl_gross - total_fees - funding_paid

                    equity           += pnl_net
                    total_fees_paid  += total_fees

                    risk_engine.close_position(
                        risk_pct   = position["risk_pct"],
                        entry      = position["entry"],
                        stop       = position["sl_initial"],
                        exit_price = exit_price,
                        direction  = position["direction"],
                    )

                    last_trade_close_bar = i
                    trades.append(pnl_net)

                    reason = position.get("reason", "unknown")
                    print(
                        f"CLOSE [{reason}] | "
                        f"Gross: {round(pnl_gross,2)} | "
                        f"Costs: {round(total_fees+funding_paid,2)} | "
                        f"Net: {round(pnl_net,2)} | "
                        f"Eq: {round(equity,2)} | "
                        f"DD: {round(max_dd*100,2)}%"
                    )

                    trade_log.append({
                        "bar_open":    position["open_bar"],
                        "bar_close":   i,
                        "direction":   position["direction"],
                        "entry":       round(position["entry"], 6),
                        "exit":        round(exit_price, 6),
                        "sl_initial":  round(position["sl_initial"], 6),
                        "sl_final":    round(position["sl"], 6),
                        "size":        round(position["size"], 4),
                        "pnl_gross":   round(pnl_gross, 4),
                        "pnl_net":     round(pnl_net, 4),
                        "fees":        round(total_fees, 4),
                        "funding":     round(funding_paid, 4),
                        "risk_pct":    round(position["risk_pct"] * 100, 3),
                        "regime":      regime_name,
                        "htf_regime":  htf.get("htf_regime", "UNKNOWN"),
                        "equity_after":round(equity, 2),
                    })

                    try:
                        from signal_logger import log_trade_result
                        log_trade_result({
                            "engine": position.get("engine", "unknown"),
                            "direction": position["direction"],
                            "entry": position["entry"],
                            "exit": exit_price,
                            "sl_initial": position["sl_initial"],
                            "pnl_net": pnl_net,
                            "pnl_gross": pnl_gross,
                            "fees": total_fees,
                            "funding": funding_paid,
                            "bars_in_trade": i - position["open_bar"],
                            "exit_reason": reason,
                            "regime": regime_name,
                            "htf_regime": htf.get("htf_regime", "UNKNOWN"),
                            "symbol": self.symbol
                        })
                    except Exception as e:
                        print("Backtest logging error:", e)

                    position = None

            # --------------------------------------------------
            # СИГНАЛ
            # --------------------------------------------------
            trend_signal    = trend_engine.evaluate(structure, regime, htf, symbol=self.symbol)
            reversal_signal = None
            signal = router.route(trend_signal, reversal_signal, regime, htf, symbol=self.symbol)

            signal = self._smart_filter_v4(signal, regime, htf)

            signal_log.append(
                {
                    "bar":        i,
                    "type":       signal["type"],
                    "direction":  signal["signal"],
                    "quality":    signal["quality"],
                    "regime":     regime_name,
                    "htf_regime": htf.get("htf_regime", "UNKNOWN"),
                }
                if signal else None
            )

            # ==========================================================
            # BTC MODE — усиленные фильтры входа (как в v31_live_bot)
            # ==========================================================
            if self.symbol == "BTCUSDT":

                atr_pct = self._get_atr_percentile(regime)

                if signal and atr_pct < 0.25:
                    signal = None

                if signal and signal.get("quality", 0.0) < 0.60:
                    signal = None

                if signal and htf.get("alignment_score", 0.0) < 0.55:
                    signal = None

                self.min_bar_gap = 5

            # --------------------------------------------------
            # Умный фильтр качества и HTF (altcoins)
            # --------------------------------------------------
            if signal and self.symbol != "BTCUSDT":
                q = signal.get("quality", 0.0)
                if q < self.min_signal_quality:
                    signal = None
                else:
                    alignment = htf.get("alignment_score", 0.0)
                    if alignment < self.min_htf_alignment:
                        signal = None

            # --------------------------------------------------
            # ОТКРЫТИЕ ПОЗИЦИИ
            # --------------------------------------------------
            too_soon = ((i - last_trade_close_bar) < self.min_bar_gap)

            if not position and signal and not too_soon:

                entry_raw   = open_price
                entry_price = self._apply_slippage_entry(entry_raw, signal["signal"])

                atr = atr_val
                if not atr or atr <= 0:
                    equity_curve.append(equity)
                    continue

                sl = self._initial_sl(signal["signal"], entry_price, atr)

                size, risk_pct = risk_engine.allocate(
                    equity      = equity,
                    entry_price = entry_price,
                    stop_price  = sl,
                    regime      = regime_name,
                    atr         = atr,
                )

                if size == 0:
                    equity_curve.append(equity)
                    continue

                position = {
                    "direction":     signal["signal"],
                    "entry":         entry_price,
                    "sl":            sl,
                    "sl_initial":    sl,
                    "size":          size,
                    "risk_pct":      risk_pct,
                    "status":        "OPEN",
                    "partial_taken": False,
                    "early_be_done": False,
                    "second_partial": False,
                    "pyramid_count": 0,
                    "open_bar":      i,
                    "funding_paid":  0.0,
                }

                position["engine"] = signal["type"]
                position["trend_signal"] = trend_signal["signal"] if trend_signal else None
                position["reversal_signal"] = reversal_signal["signal"] if reversal_signal else None

                print(
                    f"OPEN {signal['signal'].upper()} | "
                    f"Price: {round(entry_price,4)} | "
                    f"SL: {round(sl,4)} | "
                    f"Risk: {round(risk_pct*100,2)}% | "
                    f"Size: {round(size,2)}"
                )

            equity_curve.append(equity)

        # --------------------------------------------------
        # Принудительное закрытие последней позиции
        # --------------------------------------------------
        if position:
            last_price = float(candles_5m[-1]["close"])
            exit_price = self._apply_slippage_exit(last_price, position["direction"])
            position["exit_price"] = exit_price
            pnl_gross  = self._calculate_pnl(position)
            entry_fee  = position["size"] * position["entry"] * self.taker_fee
            exit_fee   = position["size"] * exit_price        * self.taker_fee
            total_fees = entry_fee + exit_fee
            pnl_net    = pnl_gross - total_fees - position.get("funding_paid", 0.0)
            equity    += pnl_net
            trades.append(pnl_net)

            try:
                from signal_logger import log_trade_result
                log_trade_result({
                    "engine": position.get("engine", "unknown"),
                    "direction": position["direction"],
                    "entry": position["entry"],
                    "exit": exit_price,
                    "sl_initial": position["sl_initial"],
                    "pnl_net": pnl_net,
                    "pnl_gross": pnl_gross,
                    "fees": total_fees,
                    "funding": position.get("funding_paid", 0.0),
                    "bars_in_trade": len(candles_5m) - position["open_bar"],
                    "exit_reason": "forced_close",
                    "regime": regime_name,
                    "htf_regime": htf.get("htf_regime", "UNKNOWN"),
                    "symbol": self.symbol
                })
            except Exception as e:
                print("Backtest logging error:", e)

            print(f"[END] Force-close | Net PnL: {round(pnl_net, 2)} | Final Equity: {round(equity, 2)}")

        print(f"\nBacktest Finished.")
        print(f"Total fees paid:    ${round(total_fees_paid, 2)}")
        print(f"Total funding paid: ${round(total_funding_paid, 2)}\n")

        stats = self._stats(trades, equity, initial_balance, max_dd, equity_curve)

        return {
            "stats":        stats,
            "equity_curve": equity_curve,
            "trades":       trade_log,
            "regimes":      regime_log,
            "signals":      signal_log,
            "htf":          htf_log,
            "atr":          atr_log,
            "cost_summary": {
                "total_fees_paid":    round(total_fees_paid, 2),
                "total_funding_paid": round(total_funding_paid, 2),
                "total_costs":        round(total_fees_paid + total_funding_paid, 2),
            },
        }

    # ============================================================
    #   ПИРАМИДИНГ
    # ============================================================

    def _apply_pyramid_add(self, position: Dict) -> None:
        signal = position.get("pyramid_signal")
        if not signal:
            return

        add_size  = signal["add_size"]
        add_price = signal["add_price"]

        old_size  = position["size"]
        old_entry = position["entry"]

        new_size  = old_size + add_size
        if new_size <= 0:
            return

        new_entry = (old_entry * old_size + add_price * add_size) / new_size

        position["size"]  = new_size
        position["entry"] = new_entry

    # ============================================================
    #   SLIPPAGE
    # ============================================================

    def _apply_slippage_entry(self, price: float, direction: str) -> float:
        if direction == "long":
            return price * (1.0 + self.slippage_entry)
        return price * (1.0 - self.slippage_entry)

    def _apply_slippage_exit(self, price: float, direction: str) -> float:
        if direction == "long":
            return price * (1.0 - self.slippage_exit)
        return price * (1.0 + self.slippage_exit)

    # ============================================================
    #   FUNDING
    # ============================================================

    def _apply_funding(self, position: Dict) -> float:
        notional = position["size"] * position["entry"]
        rate     = self.funding_rate
        if position["direction"] == "long":
            return -notional * rate
        return notional * rate

    # ============================================================
    #   UNREALIZED PnL
    # ============================================================

    def _unrealized_pnl(self, position: Dict, current_price: float) -> float:
        entry = position["entry"]
        size  = position["size"]
        if position["direction"] == "long":
            return (current_price - entry) * size
        return (entry - current_price) * size

    # ============================================================
    #   ATR STOP
    # ============================================================

    def _initial_sl(self, direction: str, entry_price: float, atr: Optional[float]) -> float:
        if not atr or atr <= 0:
            return entry_price
        atr_mult = 1.5
        if direction == "long":
            return entry_price - atr * atr_mult
        else:
            return entry_price + atr * atr_mult

    # ============================================================
    #   PnL
    # ============================================================

    def _calculate_pnl(self, position: Dict) -> float:
        entry      = position["entry"]
        exit_price = position.get("exit_price", entry)
        size       = position["size"]
        if position["direction"] == "long":
            return (exit_price - entry) * size
        return (entry - exit_price) * size

    # ============================================================
    #   UNIVERSAL ATR PERCENTILE EXTRACTOR
    # ============================================================

    def _get_atr_percentile(self, regime: Dict) -> float:
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

    # ============================================================
    #   SMART FILTERS v4 — синхронизировано с v31_live_bot
    # ============================================================

    def _smart_filter_v4(self, signal: Optional[Dict], regime: Dict, htf: Dict):
        if not signal:
            return None

        atr_pct = self._get_atr_percentile(regime)
        if atr_pct < 0.10:
            return None
        if atr_pct > 0.95:
            return None

        direction = signal.get("signal")
        htf_bias  = htf.get("bias")

        if htf_bias == "bullish" and direction == "short":
            return None
        if htf_bias == "bearish" and direction == "long":
            return None

        align = htf.get("alignment_score", 0.0)
        signed_strength = htf.get("signed_trend_strength", 0.0)
        quality = signal.get("quality", 0.0)

        # мягкий анти-флет: слабый HTF-тренд + среднее качество → отбрасываем
        if abs(signed_strength) < 0.10 and quality < 0.60:
            return None

        # почти нулевое выравнивание и слабое качество → отбрасываем
        if abs(align) < 0.05 and quality < 0.58:
            return None

        return signal

    # ============================================================
    #   СТАТИСТИКА
    # ============================================================

    def _stats(
        self,
        trades: List[float],
        final_equity: float,
        initial_balance: float,
        max_dd: float,
        equity_curve: List[float],
    ) -> Dict:

        if not trades:
            return {"error": "no trades"}

        wins   = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]

        winrate  = len(wins) / len(trades)
        avg_win  = sum(wins)   / len(wins)   if wins   else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0

        expectancy = winrate * avg_win + (1.0 - winrate) * avg_loss
        pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else 0.0

        st  = sorted(trades)
        mid = len(st) // 2
        median_pnl = st[mid] if len(st) % 2 != 0 else (st[mid-1] + st[mid]) / 2

        max_consec = cur_consec = 0
        for t in trades:
            if t <= 0:
                cur_consec += 1
                max_consec  = max(max_consec, cur_consec)
            else:
                cur_consec = 0

        sharpe, sortino = self._sharpe_sortino(equity_curve)

        total_profit    = final_equity - initial_balance
        recovery_factor = (total_profit / (max_dd * initial_balance)) if max_dd > 0 else 0.0

        return {
            "trades":                  len(trades),
            "winrate":                 round(winrate, 4),
            "avg_win":                 round(avg_win, 2),
            "avg_loss":                round(avg_loss, 2),
            "median_pnl":              round(median_pnl, 2),
            "expectancy":              round(expectancy, 2),
            "profit_factor":           round(pf, 2),
            "final_balance":           round(final_equity, 2),
            "return_pct":              round((final_equity / initial_balance - 1) * 100, 2),
            "max_drawdown_pct":        round(max_dd * 100, 2),
            "max_consecutive_losses":  max_consec,
            "sharpe_ratio":            round(sharpe, 3),
            "sortino_ratio":           round(sortino, 3),
            "recovery_factor":         round(recovery_factor, 2),
        }

    def _sharpe_sortino(self, equity_curve: List[float]):
        if len(equity_curve) < 2:
            return 0.0, 0.0

        returns = []
        for i in range(1, len(equity_curve)):
            prev = equity_curve[i - 1]
            curr = equity_curve[i]
            if prev > 0:
                returns.append((curr - prev) / prev)

        if not returns:
            return 0.0, 0.0

        n      = len(returns)
        mean_r = sum(returns) / n
        var    = sum((r - mean_r) ** 2 for r in returns) / n
        std_r  = math.sqrt(var) if var > 0 else 0.0

        sharpe = (mean_r / std_r) * math.sqrt(BARS_PER_YEAR) if std_r > 0 else 0.0

        downside = [r for r in returns if r < 0]
        if downside:
            d_var  = sum(r ** 2 for r in downside) / n
            d_std  = math.sqrt(d_var)
            sortino = (mean_r / d_std) * math.sqrt(BARS_PER_YEAR) if d_std > 0 else 0.0
        else:
            sortino = 0.0

        return sharpe, sortino
