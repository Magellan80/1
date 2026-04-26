import os
import sqlite3
import datetime
from typing import List, Dict, Any, Optional

DB_PATH = os.getenv("WEB_DB_PATH", "signals.db")


def _get_conn():
    return sqlite3.connect(DB_PATH)


def init_db():
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            signal_type TEXT NOT NULL,
            price REAL NOT NULL,
            quality INTEGER NOT NULL,
            htf_regime TEXT NOT NULL,
            funding REAL NOT NULL,
            ts TEXT NOT NULL,
            chart BLOB
        )
        """
    )
    conn.commit()
    conn.close()


def save_signal(data: Dict[str, Any]) -> int:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO signals (
            created_at, symbol, direction, signal_type,
            price, quality, htf_regime, funding, ts, chart
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            datetime.datetime.utcnow().isoformat(),
            data["symbol"],
            data["direction"],
            data["signal_type"],
            float(data["price"]),
            int(data["quality"]),
            data["htf_regime"],
            float(data["funding"]),
            data["ts"],
            data.get("chart_bytes"),
        ),
    )
    conn.commit()
    signal_id = cur.lastrowid
    conn.close()
    return signal_id


def load_signals(limit: int = 200) -> List[Dict[str, Any]]:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT
            id, symbol, direction, signal_type,
            price, quality, htf_regime, funding, ts,
            CASE WHEN chart IS NOT NULL THEN 1 ELSE 0 END AS has_chart
        FROM signals
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,),
    )
    rows = cur.fetchall()
    conn.close()

    result: List[Dict[str, Any]] = []
    for r in rows:
        result.append(
            {
                "id": r[0],
                "symbol": r[1],
                "direction": r[2],
                "signal_type": r[3],
                "price": r[4],
                "quality": r[5],
                "htf_regime": r[6],
                "funding": r[7],
                "ts": r[8],
                "has_chart": bool(r[9]),
            }
        )
    return result


def get_chart(signal_id: int) -> Optional[bytes]:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT chart FROM signals WHERE id = ?", (signal_id,))
    row = cur.fetchone()
    conn.close()
    if row and row[0] is not None:
        return row[0]
    return None


def delete_signal(signal_id: int) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM signals WHERE id = ?", (signal_id,))
    conn.commit()
    conn.close()


def clear_all() -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM signals")
    conn.commit()
    conn.close()


def cleanup_old(days: int = 7) -> None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        """
        DELETE FROM signals
        WHERE created_at < datetime('now', ?)
        """,
        (f"-{int(days)} days",),
    )
    conn.commit()
    conn.close()


# инициализация при импорте
init_db()
