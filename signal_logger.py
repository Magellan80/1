import json
import os
from datetime import datetime

LOG_PATH = "logs/signals_v31.jsonl"

os.makedirs("logs", exist_ok=True)

def _write(data: dict):
    data["timestamp"] = datetime.utcnow().isoformat()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(data, ensure_ascii=False) + "\n")


def log_signal(engine: str, data: dict):
    """
    engine: 'trend_engine', 'reversal_engine', 'router_output'
    data:   любые поля сигнала
    """
    data["engine"] = engine
    _write(data)


def log_trade_result(data: dict):
    """
    data: результат сделки
    """
    data["engine"] = "trade_result"
    _write(data)
