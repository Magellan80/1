# elite_reversal_engine.py
# V41.0_B — Reversal Engine (Aggressive Micro-Aware)

from typing import Dict, Optional


class EliteReversalEngine:

    def __init__(self):
        self.base_min_quality = 0.60
        self.base_min_clarity = 0.38
        self.base_min_impulse = 0.11

    # ==========================================================
    # PUBLIC ENTRY
    # ==========================================================

    def evaluate(self, structure: Dict, regime: Dict, htf: Dict,
                 symbol: str = "BTCUSDT") -> Optional[Dict]:

        if not structure or not regime or not htf:
            return None

        # --- micro-structure (агрессивно) ---
        micro_conf = float(structure.get("micro_confidence", 0.0))
        micro_phase = str(structure.get("micro_phase", "neutral"))
        micro_range = bool(structure.get("micro_range", False)
                           or structure.get("micro_range_regime", False))
        micro_bos = structure.get("micro_bos", None)
        micro_choch = structure.get("micro_choch", None)
        micro_sweep = structure.get("micro_sweep", None)
        micro_disp = float(structure.get("micro_displacement", 0.0))
        swing_strength = float(structure.get("swing_leg_strength", 0.0))
        m_mdecay = bool(structure.get("micro_momentum_decay", False))

        thresholds = self._adaptive_thresholds(structure, regime, htf, symbol)

        thresholds = self._micro_hard_thresholds(
            thresholds,
            micro_conf,
            micro_phase,
            micro_range,
            swing_strength,
            micro_sweep,
            micro_choch,
            m_mdecay,
        )

        min_clarity = thresholds["min_clarity"]
        min_impulse = thresholds["min_impulse"]
        min_micro = thresholds["min_micro"]
        min_atr = thresholds["min_atr"]
        min_quality = thresholds["min_quality"]

        direction = self._direction(structure, regime, htf, symbol)
        if not direction:
            return None

        clarity = float(structure.get("clarity_index", 0.0))
        impulse = float(structure.get("impulse_strength", 0.0))
        micro_score = float(structure.get("micro_reversal_score", 0.0))
        micro_confirmed = bool(structure.get("micro_confirmed", False))
        atr_eff = self._effective_atr(regime)
        htf_exhausted = bool(htf.get("exhausted", False))

        # ==========================================================
        # HARD MICRO FILTERS
        # ==========================================================

        if micro_range:
            return None
        if micro_conf < 0.45:
            return None
        if micro_phase == "late":
            return None
        if m_mdecay:
            return None

        if clarity < min_clarity:
            return None
        if impulse < min_impulse:
            return None
        if micro_score < min_micro:
            return None
        if not micro_confirmed:
            return None

        if atr_eff < min_atr and not htf_exhausted:
            return None

        if not self._local_impulse_ok(structure, direction, symbol):
            return None

        # ==========================================================
        # QUALITY
        # ==========================================================

        quality = self._quality_score(structure, regime, htf, symbol)

        quality = self._micro_aggressive_quality(
            quality,
            micro_conf,
            micro_phase,
            micro_sweep,
            micro_choch,
            micro_disp,
            swing_strength,
        )

        if quality < min_quality:
            return None

        diagnostics = self._entry_diagnostics(structure, regime, direction)

        try:
            from signal_logger import log_signal
            log_signal("reversal_engine", {
                "direction": direction,
                "quality": quality,
                "reversal_signal": direction,
                "clarity": clarity,
                "impulse": impulse,
                "micro_score": micro_score,
                "micro_confirmed": micro_confirmed,
                "atr_eff": atr_eff,
                "htf_exhausted": htf_exhausted,
                "regime": str(regime.get("regime", "")),
                "atr_percentile": float(regime.get("atr_percentile", 0.0)),
                "symbol": symbol,
                "subtype": structure.get("structure", "unknown"),
                "type": "reversal",
                "entry_diag": diagnostics,
            })
        except Exception as e:
            print("ReversalEngine logging error:", e)

        return {
            "signal": direction,
            "quality": quality,
            "type": "reversal",
            "entry_diagnostics": diagnostics
        }

    # ==========================================================
    # MICRO HARD THRESHOLDS
    # ==========================================================

    def _micro_hard_thresholds(
        self,
        t: Dict,
        micro_conf: float,
        micro_phase: str,
        micro_range: bool,
        swing_strength: float,
        micro_sweep,
        micro_choch,
        m_mdecay: bool,
    ) -> Dict:

        # низкий confidence → сильно повышаем требования
        if micro_conf < 0.45:
            t["min_clarity"] += 0.05
            t["min_impulse"] += 0.02
            t["min_micro"] += 0.05

        # высокий confidence → снижаем
        if micro_conf > 0.65:
            t["min_clarity"] -= 0.03
            t["min_impulse"] -= 0.01
            t["min_micro"] -= 0.04

        # фаза
        if micro_phase == "early":
            t["min_micro"] -= 0.04
        elif micro_phase == "mature":
            t["min_micro"] -= 0.01
        elif micro_phase == "late":
            t["min_micro"] += 0.06

        # sweep → реверс усиливается
        if micro_sweep:
            t["min_micro"] -= 0.06

        # choch → реверс усиливается
        if micro_choch:
            t["min_micro"] -= 0.05

        # сильная нога тренда → реверс сложнее
        if swing_strength > 0.6:
            t["min_micro"] += 0.05

        # momentum decay → реверс сложнее
        if m_mdecay:
            t["min_micro"] += 0.04

        # micro-range → реверс запрещён
        if micro_range:
            t["min_micro"] += 0.10

        return t

    # ==========================================================
    # MICRO AGGRESSIVE QUALITY
    # ==========================================================

    def _micro_aggressive_quality(
        self,
        quality: float,
        micro_conf: float,
        micro_phase: str,
        micro_sweep,
        micro_choch,
        micro_disp: float,
        swing_strength: float,
    ) -> float:

        if micro_conf < 0.45:
            quality -= 0.08
        elif micro_conf > 0.65:
            quality += 0.08

        if micro_phase == "early":
            quality += 0.06
        elif micro_phase == "mature":
            quality += 0.02
        elif micro_phase == "late":
            quality -= 0.08

        if micro_sweep:
            quality += 0.08

        if micro_choch:
            quality += 0.07

        if micro_disp > 0.7:
            quality += 0.06
        elif micro_disp < 0.3:
            quality -= 0.04

        if swing_strength > 0.65:
            quality -= 0.05

        return max(0.0, min(quality, 1.0))

    # ==========================================================
    # ENTRY DIAGNOSTICS
    # ==========================================================

    def _entry_diagnostics(self, structure: Dict, regime: Dict,
                           direction: str) -> Dict:

        swings = structure.get("swings", [])
        recent = structure.get("recent_candles", [])
        atr = float(regime.get("atr_percentile", 0.5))

        if not swings or not recent:
            return {
                "distance_from_swing": None,
                "distance_atr_norm": None,
                "bars_from_swing": None,
                "reversal_phase": "unknown",
                "timing_class": "unknown"
            }

        last_high = None
        last_low = None
        for s in reversed(swings):
            if s["type"] == "high" and last_high is None:
                last_high = s
            if s["type"] == "low" and last_low is None:
                last_low = s
            if last_high and last_low:
                break

        if not last_high or not last_low:
            return {
                "distance_from_swing": None,
                "distance_atr_norm": None,
                "bars_from_swing": None,
                "reversal_phase": "unknown",
                "timing_class": "unknown"
            }

        current_index = swings[-1]["index"]
        entry_price = recent[-1]["close"]

        if direction == "long":
            dist = entry_price - last_low["price"]
            bars = current_index - last_low["index"]
        else:
            dist = last_high["price"] - entry_price
            bars = current_index - last_high["index"]

        dist = max(dist, 0.0)
        dist_norm = dist / max(atr, 0.0001)

        if bars <= 0:
            phase = "early"
        elif bars <= 3:
            phase = "optimal"
        else:
            phase = "late"

        if dist_norm > 0.8:
            timing = "late"
        elif dist_norm < 0.20:
            timing = "early"
        else:
            timing = "optimal"

        return {
            "distance_from_swing": round(dist, 6),
            "distance_atr_norm": round(dist_norm, 6),
            "bars_from_swing": bars,
            "reversal_phase": phase,
            "timing_class": timing
        }

    # ==========================================================
    # ADAPTIVE THRESHOLDS
    # ==========================================================

    def _adaptive_thresholds(self, structure: Dict, regime: Dict,
                             htf: Dict, symbol: str):

        atr_p = float(regime.get("atr_percentile", 0.5))
        symbol_upper = symbol.upper()

        is_btc = symbol_upper.startswith("BTC")
        is_high_vol = symbol_upper.startswith(
            ("DOGE", "SOL", "OP", "ARB", "AVAX"))

        min_clarity = self.base_min_clarity
        min_impulse = self.base_min_impulse
        min_micro = 0.25
        min_atr = 0.40
        min_quality = self.base_min_quality

        if atr_p > 0.60:
            min_clarity = 0.32
            min_impulse = 0.09
            min_micro = 0.22
            min_atr = 0.30
            min_quality = 0.54

        elif atr_p > 0.30:
            min_clarity = 0.36
            min_impulse = 0.10
            min_micro = 0.26
            min_atr = 0.35
            min_quality = 0.56

        else:
            min_clarity = 0.40
            min_impulse = 0.12
            min_micro = 0.30
            min_atr = 0.40
            min_quality = 0.62

        if is_btc:
            min_clarity += 0.03
            min_impulse += 0.02
            min_micro += 0.05
            min_quality += 0.05

        return {
            "min_clarity": min_clarity,
            "min_impulse": min_impulse,
            "min_micro": min_micro,
            "min_atr": min_atr,
            "min_quality": min_quality,
        }

    # ==========================================================
    # DIRECTION LOGIC
    # ==========================================================

    def _direction(self, structure: Dict, regime: Dict, htf: Dict,
                   symbol: str) -> Optional[str]:

        local_structure = structure.get("structure")
        signed_strength = float(htf.get("signed_trend_strength", 0.0))
        regime_name = str(regime.get("regime", "RANGE")).upper()

        if regime_name in ["STRONG_TREND", "EXPANSION"] and abs(
                signed_strength) > 0.012:
            return None

        if local_structure == "bearish":
            return "long"
        if local_structure == "bullish":
            return "short"

        impulse = float(structure.get("impulse_strength", 0.0))
        if local_structure == "neutral" and impulse > 0.12:
            return "long" if signed_strength < 0 else "short"

        return None

    # ==========================================================
    # QUALITY SCORE
    # ==========================================================

    def _quality_score(self, structure: Dict, regime: Dict, htf: Dict,
                       symbol: str) -> float:

        clarity = float(structure.get("clarity_index", 0.0))
        impulse = float(structure.get("impulse_strength", 0.0))
        atr_eff = self._effective_atr(regime)
        alignment = float(htf.get("alignment_score", 0.5))
        micro_score = float(structure.get("micro_reversal_score", 0.0))
        htf_exhausted = bool(htf.get("exhausted", False))
        regime_name = str(regime.get("regime", "RANGE")).upper()

        score = 0.0

        score += clarity * 0.30
        score += min(impulse / 2.0, 1.0) * 0.25
        score += atr_eff * 0.20

        if regime_name == "EXHAUSTION":
            score += 0.12
        if htf_exhausted:
            score += 0.10

        if alignment < 0.45:
            score += 0.10
        elif alignment < 0.60:
            score += 0.05

        score += min(micro_score, 1.0) * 0.12

        noise_pen_penalty = max(0.0, 0.20 - clarity)
        score -= noise_pen_penalty * 0.15

        return max(0.0, min(score, 1.0))

    # ==========================================================
    # LOCAL IMPULSE FILTER
    # ==========================================================

    def _local_impulse_ok(self, structure: Dict, direction: str,
                          symbol: str) -> bool:

        candles = structure.get("recent_candles", [])
        if len(candles) < 5:
            return True

        closes = [c["close"] for c in candles[-5:]]
        highs = [c["high"] for c in candles[-5:]]
        lows = [c["low"] for c in candles[-5:]]

        last_close = closes[-1]

        sma5 = sum(closes[-5:]) / 5

        ema8 = closes[-1]
        for c in closes[-5:]:
            ema8 = ema8 * 0.7 + c * 0.3

        symbol_upper = symbol.upper()
        is_btc = symbol_upper.startswith("BTC")

        if direction == "long":

            if closes[-1] > closes[-2] > closes[-3]:
                return False

            if last_close > sma5 or last_close > ema8:
                return False if is_btc else True

            if last_close > max(highs[-3:]):
                return False

        if direction == "short":

            if closes[-1] < closes[-2] < closes[-3]:
                return False

            if last_close < sma5 or last_close < ema8:
                return False if is_btc else True

            if last_close < min(lows[-3:]):
                return False

        return True

    # ==========================================================
    # HELPERS
    # ==========================================================

    def _effective_atr(self, regime: Dict) -> float:
        atr_main = float(regime.get("atr_percentile", 0.0))
        atr_short = float(regime.get("atr_short_percentile", atr_main))
        atr_long = float(regime.get("atr_long_percentile", atr_main))
        atr_eff = 0.5 * atr_short + 0.5 * atr_long
        return max(0.0, min(atr_eff, 1.0))
