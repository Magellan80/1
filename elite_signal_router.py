# elite_signal_router.py
# V40.0A — TREND‑ONLY версия
# - Полностью отключён reversal‑engine
# - Чистая маршрутизация тренд‑сигналов
# - Anti‑Flat
# - HTF‑Boost
# - HTF‑Structure Boost
# - Полная совместимость с существующими импортами и сигнатурами

from typing import Optional, Dict, Any


class EliteSignalRouter:
    def __init__(self):
        # минимальные пороги
        self.min_trend_quality = 0.55
        self.min_alignment = -0.05

        # где тренд имеет смысл
        self.trend_allowed = {
            "EARLY_TREND",
            "STRONG_TREND",
            "EXPANSION",
            "HTF_TREND",
            "HTF_HIGH_VOL_TREND",
            "HTF_HIGH_VOL",
        }

        # Anti‑Flat параметры
        self.min_atr_pct = 0.0015
        self.min_htf_range_pct = 0.002

        # HTF‑structure веса
        self.htf_trend_bonus = 0.04
        self.htf_choch_trend_penalty = 0.04

    # ==========================================================
    # ВХОДНАЯ ТОЧКА
    # ==========================================================

    def route(
        self,
        trend_signal: Optional[Dict[str, Any]],
        reversal_signal: Optional[Dict[str, Any]],  # оставлен для совместимости
        regime: Dict[str, Any],
        htf: Dict[str, Any],
        symbol: str = "BTCUSDT",
    ) -> Optional[Dict[str, Any]]:

        regime_type = str(regime.get("regime", "RANGE")).upper()
        alignment = float(htf.get("alignment_score", 0.0))

        # Anti‑Flat
        if self._is_flat_market(regime, htf):
            return None

        # нормализуем тренд‑сигнал
        trend_signal = self._normalize_signal(trend_signal)

        # HTF‑Boost (только тренд)
        trend_signal, _ = self._apply_htf_boost(trend_signal, None, htf)

        # HTF‑Structure Boost (только тренд)
        trend_signal, _ = self._apply_htf_structure_boost(trend_signal, None, htf)

        # фильтр тренда
        trend_signal = self._filter_trend(trend_signal, regime_type, alignment)

        return trend_signal

    # ==========================================================
    # ANTI‑FLAT
    # ==========================================================

    def _is_flat_market(self, regime, htf):
        atr = float(regime.get("atr", 0.0))
        price = float(regime.get("price", 0.0))
        htf_range = float(htf.get("range_pct", 0.0))

        if price <= 0:
            return False

        if atr / price < self.min_atr_pct:
            return True

        if htf_range < self.min_htf_range_pct:
            return True

        return False

    # ==========================================================
    # HTF‑BOOST
    # ==========================================================

    def _apply_htf_boost(self, trend_signal, reversal_signal, htf):
        if not trend_signal:
            return trend_signal, None

        htf_slope = float(htf.get("slope", 0.0))
        htf_momentum = float(htf.get("momentum", 0.0))
        htf_range = float(htf.get("range_pct", 0.0))
        exhausted = bool(htf.get("exhausted", False))

        # Momentum Boost
        if htf_slope > 0 and htf_momentum > 0:
            trend_signal["quality"] += 0.03

        # Exhaustion Dampener
        if exhausted:
            trend_signal["quality"] -= 0.05

        # Range Sensitivity
        if htf_range < 0.003:
            trend_signal["quality"] -= 0.03
        elif htf_range > 0.01:
            trend_signal["quality"] += 0.03

        return trend_signal, None

    # ==========================================================
    # HTF‑STRUCTURE BOOST
    # ==========================================================

    def _apply_htf_structure_boost(self, trend_signal, reversal_signal, htf):
        if not trend_signal:
            return trend_signal, None

        htf_struct_1h = str(htf.get("htf_structure_1h", "neutral")).lower()
        htf_struct_4h = str(htf.get("htf_structure_4h", "neutral")).lower()

        bos_1h = htf.get("htf_bos_1h", None)
        bos_4h = htf.get("htf_bos_4h", None)
        choch_1h = htf.get("htf_choch_1h", None)
        choch_4h = htf.get("htf_choch_4h", None)

        direction = trend_signal.get("signal", "")

        # Совпадение структуры HTF с направлением тренда
        if direction == "long" and (htf_struct_1h == "bullish" or htf_struct_4h == "bullish"):
            trend_signal["quality"] += self.htf_trend_bonus

        if direction == "short" and (htf_struct_1h == "bearish" or htf_struct_4h == "bearish"):
            trend_signal["quality"] += self.htf_trend_bonus

        # CHoCH против тренда → штраф
        if choch_1h or choch_4h:
            if direction == "long" and (bos_1h == "bearish" or bos_4h == "bearish"):
                trend_signal["quality"] -= self.htf_choch_trend_penalty
            if direction == "short" and (bos_1h == "bullish" or bos_4h == "bullish"):
                trend_signal["quality"] -= self.htf_choch_trend_penalty

        return trend_signal, None

    # ==========================================================
    # НОРМАЛИЗАЦИЯ
    # ==========================================================

    def _normalize_signal(self, s):
        if not s:
            return None
        if "quality" not in s or s["quality"] is None:
            s["quality"] = 0.0
        return s

    # ==========================================================
    # ФИЛЬТР TREND
    # ==========================================================

    def _filter_trend(self, s, regime_type, alignment):
        if not s:
            return None
        if s["quality"] < self.min_trend_quality:
            return None
        if alignment < self.min_alignment:
            return None
        if regime_type == "CHAOS":
            return None
        return s
