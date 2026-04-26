# risk_engine_v31.py
# V31 — Institutional Adaptive Risk Allocator (fixed for realistic fees & sizing)

from collections import deque


class RiskEngineV31:
    """
    Адаптивный риск-движок институционального уровня.

    Цели:
    - Адаптация риска к просадке (dd_soft / dd_hard)
    - Учёт performance по последним сделкам (Profit Factor)
    - Учёт рыночного режима
    - Контроль портфельного тепла и плеча
    - Учёт комиссий и проскальзывания в расчёте размера позиции
    """

    def __init__(
        self,
        base_risk:           float = 0.008,   # 0.8% per trade
        max_portfolio_heat:  float = 0.20,    # 20% total risk exposure
        max_positions:       int   = 4,
        max_leverage:        float = 3.0,     # более консервативный hard leverage cap
        min_stop_pct:        float = 0.005,   # 0.5% минимальная дистанция стопа
        atr_stop_floor_mult: float = 1.0,     # стоп >= 1 * ATR
        dd_soft:             float = 0.08,    # мягкая просадка → снижаем риск
        dd_hard:             float = 0.12,    # жёсткая просадка → стоп торговли
        feedback_window:     int   = 20,      # окно для PF-оценки
        fee_roundtrip:       float = 0.0015,  # ~0.15% roundtrip (taker + slippage)
    ):
        self.base_risk           = base_risk
        self.max_portfolio_heat  = max_portfolio_heat
        self.max_positions       = max_positions
        self.max_leverage        = max_leverage

        self.min_stop_pct        = min_stop_pct
        self.atr_stop_floor_mult = atr_stop_floor_mult

        self.dd_soft = dd_soft
        self.dd_hard = dd_hard

        # Performance tracking
        self.trades_R        = deque(maxlen=feedback_window)
        self.feedback_window = feedback_window

        # Portfolio state
        self.current_heat   = 0.0
        self.open_positions = 0

        # Equity tracking
        self.peak_equity = None

        # Комиссии и проскальзывание (в долях от цены за полный цикл вход+выход)
        self.fee_roundtrip = fee_roundtrip

    # ==========================================================
    # EQUITY CONTROL
    # ==========================================================

    def update_equity(self, equity: float):
        """
        Вызывается каждый бар из бэктест-движка.
        Обновляет peak для корректного расчёта DD.
        """
        if self.peak_equity is None or equity > self.peak_equity:
            self.peak_equity = equity

    def current_dd(self, equity: float) -> float:
        if not self.peak_equity or self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - equity) / self.peak_equity)

    # ==========================================================
    # PERFORMANCE FEEDBACK
    # ==========================================================

    def register_trade_R(self, result_R: float):
        self.trades_R.append(result_R)

    def performance_multiplier(self) -> float:
        """
        Мягкая адаптация риска по Profit Factor последних сделок.
        Минимальное окно: feedback_window // 4, но не меньше 5.
        """
        min_trades = max(5, self.feedback_window // 4)
        if len(self.trades_R) < min_trades:
            return 1.0

        wins   = [r for r in self.trades_R if r > 0]
        losses = [r for r in self.trades_R if r < 0]

        if not losses:
            return 1.10   # только выигрыши → чуть увеличиваем

        pf = abs(sum(wins) / sum(losses))

        if pf < 0.8:
            return 0.60   # плохой PF → заметно снижаем
        elif pf < 1.0:
            return 0.80
        elif pf > 2.0:
            return 1.15   # отличный PF → увеличиваем
        elif pf > 1.5:
            return 1.10
        else:
            return 1.0    # нормальный PF

    # ==========================================================
    # REGIME ADJUSTMENT
    # ==========================================================

    def regime_multiplier(self, regime: str) -> float:
        if not isinstance(regime, str):
            return 1.0

        r = regime.upper()

        multipliers = {
            "STRONG_TREND":  1.20,
            "EARLY_TREND":   1.10,
            "EXPANSION":     1.05,
            "RANGE":         0.85,
            "LOW_VOL_RANGE": 0.80,
            "COMPRESSION":   0.90,
            "EXHAUSTION":    0.75,
            "CHAOS":         0.0,   # не торгуем в хаосе
        }

        return multipliers.get(r, 1.0)

    # ==========================================================
    # ИТОГОВЫЙ RISK %
    # ==========================================================

    def compute_risk_pct(self, equity: float, regime: str) -> float:
        self.update_equity(equity)
        dd = self.current_dd(equity)

        # Жёсткий стоп
        if dd >= self.dd_hard:
            return 0.0

        risk = self.base_risk

        # Мягкое снижение при soft-DD
        if dd >= self.dd_soft:
            # Плавное снижение: от 100% при dd_soft до 40% при dd_hard
            factor = 1.0 - (dd - self.dd_soft) / (self.dd_hard - self.dd_soft) * 0.6
            risk  *= max(factor, 0.4)

        risk *= self.performance_multiplier()
        risk *= self.regime_multiplier(regime)

        # Hard cap: максимум 1.5% на сделку
        return min(max(risk, 0.0), 0.015)

    # ==========================================================
    # POSITION SIZING
    # ==========================================================

    def allocate(
        self,
        equity:      float,
        entry_price: float,
        stop_price:  float,
        regime:      str,
        atr:         float = None,
    ):
        """
        Возвращает (size, risk_pct).
        size = 0 означает «не открывать позицию».
        """
        risk_pct = self.compute_risk_pct(equity, regime)

        if risk_pct <= 0:
            return 0.0, 0.0

        # Лимит открытых позиций
        if self.open_positions >= self.max_positions:
            return 0.0, 0.0

        # Лимит портфельного тепла
        if self.current_heat + risk_pct > self.max_portfolio_heat:
            return 0.0, 0.0

        # Базовый риск на единицу (только стоп)
        risk_per_unit = abs(entry_price - stop_price)

        # Минимальная дистанция стопа в процентах от цены
        min_stop_distance = entry_price * self.min_stop_pct
        if risk_per_unit < min_stop_distance:
            # Стоп слишком близко → не торгуем, чтобы не умереть от комиссий
            return 0.0, 0.0

        # ATR floor
        if atr is not None and atr > 0:
            atr_floor = atr * self.atr_stop_floor_mult
            if risk_per_unit < atr_floor:
                return 0.0, 0.0

        if risk_per_unit <= 0:
            return 0.0, 0.0

        # Учитываем комиссии и проскальзывание:
        # эффективный риск на единицу = стоп + roundtrip fee в цене
        fee_per_unit = entry_price * self.fee_roundtrip
        effective_risk_per_unit = risk_per_unit + fee_per_unit

        if effective_risk_per_unit <= 0:
            return 0.0, 0.0

        # Размер позиции по эффективному риску
        risk_amount = equity * risk_pct
        size        = risk_amount / effective_risk_per_unit

        # Leverage control (жёсткий лимит по нотации)
        position_value     = size * entry_price
        max_position_value = equity * self.max_leverage

        if position_value > max_position_value:
            size           = max_position_value / entry_price
            position_value = size * entry_price

        if size <= 0:
            return 0.0, 0.0

        # Обновляем состояние портфеля
        self.current_heat   = max(0.0, self.current_heat + risk_pct)
        self.open_positions = max(0, self.open_positions + 1)

        return size, risk_pct

    # ==========================================================
    # ЗАКРЫТИЕ ПОЗИЦИИ
    # ==========================================================

    def close_position(
        self,
        risk_pct:   float,
        entry:      float,
        stop:       float,
        exit_price: float,
        direction:  str,
    ):
        risk_per_unit = abs(entry - stop)

        if risk_per_unit == 0:
            result_R = 0.0
        else:
            d = (direction or "").lower()
            if d == "long":
                result_R = (exit_price - entry) / risk_per_unit
            else:
                result_R = (entry - exit_price) / risk_per_unit

        self.register_trade_R(result_R)

        self.current_heat   = max(0.0, self.current_heat - risk_pct)
        self.open_positions = max(0, self.open_positions - 1)
