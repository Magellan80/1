import json
from collections import defaultdict, Counter
import numpy as np

LOG_FILE = "logs/signals_v31.jsonl"


# ============================================================
# LOAD LOGS
# ============================================================

def load_logs():
    events = []
    with open(LOG_FILE, "r", encoding="utf-8") as f:
        for line in f:
            try:
                events.append(json.loads(line))
            except:
                pass
    return events


# ============================================================
# MAIN ANALYSIS
# ============================================================

def analyze(events):

    trend_signals = []
    reversal_signals = []
    router_outputs = []
    trades = []

    # Разбор событий
    for e in events:
        eng = e.get("engine")

        if eng == "trend_engine":
            trend_signals.append(e)

        elif eng == "reversal_engine":
            reversal_signals.append(e)

        elif eng == "router_output":
            router_outputs.append(e)

        elif "pnl_net" in e:
            trades.append(e)

    print("\n====================================================")
    print("  ОБЩАЯ СТАТИСТИКА")
    print("====================================================")
    print(f"Trend signals:    {len(trend_signals)}")
    print(f"Reversal signals: {len(reversal_signals)}")
    print(f"Router outputs:   {len(router_outputs)}")
    print(f"Trades:           {len(trades)}")

    # ============================================================
    # 1. WINRATE ПО ДВИЖКАМ
    # ============================================================

    engine_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "be": 0, "pnl": [], "R": []})

    for t in trades:
        eng = t.get("engine", "unknown")
        pnl = t["pnl_net"]
        entry = t["entry"]
        exitp = t["exit"]
        direction = t["direction"]

        # R-multiple
        R = (exitp - entry) if direction == "long" else (entry - exitp)
        R /= abs(entry - t["sl_initial"]) if abs(entry - t["sl_initial"]) > 0 else 1

        if pnl > 0:
            engine_stats[eng]["wins"] += 1
        elif pnl == 0:
            engine_stats[eng]["be"] += 1
        else:
            engine_stats[eng]["losses"] += 1

        engine_stats[eng]["pnl"].append(pnl)
        engine_stats[eng]["R"].append(R)

    print("\n====================================================")
    print("  WINRATE ПО ДВИЖКАМ")
    print("====================================================")

    for eng, st in engine_stats.items():
        total = st["wins"] + st["losses"] + st["be"]
        if total == 0:
            continue

        winrate = st["wins"] / total
        avg_pnl = np.mean(st["pnl"])
        avg_R = np.mean(st["R"])
        med_R = np.median(st["R"])

        print(f"\nEngine: {eng}")
        print(f"  Trades: {total}")
        print(f"  Winrate: {winrate:.3f}")
        print(f"  Avg PnL: {avg_pnl:.3f}")
        print(f"  Avg R:   {avg_R:.3f}")
        print(f"  Med R:   {med_R:.3f}")
        print(f"  Wins: {st['wins']} | Losses: {st['losses']} | BE: {st['be']}")

    # ============================================================
    # 2. АНАЛИЗ КОНФЛИКТОВ TREND vs REVERSAL
    # ============================================================

    print("\n====================================================")
    print("  КОНФЛИКТЫ TREND vs REVERSAL")
    print("====================================================")

    conflict_stats = {"trend_right": 0, "reversal_right": 0, "total_conflicts": 0}

    for t in trades:
        trend_dir = t.get("trend_signal")
        rev_dir = t.get("reversal_signal")

        if not trend_dir or not rev_dir:
            continue

        if trend_dir != rev_dir:
            conflict_stats["total_conflicts"] += 1

            if t["pnl_net"] > 0:
                if t["direction"] == trend_dir:
                    conflict_stats["trend_right"] += 1
                else:
                    conflict_stats["reversal_right"] += 1

    print(conflict_stats)

    # ============================================================
    # 3. АНАЛИЗ ОШИБОК ROUTER
    # ============================================================

    print("\n====================================================")
    print("  ОШИБКИ ROUTER")
    print("====================================================")

    router_mistakes = {
        "picked_trend_but_reversal_would_win": 0,
        "picked_reversal_but_trend_would_win": 0,
    }

    for t in trades:
        trend_dir = t.get("trend_signal")
        rev_dir = t.get("reversal_signal")
        chosen = t.get("direction")

        if not trend_dir or not rev_dir:
            continue

        # Если выбрали trend, но reversal был бы прибыльным
        if chosen == trend_dir and t["pnl_net"] < 0 and rev_dir != trend_dir:
            router_mistakes["picked_trend_but_reversal_would_win"] += 1

        # Если выбрали reversal, но trend был бы прибыльным
        if chosen == rev_dir and t["pnl_net"] < 0 and rev_dir != trend_dir:
            router_mistakes["picked_reversal_but_trend_would_win"] += 1

    print(router_mistakes)

    # ============================================================
    # 4. ТОКСИЧНЫЕ РЕЖИМЫ
    # ============================================================

    print("\n====================================================")
    print("  ТОКСИЧНЫЕ РЕЖИМЫ")
    print("====================================================")

    regime_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "be": 0})

    for t in trades:
        regime = t.get("regime", "unknown")
        pnl = t["pnl_net"]

        if pnl > 0:
            regime_stats[regime]["wins"] += 1
        elif pnl == 0:
            regime_stats[regime]["be"] += 1
        else:
            regime_stats[regime]["losses"] += 1

    for regime, st in regime_stats.items():
        total = st["wins"] + st["losses"] + st["be"]
        if total == 0:
            continue
        loss_rate = st["losses"] / total
        print(f"{regime}: loss_rate={loss_rate:.3f}  (W:{st['wins']} L:{st['losses']} BE:{st['be']})")

    # ============================================================
    # 5. ТОКСИЧНЫЕ HTF РЕЖИМЫ
    # ============================================================

    print("\n====================================================")
    print("  ТОКСИЧНЫЕ HTF РЕЖИМЫ")
    print("====================================================")

    htf_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "be": 0})

    for t in trades:
        htf = t.get("htf_regime", "unknown")
        pnl = t["pnl_net"]

        if pnl > 0:
            htf_stats[htf]["wins"] += 1
        elif pnl == 0:
            htf_stats[htf]["be"] += 1
        else:
            htf_stats[htf]["losses"] += 1

    for htf, st in htf_stats.items():
        total = st["wins"] + st["losses"] + st["be"]
        if total == 0:
            continue
        loss_rate = st["losses"] / total
        print(f"{htf}: loss_rate={loss_rate:.3f}  (W:{st['wins']} L:{st['losses']} BE:{st['be']})")

    # ============================================================
    # 6. EXIT REASON ANALYSIS
    # ============================================================

    print("\n====================================================")
    print("  EXIT REASON ANALYSIS")
    print("====================================================")

    exit_stats = Counter([t.get("exit_reason", "unknown") for t in trades])
    print(exit_stats)

    # ============================================================
    # 7. ИТОГ: СИЛЬНЫЕ И СЛАБЫЕ СТОРОНЫ
    # ============================================================

    print("\n====================================================")
    print("  СИЛЬНЫЕ И СЛАБЫЕ СТОРОНЫ ДВИЖКОВ")
    print("====================================================")

    for eng, st in engine_stats.items():
        total = st["wins"] + st["losses"] + st["be"]
        if total == 0:
            continue

        winrate = st["wins"] / total
        lossrate = st["losses"] / total
        avg_R = np.mean(st["R"])

        print(f"\nEngine: {eng}")

        if winrate > 0.55:
            print("  ✔ Сильная сторона: высокая точность сигналов")

        if avg_R > 0.5:
            print("  ✔ Сильная сторона: хорошие импульсы")

        if lossrate > 0.45:
            print("  ✘ Слабая сторона: много стопов — плохие входы")

        if avg_R < 0.1:
            print("  ✘ Слабая сторона: слабый импульс — сигналы не тянут тренд")

        if st["be"] / total > 0.30:
            print("  ~ Много BE — возможно, слишком ранний BE или слабый импульс")


def main():
    events = load_logs()
    analyze(events)


if __name__ == "__main__":
    main()
