# elite_htf_sync.py
# V35.1 — HTF Sync + HTF Structure Engine (с поддержкой HIGH_VOL режимов)
# BOS/CHoCH PRO:
#   • ATR‑адаптивные свинги
#   • BOS с ATR + импульсным фильтром
#   • CHoCH с учётом структуры и контекста
#   • Multi‑Layer HTF Context (совместимо с Trend/Router)

from typing import List, Dict, Optional
import bisect


class EliteHTFSync:

    def __init__(self):
        # трендовые пороги
        self.min_trend_strength = 0.004
        self.strong_trend_strength = 0.012
        self.exhaustion_decay_ratio = 0.55

        # структура HTF
        self.swing_window = 3
        self.min_swings = 4
        self.min_candles = 120
        self.min_swing_atr_mult = 0.3  # ATR‑адаптивный фильтр свингов

        # BOS/CHoCH фильтры
        self.bos_min_atr_mult = 0.25   # минимальный пробой в ATR
        self.bos_min_impulse_body_atr = 0.35  # минимальное тело свечи в ATR
        self.choch_min_clarity = 0.35  # CHoCH только при достаточной ясности структуры

        # параметры для slope/momentum
        self.slope_lookback = 5
        self.momentum_window = 20

        # порог высокой волатильности для HTF режимов
        self.high_vol_range_pct = 0.015

    # ==========================================================
    # PUBLIC ENTRY
    # ==========================================================

    def analyze(
        self,
        ts: int,
        htf_15m: List[Dict],
        htf_1h: List[Dict],
        htf_4h: List[Dict],
    ) -> Optional[Dict]:

        c15 = self._get_current_candle(ts, htf_15m)
        c1h = self._get_current_candle(ts, htf_1h)
        c4h = self._get_current_candle(ts, htf_4h)

        if not c15 or not c1h or not c4h:
            return None

        # === трендовые метрики ===
        trend_15 = self._trend_metrics(htf_15m)
        trend_1h = self._trend_metrics(htf_1h)
        trend_4h = self._trend_metrics(htf_4h)

        # === структура HTF ===
        htf_struct_1h = self._htf_structure(htf_1h)
        htf_struct_4h = self._htf_structure(htf_4h)

        # === Router‑метрики (сначала, чтобы range_pct был доступен для режима) ===
        slope = self._htf_slope(htf_4h)
        momentum = self._htf_momentum(htf_1h)
        range_pct = self._htf_range_pct(htf_struct_1h, c1h)

        # === агрегированные HTF‑метрики ===
        bias = self._combined_bias(trend_15, trend_1h, trend_4h)
        signed_strength = self._combined_signed_strength(trend_15, trend_1h, trend_4h)
        alignment = self._alignment_score(trend_15, trend_1h, trend_4h, bias)
        htf_regime = self._htf_regime(bias, signed_strength, alignment, htf_struct_1h, range_pct)
        exhausted = self._is_exhausted(htf_1h)

        return {
            # базовые поля
            "bias": bias,
            "signed_trend_strength": signed_strength,
            "alignment_score": alignment,
            "htf_regime": htf_regime,
            "exhausted": exhausted,

            # Router / HTF‑Boost
            "slope": slope,
            "momentum": momentum,
            "range_pct": range_pct,

            # Multi‑Layer HTF Context
            "bias_15m": trend_15["bias"],
            "bias_1h": trend_1h["bias"],
            "bias_4h": trend_4h["bias"],
            "signed_trend_strength_15m": trend_15["signed_strength"],
            "signed_trend_strength_1h": trend_1h["signed_strength"],
            "signed_trend_strength_4h": trend_4h["signed_strength"],

            # структура 1h
            "htf_structure_1h": htf_struct_1h["structure"],
            "htf_structure_clarity_1h": htf_struct_1h["clarity"],
            "htf_swings_1h": htf_struct_1h["swings"],
            "htf_range_high_1h": htf_struct_1h["range_high"],
            "htf_range_low_1h": htf_struct_1h["range_low"],
            "htf_bos_1h": htf_struct_1h["bos"],
            "htf_choch_1h": htf_struct_1h["choch"],

            # структура 4h
            "htf_structure_4h": htf_struct_4h["structure"],
            "htf_structure_clarity_4h": htf_struct_4h["clarity"],
            "htf_swings_4h": htf_struct_4h["swings"],
            "htf_range_high_4h": htf_struct_4h["range_high"],
            "htf_range_low_4h": htf_struct_4h["range_low"],
            "htf_bos_4h": htf_struct_4h["bos"],
            "htf_choch_4h": htf_struct_4h["choch"],
        }

    # ==========================================================
    # CURRENT CANDLE
    # ==========================================================

    def _get_current_candle(self, ts: int, candles: List[Dict]) -> Optional[Dict]:
        if not candles:
            return None
        timestamps = [c["timestamp"] for c in candles]
        idx = bisect.bisect_right(timestamps, ts) - 1
        if idx < 0:
            return None
        return candles[idx]

    # ==========================================================
    # TREND METRICS
    # ==========================================================

    def _trend_metrics(self, candles: List[Dict]) -> Dict:
        if len(candles) < self.min_candles:
            return {"bias": "neutral", "signed_strength": 0.0}

        closes = [float(c["close"]) for c in candles[-120:]]
        if len(closes) < 4:
            return {"bias": "neutral", "signed_strength": 0.0}

        start = closes[0]
        end = closes[-1]
        rel = (end - start) / start if start != 0 else 0.0

        diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        avg_step = sum(diffs[-10:]) / max(1, min(10, len(diffs)))

        signed_strength = rel + avg_step / max(abs(start), 1e-8)

        if signed_strength > self.min_trend_strength:
            bias = "bullish"
        elif signed_strength < -self.min_trend_strength:
            bias = "bearish"
        else:
            bias = "neutral"

        return {"bias": bias, "signed_strength": signed_strength}

    # ==========================================================
    # ATR
    # ==========================================================

    def _atr(self, candles: List[Dict], period: int = 14) -> Optional[float]:
        trs = []
        for i in range(1, len(candles)):
            high = float(candles[i]["high"])
            low = float(candles[i]["low"])
            prev = float(candles[i - 1]["close"])
            tr = max(high - low, abs(high - prev), abs(low - prev))
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period

    # ==========================================================
    # HTF STRUCTURE (ATR‑свинги + BOS/CHoCH PRO)
    # ==========================================================

    def _htf_structure(self, candles: List[Dict]) -> Dict:
        if len(candles) < self.min_candles:
            return self._empty_struct()

        atr = self._atr(candles, 14)
        swings = self._detect_swings(candles, atr)

        if len(swings) < self.min_swings:
            return self._empty_struct(swings)

        structure = self._classify_structure(swings)
        clarity = self._structure_clarity(swings)
        range_high, range_low = self._range_bounds(swings)
        bos, choch = self._bos_choch(candles, swings, atr, structure, clarity)

        return {
            "structure": structure,
            "clarity": clarity,
            "swings": swings[-12:],
            "range_high": range_high,
            "range_low": range_low,
            "bos": bos,
            "choch": choch,
        }

    def _empty_struct(self, swings=None):
        return {
            "structure": "neutral",
            "clarity": 0.0,
            "swings": swings if swings else [],
            "range_high": None,
            "range_low": None,
            "bos": None,
            "choch": None,
        }

    def _detect_swings(self, candles: List[Dict], atr: Optional[float]) -> List[Dict]:
        swings = []
        w = self.swing_window

        for i in range(w, len(candles) - w):
            high = float(candles[i]["high"])
            low = float(candles[i]["low"])

            if all(float(candles[i - j]["high"]) < high and float(candles[i + j]["high"]) < high for j in range(1, w + 1)):
                swings.append({"type": "high", "price": high, "index": i})

            if all(float(candles[i - j]["low"]) > low and float(candles[i + j]["low"]) > low for j in range(1, w + 1)):
                swings.append({"type": "low", "price": low, "index": i})

        # ATR‑адаптивная фильтрация
        if atr and atr > 0 and len(swings) > 1:
            filtered = [swings[0]]
            for s in swings[1:]:
                if abs(s["price"] - filtered[-1]["price"]) >= atr * self.min_swing_atr_mult:
                    filtered.append(s)
            swings = filtered

        return swings

    def _classify_structure(self, swings: List[Dict]) -> str:
        recent = swings[-6:]
        highs = [s for s in recent if s["type"] == "high"]
        lows = [s for s in recent if s["type"] == "low"]

        if len(highs) < 2 or len(lows) < 2:
            return "neutral"

        hh = highs[-1]["price"] > highs[-2]["price"]
        hl = lows[-1]["price"] > lows[-2]["price"]
        lh = highs[-1]["price"] < highs[-2]["price"]
        ll = lows[-1]["price"] < lows[-2]["price"]

        if hh and hl:
            return "bullish"
        if lh and ll:
            return "bearish"
        return "range"

    def _bos_choch(
        self,
        candles: List[Dict],
        swings: List[Dict],
        atr: Optional[float],
        structure: str,
        clarity: float,
    ):
        """
        BOS:
          • пробой предыдущего экстремума по тренду
          • пробой >= bos_min_atr_mult * ATR
          • свеча‑пробой с импульсным телом (body >= bos_min_impulse_body_atr * ATR)
        CHoCH:
          • смена доминирующего направления BOS
          • только если структура достаточно ясная (clarity >= choch_min_clarity)
        """
        if len(swings) < 4 or not atr or atr <= 0:
            return None, None

        recent = swings[-8:]
        highs = [s for s in recent if s["type"] == "high"]
        lows = [s for s in recent if s["type"] == "low"]

        if len(highs) < 2 and len(lows) < 2:
            return None, None

        bos = None
        choch = None

        # --- helper: импульсная свеча пробоя ---
        def is_impulse_break(idx_candle: int, direction: str) -> bool:
            if idx_candle <= 0 or idx_candle >= len(candles):
                return False
            c = candles[idx_candle]
            o = float(c["open"])
            h = float(c["high"])
            l = float(c["low"])
            cl = float(c["close"])
            body = abs(cl - o)
            rng = h - l
            if rng <= 0:
                return False
            # тело должно быть значимым относительно ATR
            if body < self.bos_min_impulse_body_atr * atr:
                return False
            # направление тела должно совпадать с пробоем
            if direction == "bullish" and cl <= o:
                return False
            if direction == "bearish" and cl >= o:
                return False
            return True

        # --- BOS по high ---
        if len(highs) >= 2:
            last_h = highs[-1]
            prev_h = highs[-2]
            diff_h = last_h["price"] - prev_h["price"]
            if diff_h >= atr * self.bos_min_atr_mult and is_impulse_break(last_h["index"], "bullish"):
                bos = "bullish"
            elif diff_h <= -atr * self.bos_min_atr_mult and is_impulse_break(last_h["index"], "bearish"):
                bos = "bearish"

        # --- BOS по low ---
        if len(lows) >= 2:
            last_l = lows[-1]
            prev_l = lows[-2]
            diff_l = last_l["price"] - prev_l["price"]
            if diff_l >= atr * self.bos_min_atr_mult and is_impulse_break(last_l["index"], "bullish"):
                if bos is None:
                    bos = "bullish"
            elif diff_l <= -atr * self.bos_min_atr_mult and is_impulse_break(last_l["index"], "bearish"):
                if bos is None:
                    bos = "bearish"

        if bos is None:
            return None, None

        # --- CHoCH: смена направления BOS относительно текущей структуры ---
        if clarity < self.choch_min_clarity:
            return bos, None  # структура грязная, CHoCH не доверяем

        if structure == "bullish" and bos == "bearish":
            choch = True
        elif structure == "bearish" and bos == "bullish":
            choch = True
        else:
            choch = None

        return bos, choch

    def _structure_clarity(self, swings: List[Dict]) -> float:
        if len(swings) < 4:
            return 0.0

        moves = [abs(swings[i]["price"] - swings[i - 1]["price"]) for i in range(1, len(swings))]
        if not moves:
            return 0.0

        avg = sum(moves[-4:]) / max(1, min(4, len(moves)))
        total_range = max(s["price"] for s in swings) - min(s["price"] for s in swings)
        if total_range <= 0:
            return 0.0

        return max(0.0, min(avg / (total_range / 2.0), 1.0))

    def _range_bounds(self, swings: List[Dict]):
        highs = [s["price"] for s in swings if s["type"] == "high"]
        lows = [s["price"] for s in swings if s["type"] == "low"]
        if not highs or not lows:
            return None, None
        return max(highs), min(lows)

    # ==========================================================
    # COMBINED HTF METRICS
    # ==========================================================

    def _combined_bias(self, t15: Dict, t1h: Dict, t4h: Dict) -> str:
        votes = [t15["bias"], t1h["bias"], t4h["bias"]]
        if votes.count("bullish") >= 2:
            return "bullish"
        if votes.count("bearish") >= 2:
            return "bearish"
        return "neutral"

    def _combined_signed_strength(self, t15: Dict, t1h: Dict, t4h: Dict) -> float:
        return (
            t15["signed_strength"] * 0.25 +
            t1h["signed_strength"] * 0.45 +
            t4h["signed_strength"] * 0.30
        )

    def _alignment_score(self, t15: Dict, t1h: Dict, t4h: Dict, bias: str) -> float:
        dirs = []
        for t in (t15, t1h, t4h):
            if t["signed_strength"] > self.min_trend_strength:
                dirs.append("bullish")
            elif t["signed_strength"] < -self.min_trend_strength:
                dirs.append("bearish")
            else:
                dirs.append("neutral")

        if bias == "bullish":
            aligned = dirs.count("bullish")
        elif bias == "bearish":
            aligned = dirs.count("bearish")
        else:
            aligned = max(dirs.count("bullish"), dirs.count("bearish"))

        return aligned / 3.0

    def _htf_regime(
        self,
        bias: str,
        signed_strength: float,
        alignment: float,
        htf_struct: Dict,
        range_pct: float,
    ) -> str:
        structure = htf_struct.get("structure", "neutral")
        clarity = htf_struct.get("clarity", 0.0)

        # HTF рейндж
        if structure == "range" and clarity > 0.35:
            if range_pct >= self.high_vol_range_pct:
                return "HTF_HIGH_VOL"
            return "HTF_RANGE"

        # слабый тренд
        if abs(signed_strength) < self.min_trend_strength:
            return "HTF_WEAK_TREND"

        # сильный тренд
        if abs(signed_strength) > self.strong_trend_strength and alignment > 0.66:
            if range_pct >= self.high_vol_range_pct:
                return "HTF_HIGH_VOL_TREND"
            return "HTF_TREND"

        return "HTF_WEAK_TREND"

    def _is_exhausted(self, candles: List[Dict]) -> bool:
        if len(candles) < 40:
            return False

        closes = [float(c["close"]) for c in candles[-40:]]
        diffs = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        if len(diffs) < 8:
            return False

        first = sum(diffs[:8]) / 8
        last = sum(diffs[-8:]) / 8

        if abs(first) < 1e-8:
            return False

        return abs(last) < abs(first) * self.exhaustion_decay_ratio

    # ==========================================================
    # HTF SLOPE / MOMENTUM / RANGE_PCT
    # ==========================================================

    def _htf_slope(self, candles: List[Dict]) -> float:
        if len(candles) < self.min_candles:
            return 0.0

        closes = [float(c["close"]) for c in candles]
        if len(closes) < self.slope_lookback + 2:
            return 0.0

        price_now = closes[-1]
        price_prev = closes[-1 - self.slope_lookback]

        if price_now <= 0:
            return 0.0

        return (price_now - price_prev) / price_now

    def _htf_momentum(self, candles: List[Dict]) -> float:
        if len(candles) < self.momentum_window:
            return 0.0

        recent = candles[-self.momentum_window:]
        first_close = float(recent[0]["close"])
        last_close = float(recent[-1]["close"])

        if last_close <= 0:
            return 0.0

        return (last_close - first_close) / last_close

    def _htf_range_pct(self, htf_struct_1h: Dict, current_candle_1h: Dict) -> float:
        range_high = htf_struct_1h.get("range_high")
        range_low = htf_struct_1h.get("range_low")
        price = float(current_candle_1h.get("close", 0.0))

        if range_high is None or range_low is None or price <= 0:
            return 0.0

        width = float(range_high) - float(range_low)
        if width <= 0:
            return 0.0

        return width / price
