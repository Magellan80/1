# run_v30_test.py
# V31 Institutional Backtest Runner — РЕАЛИСТИЧНАЯ ВЕРСИЯ
#
# Изменения:
# [1] Поддержка нескольких символов через SYMBOLS список
# [2] Вывод подробного отчёта включая издержки
# [3] Правильный DATA_DIR для каждого символа
# [4] Параметры бэктест-движка вынесены в CONFIG-блок

import os
import json
import sys

# ==========================================================
# PATH FIX
# ==========================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # Backtest/
BOT_DIR  = os.path.dirname(BASE_DIR)                   # Bot/
sys.path.append(BOT_DIR)

# ==========================================================
# IMPORTS
# ==========================================================

from v30_backtest_engine import V30BacktestEngine

from elite_structure_engine import EliteStructureEngine
from elite_regime_engine    import EliteRegimeEngine
from elite_htf_sync         import EliteHTFSync
from elite_trend_engine     import EliteTrendEngine
from elite_signal_router    import EliteSignalRouter
from elite_exit_engine      import EliteExitEngine
from risk_engine_v31        import RiskEngineV31

from historical_downloader  import BybitDownloader, ensure_dir


# ==========================================================
# КОНФИГУРАЦИЯ
# ==========================================================

SYMBOL   = "SOLUSDT"   # ← меняй символ здесь
EXCHANGE = "bybit"

# Лимиты свечей (подобраны под ≈70 дней истории)
CANDLE_LIMITS = {
    "5m":  105000,
    "15m":  35000,
    "1h":   9000,
    "4h":    2200,
}

INITIAL_BALANCE = 10000

# Параметры реалистичности (передаются в движок)
BACKTEST_CONFIG = {
    "taker_fee":             0.00055,  # 0.055% Bybit taker
    "slippage_entry":        0.0002,   # 0.02%
    "slippage_exit":         0.00015,  # 0.015%
    "funding_rate":          0.0001,   # +0.01% каждые 8h (лонги платят)
    "funding_interval_bars": 96,       # 8h / 5min
    # умные фильтры можно переопределить при желании:
    # "min_signal_quality": 0.55,
    # "min_htf_alignment": -0.05,
    # "min_bar_gap": 2,
}


# ==========================================================
# DATA LOADING
# ==========================================================

def get_data_dir(symbol: str) -> str:
    return os.path.join(BASE_DIR, "data", EXCHANGE, symbol)


def load_json(path: str):
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_or_download(symbol: str, tf: str, needed: int):
    data_dir  = get_data_dir(symbol)
    ensure_dir(data_dir)

    json_path = os.path.join(data_dir, f"{tf}.json")

    data = load_json(json_path)
    if data and len(data) >= needed:
        print(f"[OK] Loaded {len(data)} {tf} candles from local file.")
        return data

    print(f"[DL] Downloading {needed} candles for {symbol} {tf} from Bybit...")
    downloader = BybitDownloader()
    candles    = downloader.download(symbol, tf, needed)

    if candles:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(candles, f, ensure_ascii=False, indent=2)
        print(f"[SAVE] {tf} → {json_path}")

    return candles


# ==========================================================
# PRINT REPORT
# ==========================================================

def print_report(result: dict, symbol: str):
    stats = result["stats"]
    costs = result.get("cost_summary", {})

    print("\n" + "=" * 50)
    print(f"  V31 BACKTEST RESULTS — {symbol}")
    print("=" * 50)

    print(f"\n📊 ТОРГОВЛЯ")
    print(f"  Сделок:              {stats['trades']}")
    print(f"  Winrate:             {round(stats['winrate']*100, 2)}%")
    print(f"  Avg win:             ${stats['avg_win']}")
    print(f"  Avg loss:            ${stats['avg_loss']}")
    print(f"  Медиана PnL:         ${stats['median_pnl']}")
    print(f"  Expectancy:          ${stats['expectancy']}")
    print(f"  Profit Factor:       {stats['profit_factor']}")
    print(f"  Max consec. losses:  {stats['max_consecutive_losses']}")

    print(f"\n📈 РЕЗУЛЬТАТ")
    print(f"  Начальный баланс:    ${INITIAL_BALANCE:,.2f}")
    print(f"  Итоговый баланс:     ${stats['final_balance']:,.2f}")
    print(f"  Доходность:          {stats['return_pct']}%")
    print(f"  Max Drawdown:        {stats['max_drawdown_pct']}%")
    print(f"  Recovery Factor:     {stats['recovery_factor']}")

    print(f"\n📐 РИСК-МЕТРИКИ")
    print(f"  Sharpe Ratio:        {stats['sharpe_ratio']}")
    print(f"  Sortino Ratio:       {stats['sortino_ratio']}")

    print(f"\n💸 ИЗДЕРЖКИ")
    print(f"  Комиссии:            ${costs.get('total_fees_paid', 0):,.2f}")
    print(f"  Funding:             ${costs.get('total_funding_paid', 0):,.2f}")
    print(f"  Всего издержек:      ${costs.get('total_costs', 0):,.2f}")
    print(
        f"  Доля от прибыли:     "
        f"{round(costs.get('total_costs',0) / max(stats['final_balance'] - INITIAL_BALANCE, 1) * 100, 1)}%"
    )

    print("\n" + "=" * 50)


# ==========================================================
# MAIN
# ==========================================================

def main():
    print(f"\n{'='*50}")
    print(f"  Symbol: {SYMBOL}")
    print(f"  Initial balance: ${INITIAL_BALANCE:,}")
    print(f"{'='*50}\n")

    print("Loading data...\n")
    candles_5m = load_or_download(SYMBOL, "5m",  CANDLE_LIMITS["5m"])
    htf_15m    = load_or_download(SYMBOL, "15m", CANDLE_LIMITS["15m"])
    htf_1h     = load_or_download(SYMBOL, "1h",  CANDLE_LIMITS["1h"])
    htf_4h     = load_or_download(SYMBOL, "4h",  CANDLE_LIMITS["4h"])

    if not all([candles_5m, htf_15m, htf_1h, htf_4h]):
        print("ERROR: не удалось загрузить данные.")
        return

    print(
        f"\nLoaded: 5m={len(candles_5m)}, 15m={len(htf_15m)}, "
        f"1h={len(htf_1h)}, 4h={len(htf_4h)}\n"
    )

    # Инициализация движков
    structure_engine = EliteStructureEngine()
    regime_engine    = EliteRegimeEngine()
    htf_sync         = EliteHTFSync()
    trend_engine     = EliteTrendEngine()
    router           = EliteSignalRouter()
    exit_engine      = EliteExitEngine()

    risk_engine = RiskEngineV31(
        base_risk=0.008,
        max_portfolio_heat=0.25,
    )

    # Бэктест-движок с реалистичными параметрами
    engine = V30BacktestEngine(symbol=SYMBOL, **BACKTEST_CONFIG)

    print("Running V31 Backtest (realistic mode)...\n")

    result = engine.run(
        candles_5m       = candles_5m,
        structure_engine = structure_engine,
        regime_engine    = regime_engine,
        htf_sync         = htf_sync,
        trend_engine     = trend_engine,
        router           = router,
        exit_engine      = exit_engine,
        htf_15m          = htf_15m,
        htf_1h           = htf_1h,
        htf_4h           = htf_4h,
        risk_engine      = risk_engine,
        initial_balance  = INITIAL_BALANCE,
    )

    print_report(result, SYMBOL)

    out_path = os.path.join(BASE_DIR, f"report_{SYMBOL}_v31_realistic.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"\nFull report saved to: {out_path}\n")


if __name__ == "__main__":
    main()
