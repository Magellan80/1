# elite_structure_engine.py
# V34.0 — Microstructure Engine + SMC layer + Volume Confirmation

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

        # volume thresholds
        self.bos_volume_mult = 1.4
        self.choch_volume_mult = 1.2
        self.sweep_volume_mult = 1.2

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

        # MICRO SMC + VOLUME
        smc = self._micro_smc(candles, swings, atr)
        micro_bos   = smc["micro_bos"]
        micro_choch = smc["micro_choch"]
        micro_sweep = smc["micro_sweep"]
        micro_range = smc["micro_range"]
        micro_phase = smc["micro_phase"]

        if len(swings) >= 2:
            last_leg_abs = abs(swings[-1]["price"] - swings[-2]["price"])
            swing_leg_strength = max(0.0, min(last_leg_abs / (atr * 2.0), 1.0))
        else:
            swing_leg_strength = 0.0

        micro_displacement = max(0.0, min(impulse / 2.0, 1.0))
        micro_confidence = micro_score
        micro_momentum_decay = momentum_decay

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

            # micro-layer
            "micro_confidence":     micro_confidence,
            "micro_phase":          micro_phase,
            "micro_range":          micro_range,
            "micro_range_regime":   micro_range,
            "micro_bos":            micro_bos,
            "micro_choch":          micro_choch,
            "micro_sweep":          micro_sweep,
            "micro_displacement":   micro_displacement,
            "swing_leg_strength":   swing_leg_strength,
            "micro_momentum_decay": micro_momentum_decay,
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

        total_move = abs(last_high - last_low)
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
    # MOMENTUM DECAY
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
    # MICRO REVERSAL
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
    # MICRO SMC + VOLUME
    # ==========================================================

    def _micro_smc(self, candles: List[Dict], swings: List[Dict], atr: float) -> Dict:
        if len(swings) < 4 or atr <= 0:
            return {
                "micro_bos": None,
                "micro_choch": False,
                "micro_sweep": False,
                "micro_range": False,
                "micro_phase": "mature",
            }

        recent_sw = swings[-6:]
        highs  = [s for s in recent_sw if s["type"] == "high"]
        lows   = [s for s in recent_sw if s["type"] == "low"]

        micro_bos = None
        micro_choch = False
        micro_sweep = False
        micro_range = False
        micro_phase = "mature"

        # volume series
        vols = [float(c["volume"]) for c in candles[-80:]] if "volume" in candles[-1] else []
        vol_sma = sum(vols) / len(vols) if vols else 0.0

        def swing_volume_ok(idx: int, mult: float) -> bool:
            if not vols or vol_sma <= 0:
                return True
            # привязываемся к индексу свечи
            if idx < 0 or idx >= len(candles):
                return True
            v = float(candles[idx].get("volume", vol_sma))
            return v >= vol_sma * mult

        # BOS
        if len(highs) >= 2:
            last_h = highs[-1]
            prev_h = highs[-2]
            diff_h = last_h["price"] - prev_h["price"]
            if diff_h >= atr * 0.2 and swing_volume_ok(last_h["index"], self.bos_volume_mult):
                micro_bos = "bullish"
        if len(lows) >= 2:
            last_l = lows[-1]
            prev_l = lows[-2]
            diff_l = last_l["price"] - prev_l["price"]
            if diff_l <= -atr * 0.2 and swing_volume_ok(last_l["index"], self.bos_volume_mult):
                micro_bos = "bearish"

        # CHoCH
        if len(recent_sw) >= 4:
            a, b, c, d = recent_sw[-4:]
            if a["price"] < b["price"] < c["price"] and d["price"] < c["price"]:
                if swing_volume_ok(d["index"], self.choch_volume_mult):
                    micro_choch = True
            if a["price"] > b["price"] > c["price"] and d["price"] > c["price"]:
                if swing_volume_ok(d["index"], self.choch_volume_mult):
                    micro_choch = True

        # Sweep
        if len(highs) >= 2:
            h_prev = highs[-2]
            h_last = highs[-1]
            if h_last["price"] > h_prev["price"] and abs(h_last["price"] - h_prev["price"]) < atr * 0.4:
                if swing_volume_ok(h_last["index"], self.sweep_volume_mult):
                    micro_sweep = True
        if len(lows) >= 2:
            l_prev = lows[-2]
            l_last = lows[-1]
            if l_last["price"] < l_prev["price"] and abs(l_last["price"] - l_prev["price"]) < atr * 0.4:
                if swing_volume_ok(l_last["index"], self.sweep_volume_mult):
                    micro_sweep = True

        # micro-range
        prices = [s["price"] for s in recent_sw]
        total_span = max(prices) - min(prices)
        if total_span < atr * 1.0:
            micro_range = True

        # micro-phase
        last = recent_sw[-1]
        prev = recent_sw[-2]
        bars = abs(last["index"] - prev["index"])
        if bars <= 2:
            micro_phase = "early"
        elif bars <= 6:
            micro_phase = "mature"
        else:
            micro_phase = "late"

        return {
            "micro_bos": micro_bos,
            "micro_choch": micro_choch,
            "micro_sweep": micro_sweep,
            "micro_range": micro_range,
            "micro_phase": micro_phase,
        }

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
