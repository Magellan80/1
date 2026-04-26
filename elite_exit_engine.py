# elite_exit_engine_pyramid.py
# V50.0 — Elite Exit Engine Pyramid
# Partial @ adaptive R + Pyramid in strong/institutional trend + ATR‑adaptive + institutional regimes support

from typing import Dict, Optional


class EliteExitEngine:

    def __init__(self):
        # Временные выходы (пока не используем, оставлены для совместимости/будущего)
        self.time_exit_range  = 25
        self.time_exit_trend  = 60

        # Ранний BE
        self.early_be_r_base  = 0.7   # базовый уровень для early BE

        # PARTIAL — теперь адаптивный (относительно ATR/R и режима)
        self.partial_r_base   = 0.7   # базовый уровень partial

        # Второй partial
        self.second_partial_r = 2.0

        # Пирамидинг
        self.max_pyramids     = 3
        self.pyramid_start_r  = 0.5   # старт пирамидинга
        self.pyramid_step_r   = 0.7   # шаг по R

        # Базовый риск для добавления (будет адаптироваться)
        self.add_risk_pct     = 0.007

        self.min_atr          = 1e-8

    # ==========================================================
    # PUBLIC ENTRY
    # ==========================================================

    def manage_position(
        self,
        position:       Dict,
        current_price:  float,
        atr:            float,
        regime,
        bar_index:      int,
        current_candle: Optional[Dict] = None
    ) -> Dict:

        if position["status"] == "CLOSED":
            return position

        entry     = position["entry"]
        sl        = position["sl"]
        direction = position["direction"]

        atr = max(atr, self.min_atr)
        risk = abs(entry - sl)
        if risk <= 0:
            return position

        # R-множитель
        r_multiple = self._r_multiple(direction, entry, sl, current_price)

        # Режимы (поддержка STRONG_TREND + institutional)
        regime_name = self._get_regime_name(regime)
        flags       = self._regime_flags(regime_name)

        # Относительный ATR к риску
        atr_r = atr / risk if risk > 0 else 1.0

        # Худшая цена бара
        if current_candle:
            worst_price = (
                float(current_candle["low"]) if direction == "long"
                else float(current_candle["high"])
            )
        else:
            worst_price = current_price

        # ======================================================
        # STOP LOSS (с определением BE)
        # ======================================================

        if self._stop_hit(direction, worst_price, sl):
            position["status"]     = "CLOSED"
            position["exit_price"] = sl

            entry = position["entry"]
            is_be = False

            if position.get("early_be_done"):
                is_be = True
            if position.get("partial_taken"):
                is_be = True

            if direction == "long" and sl >= entry:
                is_be = True
            if direction == "short" and sl <= entry:
                is_be = True

            position["reason"] = "breakeven_stop" if is_be else "stop_loss"
            return position

        # ======================================================
        # EARLY BE (адаптивный)
        # ======================================================

        if (
            not position.get("partial_taken", False)
            and not position.get("early_be_done", False)
        ):
            early_be_r = self._adaptive_early_be_r(flags, atr_r)

            if r_multiple >= early_be_r:
                be_buffer = self._early_be_buffer(flags, atr_r)

                if direction == "long":
                    new_sl = entry * (1 + be_buffer)
                    position["sl"] = max(position["sl"], new_sl)
                else:
                    new_sl = entry * (1 - be_buffer)
                    position["sl"] = min(position["sl"], new_sl)

                position["early_be_done"] = True
                position["reason"]        = "early_be"

        # ======================================================
        # PARTIAL (адаптивный уровень R)
        # ======================================================

        if not position.get("partial_taken", False):
            partial_r = self._adaptive_partial_r(flags, atr_r)

            if r_multiple >= partial_r:
                position["partial_taken"] = True
                position["partial_price"] = current_price

                buffer = self._partial_be_buffer(atr, flags, atr_r)

                if direction == "long":
                    position["sl"] = max(position["sl"], entry + buffer)
                else:
                    position["sl"] = min(position["sl"], entry - buffer)

                position["reason"] = "partial_be"

        # ======================================================
        # SECOND PARTIAL @ 2R (только в сильном тренде)
        # ======================================================

        if (
            position.get("partial_taken")
            and not position.get("second_partial")
            and flags["strong_trend_like"]
            and r_multiple >= self.second_partial_r
        ):
            position["second_partial"]       = True
            position["second_partial_price"] = current_price

            if direction == "long":
                position["sl"] = max(position["sl"], entry + 0.7 * risk)
            else:
                position["sl"] = min(position["sl"], entry - 0.7 * risk)

            position["reason"] = "second_partial"

        # ======================================================
        # PYRAMIDING (адаптивный, только в сильных режимах)
        # ======================================================

        self._handle_pyramiding(position, r_multiple, flags, current_price, atr, atr_r)

        # ======================================================
        # TRAILING (адаптивный по режиму и partial)
        # ======================================================

        allow_trail = flags["strong_trend_like"] or position.get("partial_taken", False)

        if allow_trail:
            new_sl = self._adaptive_trailing(direction, current_price, atr, flags, position, atr_r)
            old_sl = position["sl"]

            if direction == "long":
                position["sl"] = max(old_sl, new_sl)
            else:
                position["sl"] = min(old_sl, new_sl)

            if self._stop_hit(direction, current_price, position["sl"]):
                position["status"]     = "CLOSED"
                position["exit_price"] = position["sl"]
                position["reason"]     = "trailing_stop"
                return position

        return position

    # ==========================================================
    # REGIME HELPERS
    # ==========================================================

    def _get_regime_name(self, regime) -> str:
        if isinstance(regime, dict):
            return str(regime.get("regime", "UNKNOWN")).upper()
        if isinstance(regime, str):
            return regime.upper()
        return "UNKNOWN"

    def _regime_flags(self, regime_name: str) -> Dict[str, bool]:
        r = regime_name.upper()

        strong_trend = r in (
            "STRONG_TREND",
            "INSTITUTIONAL_TREND",
        )

        expansion = r in (
            "EXPANSION",
            "INSTITUTIONAL_EXPANSION",
        )

        compression = r in (
            "COMPRESSION",
            "INSTITUTIONAL_COMPRESSION",
        )

        exhaustion = r in (
            "EXHAUSTION",
            "INSTITUTIONAL_EXHAUSTION",
        )

        range_like = r in (
            "RANGE",
            "LOW_VOL_RANGE",
            "INSTITUTIONAL_RANGE",
        )

        chaos = r in ("CHAOS", "INSTITUTIONAL_CHAOS")

        strong_trend_like = strong_trend or expansion

        return {
            "strong_trend":      strong_trend,
            "expansion":         expansion,
            "compression":       compression,
            "exhaustion":        exhaustion,
            "range_like":        range_like,
            "chaos":             chaos,
            "strong_trend_like": strong_trend_like,
        }

    # ==========================================================
    # ADAPTIVE EARLY BE
    # ==========================================================

    def _adaptive_early_be_r(self, flags: Dict[str, bool], atr_r: float) -> float:
        r = self.early_be_r_base

        if flags["strong_trend_like"]:
            r += 0.1
        if flags["range_like"] or flags["compression"]:
            r -= 0.1

        if atr_r > 1.5:
            r += 0.1
        elif atr_r < 0.7:
            r -= 0.1

        return max(0.4, min(r, 1.0))

    def _early_be_buffer(self, flags: Dict[str, bool], atr_r: float) -> float:
        base = 0.0012  # 0.12%

        if flags["strong_trend_like"]:
            base *= 0.8
        if flags["range_like"] or flags["compression"]:
            base *= 1.2

        if atr_r > 1.5:
            base *= 0.8
        elif atr_r < 0.7:
            base *= 1.2

        return max(0.0006, min(base, 0.0025))

    # ==========================================================
    # ADAPTIVE PARTIAL
    # ==========================================================

    def _adaptive_partial_r(self, flags: Dict[str, bool], atr_r: float) -> float:
        r = self.partial_r_base

        if flags["strong_trend_like"]:
            r += 0.2
        if flags["range_like"] or flags["compression"]:
            r -= 0.1

        if atr_r > 1.5:
            r += 0.1
        elif atr_r < 0.7:
            r -= 0.1

        return max(0.5, min(r, 1.2))

    def _partial_be_buffer(self, atr: float, flags: Dict[str, bool], atr_r: float) -> float:
        buffer = 0.10 * atr

        if flags["strong_trend_like"]:
            buffer *= 0.8
        if flags["range_like"] or flags["compression"]:
            buffer *= 1.2

        if atr_r > 1.5:
            buffer *= 0.8
        elif atr_r < 0.7:
            buffer *= 1.2

        return max(0.05 * atr, min(buffer, 0.25 * atr))

    # ==========================================================
    # PYRAMIDING AFTER PARTIAL (STRONG/EXPANSION only)
    # ==========================================================

    def _handle_pyramiding(
        self,
        position:      Dict,
        r_multiple:    float,
        flags:         Dict[str, bool],
        current_price: float,
        atr:           float,
        atr_r:         float,
    ) -> None:

        if not position.get("partial_taken"):
            return

        if not flags["strong_trend_like"]:
            return

        if "pyramid_count" not in position:
            position["pyramid_count"] = 0

        if position["pyramid_count"] >= self.max_pyramids:
            return

        next_level = self.pyramid_start_r + position["pyramid_count"] * self.pyramid_step_r

        if r_multiple < next_level:
            return

        risk = abs(position["entry"] - position["sl"])
        if risk <= 0:
            return

        if atr_r < 0.6:
            add_risk_pct = 0.005
        elif atr_r < 1.2:
            add_risk_pct = 0.007
        else:
            add_risk_pct = 0.010

        if flags["compression"] or flags["exhaustion"]:
            add_risk_pct *= 0.6

        base_notional = position["entry"] * position["size"]
        add_notional  = base_notional * add_risk_pct
        add_size      = add_notional / current_price

        position["pyramid_signal"] = {
            "add_size":      add_size,
            "add_price":     current_price,
            "level_r":       next_level,
            "pyramid_index": position["pyramid_count"] + 1,
        }

        position["pyramid_count"] += 1
        position["reason"] = "pyramid_add"

    # ==========================================================
    # R MULTIPLE
    # ==========================================================

    def _r_multiple(self, direction: str, entry: float, sl: float, price: float) -> float:
        risk = abs(entry - sl)
        if risk <= 0:
            return 0.0
        return (price - entry) / risk if direction == "long" else (entry - price) / risk

    # ==========================================================
    # STOP HIT
    # ==========================================================

    def _stop_hit(self, direction: str, price: float, sl: float) -> bool:
        return price <= sl if direction == "long" else price >= sl

    # ==========================================================
    # ADAPTIVE TRAILING
    # ==========================================================

    def _adaptive_trailing(
        self,
        direction: str,
        price:     float,
        atr:       float,
        flags:     Dict[str, bool],
        position:  Dict,
        atr_r:     float,
    ) -> float:

        if flags["strong_trend_like"]:
            trail = 0.55 * atr
        elif flags["range_like"] or flags["compression"]:
            trail = 1.30 * atr
        else:
            trail = 1.10 * atr

        if position.get("second_partial"):
            trail *= 0.75

        if atr_r > 1.5:
            trail *= 0.9
        elif atr_r < 0.7:
            trail *= 1.1

        swing_offset = 0.35 * atr

        if direction == "long":
            return min(price - trail, price - swing_offset)
        else:
            return max(price + trail, price + swing_offset)
