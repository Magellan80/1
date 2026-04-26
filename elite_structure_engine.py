# elite_structure_engine.py
# V33.0_B ELITE — Microstructure Engine (soft upgrade, metrics replaced)
# - Ключи в выходном словаре те же, что в V32.1
# - Внутри: ATR-адаптивные свинги, clarity_v2, pullback_v2, улучшенный impulse

from typing import List, Dict, Optional


class EliteStructureEngine:

    def __init__(self, swing_window: int = 3, atr_swing_factor: float = 0.8):
        self.swing_window = swing_window
        self.atr_swing_factor = atr_swing_factor

        self.min_swings  = 6
        self.min_candles = 120

        self.min_clarity  = 0.35
        self.min_impulse  = 0.25
        self.min_pullback = 0.25

    # ==========================================================
    # PUBLIC ENTRY
    # ==========================================================

    def analyze(self, candles: List[Dict]) -> Optional[Dict]:
        if len(candles) < self.min_candles:
            return None

        atr = self._atr(candles, 14)
        if not atr or atr <= 0:
            return None

        swings = self._detect_swings_atr_adaptive(candles, atr)
        if len(swings) < self.min_swings:
            return None

        structure_state = self._classify_structure(swings)
        impulse         = self._impulse_strength_v2(candles, atr, swings)
        pullback        = self._pullback_quality_v2(candles, swings, atr)
        clarity         = self._structure_clarity_v2(swings, atr)
        momentum_decay  = self._momentum_decay(candles)

        if clarity < self.min_clarity:
            return None
        if impulse < self.min_impulse:
            return None
        if pullback < self.min_pullback:
            return None

        micro_confirmed, micro_score = self._micro_reversal(candles, atr)

        recent_candles = candles[-20:]
        recent_highs   = [c["high"] for c in recent_candles]
        recent_lows    = [c["low"]  for c in recent_candles]

        return {
            "structure":            structure_state,
            "impulse_strength":     impulse,
            "pullback_quality":     pullback,
            "clarity_index":        clarity,
            "momentum_decay":       momentum_decay,
            "micro_confirmed":      micro_confirmed,
            "micro_reversal_score": micro_score,
            "recent_candles":       recent_candles,
            "recent_highs":         recent_highs,
            "recent_lows":          recent_lows,
            "swings":               swings[-12:],
        }

    # ==========================================================
    # ATR-ADAPTIVE SWING DETECTION
    # ==========================================================

    def _detect_swings_atr_adaptive(self, candles: List[Dict], atr: float) -> List[Dict]:
        swings = []
        w = self.swing_window
        min_move = atr * self.atr_swing_factor

        for i in range(w, len(candles) - w):
            high = float(candles[i]["high"])
            low  = float(candles[i]["low"])

            is_swing_high = all(
                float(candles[i - j]["high"]) < high and
                float(candles[i + j]["high"]) < high
                for j in range(1, w + 1)
            )
            is_swing_low = all(
                float(candles[i - j]["low"]) > low and
                float(candles[i + j]["low"]) > low
                for j in range(1, w + 1)
            )

            if is_swing_high:
                if not swings or abs(high - swings[-1]["price"]) >= min_move:
                    swings.append({"type": "high", "price": high, "index": i})

            if is_swing_low:
                if not swings or abs(low - swings[-1]["price"]) >= min_move:
                    swings.append({"type": "low", "price": low, "index": i})

        return swings

    # ==========================================================
    # STRUCTURE CLASSIFICATION
    # ==========================================================

    def _classify_structure(self, swings: List[Dict]) -> str:
        recent = swings[-6:]
        highs  = [s for s in recent if s["type"] == "high"]
        lows   = [s for s in recent if s["type"] == "low"]

        if len(highs) < 2 or len(lows) < 2:
            return "neutral"

        hh = highs[-1]["price"] > highs[-2]["price"]
        hl = lows[-1]["price"]  > lows[-2]["price"]
        lh = highs[-1]["price"] < highs[-2]["price"]
        ll = lows[-1]["price"]  < lows[-2]["price"]

        if hh and hl:
            return "bullish"
        if lh and ll:
            return "bearish"
        return "range"

    # ==========================================================
    # IMPULSE STRENGTH V2
    # ==========================================================

    def _impulse_strength_v2(self, candles: List[Dict], atr: float, swings: List[Dict]) -> float:
        if atr <= 0 or len(swings) < 3:
            return 0.0

        recent = candles[-20:]
        move   = abs(float(recent[-1]["close"]) - float(recent[0]["open"]))

        # учитываем последний swing-leg
        last_leg = abs(swings[-1]["price"] - swings[-2]["price"])
        leg_score = last_leg / (atr * 1.5)

        raw = (move / atr) * 0.6 + leg_score * 0.4
        return max(0.0, min(raw, 3.0))

    # ==========================================================
    # PULLBACK QUALITY V2
    # ==========================================================

    def _pullback_quality_v2(self, candles: List[Dict], swings: List[Dict], atr: float) -> float:
        if len(swings) < 4 or atr <= 0:
            return 0.0

        last_swings = swings[-4:]
        last_highs  = [s for s in last_swings if s["type"] == "high"]
        last_lows   = [s for s in last_swings if s["type"] == "low"]

        if not last_highs or not last_lows:
            return 0.0

        last_high = last_highs[-1]["price"]
        last_low  = last_lows[-1]["price"]

        recent = candles[-20:]
        closes = [float(c["close"]) for c in recent]

        ref = closes[0]
        last = closes[-1]

        if last_high > last_low:
            total_move = last_high - last_low
        else:
            total_move = last_low - last_high

        if total_move <= 0:
            return 0.0

        depth = abs(last - ref) / max(total_move, atr * 0.5)
        depth = max(0.0, min(depth, 2.0))

        score = 1.0 - abs(depth - 0.5) * 1.5
        return max(0.0, min(score, 1.0))

    # ==========================================================
    # STRUCTURE CLARITY V2
    # ==========================================================

    def _structure_clarity_v2(self, swings: List[Dict], atr: float) -> float:
        if len(swings) < 6 or atr <= 0:
            return 0.0

        moves = []
        directions = []
        for i in range(1, len(swings)):
            d = swings[i]["price"] - swings[i - 1]["price"]
            moves.append(abs(d))
            directions.append(1 if d > 0 else -1)

        if len(moves) < 4:
            return 0.0

        avg_move = sum(moves[-6:]) / 6
        dir_consistency = sum(directions[-6:]) / 6.0

        move_score = max(0.0, min(avg_move / (atr * 1.5), 1.0))
        dir_score  = abs(dir_consistency)

        clarity = 0.6 * move_score + 0.4 * dir_score
        return max(0.0, min(clarity, 1.0))

    # ==========================================================
    # MOMENTUM DECAY (как было)
    # ==========================================================

    def _momentum_decay(self, candles: List[Dict]) -> bool:
        closes = [float(c["close"]) for c in candles[-8:]]
        diffs  = [closes[i] - closes[i - 1] for i in range(1, len(closes))]

        if len(diffs) < 4:
            return False

        first = sum(diffs[:3]) / 3
        last  = sum(diffs[-3:]) / 3

        if abs(first) < 1e-8:
            return False

        return abs(last) < abs(first) * 0.7

    # ==========================================================
    # MICRO REVERSAL (как было)
    # ==========================================================

    def _micro_reversal(self, candles: List[Dict], atr: float):
        if len(candles) < 3 or atr <= 0:
            return False, 0.0

        c = candles[-1]
        o = float(c["open"])
        h = float(c["high"])
        l = float(c["low"])
        cl= float(c["close"])

        body        = abs(cl - o)
        upper_wick  = h - max(cl, o)
        lower_wick  = min(cl, o) - l
        total_range = h - l

        if total_range <= 0:
            return False, 0.0

        bullish_pin = (lower_wick > body * 2.0) and (lower_wick > upper_wick * 2.0)
        bearish_pin = (upper_wick > body * 2.0) and (upper_wick > lower_wick * 2.0)

        micro_confirmed = bullish_pin or bearish_pin

        score = 0.0
        if micro_confirmed:
            wick_ratio = max(lower_wick, upper_wick) / total_range
            score = min(wick_ratio, 1.0)

        return micro_confirmed, score

    # ==========================================================
    # ATR
    # ==========================================================

    def _atr(self, candles: List[Dict], period: int) -> Optional[float]:
        trs = []
        for i in range(1, len(candles)):
            high = float(candles[i]["high"])
            low  = float(candles[i]["low"])
            prev = float(candles[i - 1]["close"])
            tr   = max(high - low, abs(high - prev), abs(low - prev))
            trs.append(tr)

        if len(trs) < period:
            return None

        return sum(trs[-period:]) / period
