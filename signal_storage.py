import json
import os
from typing import List, Dict, Any


class SignalStorage:
    FILE = "signals.json"

    @staticmethod
    def load() -> List[Dict[str, Any]]:
        if not os.path.exists(SignalStorage.FILE):
            return []
        try:
            with open(SignalStorage.FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            print(f"[SignalStorage] load error: {e}")
            return []

    @staticmethod
    def save(signals: List[Dict[str, Any]]) -> None:
        try:
            with open(SignalStorage.FILE, "w", encoding="utf-8") as f:
                json.dump(signals, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"[SignalStorage] save error: {e}")

    @staticmethod
    def add(signal: Dict[str, Any]) -> None:
        signals = SignalStorage.load()
        signals.append(signal)
        SignalStorage.save(signals)

    @staticmethod
    def delete(index: int) -> None:
        signals = SignalStorage.load()
        if 0 <= index < len(signals):
            signals.pop(index)
            SignalStorage.save(signals)

    @staticmethod
    def clear() -> None:
        SignalStorage.save([])
