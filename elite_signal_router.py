# elite_signal_router.py
# V41.0 — High‑Vol / Low‑Vol Aware Router
# Полностью согласован с HTF‑sync V36 и движками V41

from typing import Optional, Dict, Any


class EliteSignalRouter:
    def __init__(self):
        # минимальные пороги
        self.min_trend_quality = 0.55
        self.min_reversal_quality = 0.60
        self.min_alignment = -0.05

        # где реверс имеет смысл
        self.reversal_allowed = {
            "RANGE", "LOW_VOL_RANGE", "EXHAUSTION", "COMPRESSION",
            "HTF_RANGE", "HTF_LOW_VOL_RANGE"
        }

        # где тренд имеет смысл
        self.trend_allowed = {
            "EARLY_TREND", "STRONG_TREND", "EXPANSION",
            "HTF_TREND", "HTF_HIGH_VOL_TREND", "HTF_HIGH_VOL"
        }

        # Anti‑Flat параметры
        self.min_atr_pct = 0.0015
        self.min_htf_range_pct = 0.002

        # HTF‑structure веса
        self.htf_trend_bonus = 0.04
        self.htf_range_reversal_bonus = 0.04
        self.htf_choch_reversal_bonus = 0.05
        self.htf_choch_trend_penalty = 0.04

        # NEW: High‑Vol / Low‑Vol веса
        self.high_vol_trend_bonus = 0.05
        self.high_vol_reversal_penalty = 0.05
        self.low_vol_reversal_bonus = 0.05
        self.low_vol_trend_penalty = 0.04

    # ==========================================================
    # ВХОДНАЯ ТОЧКА
    # ==========================================================

    def route(
        self,
        trend_signal: Optional[Dict[str, Any]],
        reversal_signal: Optional[Dict[str, Any]],
        regime: Dict[str, Any],
        htf: Dict[str, Any],
        symbol: str = "BTCUSDT",
    ) -> Optional[Dict[str, Any]]:

        regime_type = str(regime.get("regime", "RANGE")).upper()
        htf_regime = str(htf.get("htf_regime", "HTF_RANGE")).upper()
        alignment = float(htf.get("alignment_score", 0.0))
        exhausted = bool(htf.get("exhausted", False))

        # Anti‑Flat
        if self._is_flat_market(regime, htf):
            return None

        # нормализуем сигналы
        trend_signal = self._normalize_signal(trend_signal)
        reversal_signal = self._normalize_signal(reversal_signal)

        # HTF‑Boost (старый блок)
        trend_signal, reversal_signal = self._apply_htf_boost(
            trend_signal, reversal_signal, htf
        )

        # HTF‑Structure Boost (структура + BOS/CHoCH)
        trend_signal, reversal_signal = self._apply_htf_structure_boost(
            trend_signal, reversal_signal, htf
        )

        # NEW: High‑Vol / Low‑Vol Boost
        trend_signal, reversal_signal = self._apply_volatility_boost(
            trend_signal, reversal_signal, htf_regime
        )

        # мягкие фильтры
        trend_signal = self._filter_trend(trend_signal, regime_type, alignment)
        reversal_signal = self._filter_reversal(reversal_signal, regime_type, exhausted)

        # нет сигналов
        if not trend_signal and not reversal_signal:
            return None

        # один сигнал
        if trend_signal and not reversal_signal:
            return trend_signal
        if reversal_signal and not trend_signal:
            return reversal_signal

        # оба сигнала → умный выбор
        return self._smart_pick(trend_signal, reversal_signal, regime_type, htf_regime)

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
    # HTF‑BOOST (старший ТФ: slope/momentum/range/exhaustion)
    # ==========================================================

    def _apply_htf_boost(self, trend_signal, reversal_signal, htf):
        if not trend_signal and not reversal_signal:
            return trend_signal, reversal_signal

        htf_slope = float(htf.get("slope", 0.0))
        htf_momentum = float(htf.get("momentum", 0.0))
        htf_range = float(htf.get("range_pct", 0.0))
        exhausted = bool(htf.get("exhausted", False))

        # Momentum Boost
        if htf_slope > 0.0 and htf_momentum > 0.0:
            if trend_signal:
                trend_signal["quality"] += 0.03
            if reversal_signal:
                reversal_signal["quality"] -= 0.03

        # Exhaustion Dampener
        if exhausted:
            if trend_signal:
                trend_signal["quality"] -= 0.05
            if reversal_signal:
                reversal_signal["quality"] += 0.05

        # Range Sensitivity
        if htf_range < 0.003:
            if trend_signal:
                trend_signal["quality"] -= 0.03
            if reversal_signal:
                reversal_signal["quality"] += 0.03
        elif htf_range > 0.01:
            if trend_signal:
                trend_signal["quality"] += 0.03
            if reversal_signal:
                reversal_signal["quality"] -= 0.03

        return trend_signal, reversal_signal

    # ==========================================================
    # HTF‑STRUCTURE BOOST
    # ==========================================================

    def _apply_htf_structure_boost(self, trend_signal, reversal_signal, htf):
        if not trend_signal and not reversal_signal:
            return trend_signal, reversal_signal

        htf_struct_1h = str(htf.get("htf_structure_1h", "neutral")).lower()
        htf_struct_4h = str(htf.get("htf_structure_4h", "neutral")).lower()
        bos_1h = htf.get("htf_bos_1h", None)
        choch_1h = htf.get("htf_choch_1h", None)
        bos_4h = htf.get("htf_bos_4h", None)
        choch_4h = htf.get("htf_choch_4h", None)

        # TREND: бонус, если HTF‑структура совпадает с направлением тренда
        if trend_signal:
            direction = trend_signal.get("signal", "")
            if direction == "long" and ("bullish" in (htf_struct_1h, htf_struct_4h)):
                trend_signal["quality"] += self.htf_trend_bonus
            if direction == "short" and ("bearish" in (htf_struct_1h, htf_struct_4h)):
                trend_signal["quality"] += self.htf_trend_bonus

            # CHoCH против тренда → штраф
            if choch_1h or choch_4h:
                if direction == "long" and ("bearish" in (bos_1h, bos_4h)):
                    trend_signal["quality"] -= self.htf_choch_trend_penalty
                if direction == "short" and ("bullish" in (bos_1h, bos_4h)):
                    trend_signal["quality"] -= self.htf_choch_trend_penalty

        # REVERSAL: бонус в рейндже или при CHoCH
        if reversal_signal:
            direction = reversal_signal.get("signal", "")
            if htf_struct_1h == "range" or htf_struct_4h == "range":
                reversal_signal["quality"] += self.htf_range_reversal_bonus

            if choch_1h or choch_4h:
                if direction == "long" and ("bullish" in (bos_1h, bos_4h)):
                    reversal_signal["quality"] += self.htf_choch_reversal_bonus
                if direction == "short" and ("bearish" in (bos_1h, bos_4h)):
                    reversal_signal["quality"] += self.htf_choch_reversal_bonus

        return trend_signal, reversal_signal

    # ==========================================================
    # NEW: HIGH‑VOL / LOW‑VOL BOOST
    # ==========================================================

    def _apply_volatility_boost(self, trend_signal, reversal_signal, htf_regime):
        if not trend_signal and not reversal_signal:
            return trend_signal, reversal_signal

        # HIGH VOL TREND → усиливаем тренд
        if htf_regime == "HTF_HIGH_VOL_TREND":
            if trend_signal:
                trend_signal["quality"] += self.high_vol_trend_bonus
            if reversal_signal:
                reversal_signal["quality"] -= self.high_vol_reversal_penalty

        # HIGH VOL (без тренда) → тренд опасен, реверс слабый
        if htf_regime == "HTF_HIGH_VOL":
            if trend_signal:
                trend_signal["quality"] -= self.high_vol_reversal_penalty
            if reversal_signal:
                reversal_signal["quality"] -= self.high_vol_reversal_penalty

        # LOW VOL RANGE → реверс идеален, тренд слаб
        if htf_regime == "HTF_LOW_VOL_RANGE":
            if reversal_signal:
                reversal_signal["quality"] += self.low_vol_reversal_bonus
            if trend_signal:
                trend_signal["quality"] -= self.low_vol_trend_penalty

        return trend_signal, reversal_signal

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

    # ==========================================================
    # ФИЛЬТР REVERSAL
    # ==========================================================

    def _filter_reversal(self, s, regime_type, exhausted):
        if not s:
            return None
        if s["quality"] < self.min_reversal_quality:
            return None
        if regime_type not in self.reversal_allowed:
            return None
        if not exhausted:
            return None
        return s

    # ==========================================================
    # УМНЫЙ ВЫБОР TREND vs REVERSAL
    # ==========================================================

    def _smart_pick(self, trend_signal, reversal_signal, regime_type, htf_regime):
        qt = trend_signal.get("quality", 0.0)
        qr = reversal_signal.get("quality", 0.0)

        # 1) Если качество сильно различается → берём лучший
        if abs(qt - qr) >= 0.05:
            return trend_signal if qt > qr else reversal_signal

        # 2) Если качества близки → приоритет по HTF‑режиму
        if htf_regime in ("HTF_HIGH_VOL_TREND", "HTF_TREND"):
            return trend_signal
        if htf_regime in ("HTF_LOW_VOL_RANGE", "HTF_RANGE"):
            return reversal_signal

        # 3) fallback: приоритет по локальному режиму
        if regime_type in self.trend_allowed:
            return trend_signal
        if regime_type in self.reversal_allowed:
            return reversal_signal

        # 4) fallback: лучший по качеству
        if qt > qr:
            return trend_signal
        if qr > qt:
            return reversal_signal

        return None
