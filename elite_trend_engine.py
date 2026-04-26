# elite_trend_engine.py
# V41.1 — Trend Engine (Clean Trend-Only, без micro-зависимостей)

from typing import Dict, Optional


class EliteTrendEngine:

    def __init__(self):
        self.base_min_quality = 0.52
        self.base_min_clarity = 0.36
        self.base_min_impulse = 0.028
        self.base_min_alignment = 0.42
        self.base_min_signed_strength = 0.007

    # ==========================================================
    # PUBLIC ENTRY
    # ==========================================================

    def evaluate(self, structure, regime, htf, symbol="BTCUSDT"):

            if not structure or not regime or not htf:
                return None

            min_clarity, min_impulse, min_alignment, min_signed_strength = \
                self._adaptive_thresholds(structure, regime, htf, symbol)

            direction = self._direction(structure, regime, htf, min_alignment, min_signed_strength)
            if not direction:
                return None

            clarity = float(structure.get("clarity_index", 0.0))
            impulse = float(structure.get("impulse_strength", 0.0))

            if clarity < min_clarity:
                return None
            if impulse < min_impulse:
                return None

            quality = self._quality_score(structure, regime, htf, symbol)
            if quality < self.base_min_quality:
                return None

            if not self._local_impulse_ok(structure, direction, symbol):
                return None

            candles = structure.get("recent_candles", [])

            # ============================================================
            # ANTI-EARLY 2.0 (анализ тела и диапазона)
            # ============================================================
            if len(candles) >= 2:
                c1 = candles[-1]
                c2 = candles[-2]

                body1 = abs(c1["close"] - c1["open"])
                range1 = c1["high"] - c1["low"]
                body2 = abs(c2["close"] - c2["open"])
                range2 = c2["high"] - c2["low"]

                if range1 <= 0 or range2 <= 0:
                    return None

                if direction == "long" and c1["close"] < c1["open"]:
                    return None
                if direction == "short" and c1["close"] > c1["open"]:
                    return None

                if body1 < range1 * 0.25:
                    return None

                close_pos = (c1["close"] - c1["low"]) / range1
                if direction == "long" and close_pos < 0.55:
                    return None
                if direction == "short" and close_pos > 0.45:
                    return None

                if body1 < body2 * 0.5:
                    return None

                diagnostics = self._entry_diagnostics(structure, regime, direction)

                return {
                    "signal": direction,
                    "quality": quality,
                    "type": "trend",
                    "entry_diagnostics": diagnostics
                }


    # ==========================================================
    # ENTRY DIAGNOSTICS
    # ==========================================================

    def _entry_diagnostics(self, structure: Dict, regime: Dict, direction: str) -> Dict:
        swings = structure.get("swings", [])
        recent = structure.get("recent_candles", [])
        atr = float(regime.get("atr_percentile", 0.5))

        if not swings or not recent:
            return {
                "distance_from_swing": None,
                "distance_atr_norm": None,
                "bars_from_swing": None,
                "impulse_phase": "unknown",
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
                "impulse_phase": "unknown",
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

        if bars <= 1:
            phase = "early"
        elif bars <= 5:
            phase = "optimal"
        else:
            phase = "late"

        if dist_norm > 1.2:
            timing = "late"
        elif dist_norm < 0.25:
            timing = "early"
        else:
            timing = "optimal"

        return {
            "distance_from_swing": round(dist, 6),
            "distance_atr_norm": round(dist_norm, 6),
            "bars_from_swing": bars,
            "impulse_phase": phase,
            "timing_class": timing
        }

    # ==========================================================
    # ADAPTIVE THRESHOLDS
    # ==========================================================

    def _adaptive_thresholds(self, structure: Dict, regime: Dict, htf: Dict, symbol: str):
        atr_p = float(regime.get("atr_percentile", 0.5))

        min_clarity = self.base_min_clarity
        min_impulse = self.base_min_impulse
        min_alignment = self.base_min_alignment
        min_signed_strength = self.base_min_signed_strength

        symbol_upper = symbol.upper()
        is_btc = symbol_upper.startswith("BTC")
        is_high_vol = symbol_upper.startswith(("DOGE", "SOL", "OP", "ARB", "AVAX"))

        if atr_p > 0.60:
            min_clarity = 0.32
            min_impulse = 0.022
            min_alignment = 0.34
            min_signed_strength = 0.005
            if is_btc:
                min_alignment = 0.42
                min_signed_strength = 0.007

        elif atr_p > 0.30:
            min_clarity = 0.36
            min_impulse = 0.027
            min_alignment = 0.42
            min_signed_strength = 0.007
            if is_btc:
                min_alignment = 0.47
                min_signed_strength = 0.009

        else:
            min_clarity = 0.42
            min_impulse = 0.032
            min_alignment = 0.52
            min_signed_strength = 0.011
            if is_high_vol:
                min_alignment = 0.47
                min_signed_strength = 0.009

        return min_clarity, min_impulse, min_alignment, min_signed_strength

    # ==========================================================
    # DIRECTION
    # ==========================================================

    def _direction(self, structure: Dict, regime: Dict, htf: Dict,
                   min_alignment: float, min_signed_strength: float) -> Optional[str]:

        regime_name = str(regime.get("regime", "RANGE")).upper()
        bias = str(htf.get("bias", "neutral")).lower()
        alignment = float(htf.get("alignment_score", 0.5))
        signed_strength = float(htf.get("signed_trend_strength", 0.0))
        local_structure = structure.get("structure", "neutral")

        allowed = [
            "TREND", "EARLY_TREND", "STRONG_TREND",
            "EXPANSION", "HTF_TREND", "HTF_WEAK_TREND", "COMPRESSION",
            "HTF_HIGH_VOL_TREND", "HTF_HIGH_VOL",
        ]
        if regime_name not in allowed:
            return None

        if regime_name == "COMPRESSION":
            if abs(signed_strength) < max(0.014, min_signed_strength * 1.6):
                return None
            if alignment < max(0.57, min_alignment + 0.12):
                return None

        if abs(signed_strength) < min_signed_strength:
            return None

        if alignment < min_alignment:
            return None

        if (
            local_structure == "neutral"
            and abs(signed_strength) > (min_signed_strength * 3.8)
            and alignment > (min_alignment + 0.22)
        ):
            if bias == "bullish":
                return "long"
            if bias == "bearish":
                return "short"
            return None

        if local_structure == "bullish" and bias == "bullish":
            return "long"

        if local_structure == "bearish" and bias == "bearish":
            return "short"

        return None

    # ==========================================================
    # QUALITY SCORE
    # ==========================================================

    def _quality_score(self, structure: Dict, regime: Dict, htf: Dict, symbol: str) -> float:

        clarity = float(structure.get("clarity_index", 0.0))
        impulse = float(structure.get("impulse_strength", 0.0))
        alignment = float(htf.get("alignment_score", 0.5))
        signed_strength = abs(float(htf.get("signed_trend_strength", 0.0)))

        score = 0.0

        score += clarity * 0.32

        vol_factor = 1.0
        symbol_upper = symbol.upper()
        if symbol_upper.startswith(("DOGE", "SOL", "OP", "ARB", "AVAX")):
            vol_factor = 0.85
        impulse_norm = min(impulse * vol_factor / 2.0, 1.0)
        score += impulse_norm * 0.26

        score += alignment * 0.22

        if signed_strength > 0.032:
            score += 0.20
        elif signed_strength > 0.020:
            score += 0.14
        elif signed_strength > 0.011:
            score += 0.09
        else:
            score += 0.03

        noise_penalty = max(0.0, 0.25 - clarity)
        score -= noise_penalty * 0.17

        regime_name = str(regime.get("regime", "RANGE")).upper()
        if regime_name in ("TREND", "STRONG_TREND", "EXPANSION", "HTF_TREND"):
            score += 0.05
        elif regime_name in ("EARLY_TREND", "HTF_WEAK_TREND", "COMPRESSION"):
            score += 0.03

        return max(0.0, min(score, 1.0))

    # ==========================================================
    # LOCAL IMPULSE (EMA8 оставлен как есть)
    # ==========================================================

    def _local_impulse_ok(self, structure: Dict, direction: str, symbol: str) -> bool:

        candles = structure.get("recent_candles", [])
        if len(candles) < 5:
            return True

        closes = [c["close"] for c in candles[-5:]]
        highs  = [c["high"]  for c in candles[-5:]]
        lows   = [c["low"]   for c in candles[-5:]]

        last_close = closes[-1]

        sma5 = sum(closes[-5:]) / 5

        ema8 = closes[-1]
        for c in closes[-5:]:
            ema8 = ema8 * 0.7 + c * 0.3

        symbol_upper = symbol.upper()
        is_btc = symbol_upper.startswith("BTC")
        is_high_vol = symbol_upper.startswith(("DOGE", "SOL", "OP", "ARB", "AVAX"))

        if len(closes) < 3:
            return True

        if direction == "long":

            if closes[-1] < closes[-2] < closes[-3]:
                return False

            if last_close < sma5 or last_close < ema8:
                if is_btc or not is_high_vol:
                    return False

            if last_close < min(lows[-3:]):
                return False

        if direction == "short":

            if closes[-1] > closes[-2] > closes[-3]:
                return False

            if last_close > sma5 or last_close > ema8:
                if is_btc or not is_high_vol:
                    return False

            if last_close > max(highs[-3:]):
                return False

        return True
