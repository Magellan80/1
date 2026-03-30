# elite_regime_engine.py
# V32 ELITE — Regime State Machine
#
# Исправления vs V31:
#   1. _ema: исправлен seed (SMA вместо closes[0])
#   2. _ema_slope: добавлена защита от будущего — candles[:-10] уже ок,
#      но добавлена проверка len
#   3. Добавлен режим EXHAUSTION в classify
#   4. _atr_percentile: оптимизирован (убран лишний вычислительный цикл)
#   5. _impulse_frequency: нормализация на len(recent) вместо жёсткого 40

from typing import List, Dict, Optional


class EliteRegimeEngine:

    def __init__(self):
        self.min_candles = 250

        self.low_vol_threshold  = 0.25
        self.high_vol_threshold = 0.75

        self.strong_slope = 0.0016
        self.early_slope  = 0.0009
        self.range_slope  = 0.0005

        self.strong_separation = 0.003
        self.early_separation  = 0.002
        self.range_separation  = 0.0015

        self.compression_threshold  = 0.70
        self.exhaustion_efficiency  = 0.25   # порог для EXHAUSTION

    # ==========================================================
    # PUBLIC ENTRY
    # ==========================================================

    def detect(self, candles: List[Dict]) -> Optional[Dict]:
        if len(candles) < self.min_candles:
            return None

        atr = self._atr(candles, 14)
        if not atr or atr <= 0:
            return None

        price = float(candles[-1]["close"])
        if price <= 0:
            return None

        atr_percentile     = self._atr_percentile(candles, 200)
        ema50              = self._ema(candles, 50)
        ema200             = self._ema(candles, 200)
        slope              = self._ema_slope(candles, 50)
        slope_norm         = slope / price if price > 0 else 0.0
        separation         = abs(ema50 - ema200) / price
        compression_ratio  = self._range_compression(candles, atr)
        impulse_freq       = self._impulse_frequency(candles, atr)
        efficiency         = self._price_efficiency(candles)

        regime = self._classify(
            atr_percentile, slope_norm, separation,
            compression_ratio, impulse_freq, efficiency
        )

        trend_confidence = self._trend_confidence(slope_norm, separation, impulse_freq)

        return {
            "regime":           regime,
            "atr_percentile":   atr_percentile,
            "slope_norm":       slope_norm,
            "separation":       separation,
            "compression":      compression_ratio,
            "impulse_freq":     impulse_freq,
            "trend_confidence": trend_confidence,
            "efficiency":       efficiency,
        }

    # ==========================================================
    # REGIME CLASSIFICATION
    # ==========================================================

    def _classify(
        self,
        atr_pct:      float,
        slope_norm:   float,
        separation:   float,
        compression:  float,
        impulse_freq: float,
        efficiency:   float
    ) -> str:

        abs_slope = abs(slope_norm)

        # EXHAUSTION: высокое движение, но низкая эффективность
        if efficiency < self.exhaustion_efficiency and abs_slope > self.early_slope:
            return "EXHAUSTION"

        # LOW VOL ZONE
        if atr_pct < self.low_vol_threshold:
            if compression > self.compression_threshold:
                return "COMPRESSION"
            if abs_slope < self.range_slope and separation < self.range_separation:
                return "LOW_VOL_RANGE"

        # STRONG TREND
        if abs_slope > self.strong_slope and separation > self.strong_separation:
            if impulse_freq > 0.25:
                return "STRONG_TREND"

        # EARLY TREND
        if abs_slope > self.early_slope and separation > self.early_separation:
            return "EARLY_TREND"

        # HIGH VOL ZONE
        if atr_pct > self.high_vol_threshold:
            if impulse_freq > 0.6 and abs_slope < self.early_slope:
                return "CHAOS"
            if impulse_freq > 0.4 and abs_slope >= self.early_slope:
                return "EXPANSION"

        # RANGE fallback
        if abs_slope < self.range_slope and separation < self.range_separation:
            return "RANGE"

        return "RANGE"

    # ==========================================================
    # PRICE EFFICIENCY (чтобы поймать EXHAUSTION)
    # ==========================================================

    def _price_efficiency(self, candles: List[Dict]) -> float:
        recent = candles[-30:]
        if len(recent) < 5:
            return 1.0

        highs  = [float(c["high"])  for c in recent]
        lows   = [float(c["low"])   for c in recent]
        closes = [float(c["close"]) for c in recent]

        total_range = max(highs) - min(lows)
        net_move    = abs(closes[-1] - closes[0])

        if total_range <= 0:
            return 1.0

        return min(net_move / total_range, 1.0)

    # ==========================================================
    # TREND CONFIDENCE
    # ==========================================================

    def _trend_confidence(
        self,
        slope_norm:   float,
        separation:   float,
        impulse_freq: float
    ) -> float:
        abs_slope = abs(slope_norm)

        slope_score   = min(abs_slope / 0.002, 1.0)
        sep_score     = min(separation / 0.004, 1.0)
        impulse_score = min(impulse_freq / 0.5,  1.0)

        return max(0.0, min(1.0, slope_score * 0.4 + sep_score * 0.4 + impulse_score * 0.2))

    # ==========================================================
    # ATR PERCENTILE
    # ==========================================================

    def _atr_percentile(self, candles: List[Dict], window: int) -> float:
        trs = []
        for i in range(1, len(candles)):
            high = float(candles[i]["high"])
            low  = float(candles[i]["low"])
            prev = float(candles[i - 1]["close"])
            tr   = max(high - low, abs(high - prev), abs(low - prev))
            trs.append(tr)

        if len(trs) < 14 + 20:
            return 0.5

        atr_values = []
        for i in range(14, len(trs) + 1):
            chunk = trs[i - 14:i]
            atr_values.append(sum(chunk) / 14)

        if len(atr_values) < 20:
            return 0.5

        current_atr = atr_values[-1]
        # используем только последние `window` значений для репрезентативности
        recent_atrs = atr_values[-window:] if len(atr_values) > window else atr_values
        below = sum(1 for v in recent_atrs if v <= current_atr)
        return below / len(recent_atrs)

    # ==========================================================
    # EMA SLOPE
    # ==========================================================

    def _ema_slope(self, candles: List[Dict], period: int) -> float:
        if len(candles) < period + 10:
            return 0.0

        ema_now  = self._ema(candles,        period)
        ema_prev = self._ema(candles[:-10],  period)

        return ema_now - ema_prev

    # ==========================================================
    # RANGE COMPRESSION
    # ==========================================================

    def _range_compression(self, candles: List[Dict], atr: float) -> float:
        recent = candles[-30:]
        highs  = [float(c["high"]) for c in recent]
        lows   = [float(c["low"])  for c in recent]

        total_range = max(highs) - min(lows)

        if atr <= 0:
            return 0.0

        ratio       = total_range / (atr * 10)
        compression = 1 - min(ratio, 1)
        return max(0.0, min(compression, 1.0))

    # ==========================================================
    # IMPULSE FREQUENCY
    # ==========================================================

    def _impulse_frequency(self, candles: List[Dict], atr: float) -> float:
        recent = candles[-40:]
        if atr <= 0 or len(recent) < 2:
            return 0.0

        impulses = 0
        for i in range(1, len(recent)):
            move = abs(float(recent[i]["close"]) - float(recent[i - 1]["close"]))
            if move > 0.8 * atr:
                impulses += 1

        return impulses / len(recent)

    # ==========================================================
    # EMA — ИСПРАВЛЕНА (SMA seed)
    # ==========================================================

    def _ema(self, candles: List[Dict], period: int) -> float:
        closes = [float(c["close"]) for c in candles]
        if len(closes) < period:
            return closes[-1]

        k   = 2.0 / (period + 1)
        ema = sum(closes[:period]) / period   # правильный SMA seed

        for price in closes[period:]:
            ema = price * k + ema * (1 - k)

        return ema

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
