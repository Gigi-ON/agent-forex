"""
Cache local sur SQLite.

Pourquoi c'est indispensable : OANDA plafonne le nombre de bougies par
requête (~5000). Pour des années de M15, on pagine, puis on STOCKE, et on
réutilise. Sans cache, chaque backtest re-télécharge tout pour rien et
tape inutilement l'API.

Deux tables :
  - candles  : bougies OHLC (OANDA), clé (pair, granularity, time)
  - fx_daily : taux quotidiens médians (Frankfurter), clé (pair, date)
"""

import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).parent / "data" / "market.db"


class Cache:
    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS candles (
                pair TEXT, granularity TEXT, time TEXT,
                o REAL, h REAL, l REAL, c REAL,
                PRIMARY KEY (pair, granularity, time)
            );
            CREATE TABLE IF NOT EXISTS fx_daily (
                pair TEXT, date TEXT, rate REAL,
                PRIMARY KEY (pair, date)
            );
        """)
        self.conn.commit()

    # -- bougies (OANDA) -----------------------------------------------------
    def upsert_candles(self, pair, granularity, candles):
        rows = [(pair, granularity, c["time"], c["o"], c["h"], c["l"], c["c"])
                for c in candles]
        self.conn.executemany(
            "INSERT OR REPLACE INTO candles "
            "(pair, granularity, time, o, h, l, c) VALUES (?,?,?,?,?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_candles(self, pair, granularity, start=None, end=None):
        q = ("SELECT time, o, h, l, c FROM candles "
             "WHERE pair=? AND granularity=?")
        params = [pair, granularity]
        if start:
            q += " AND time >= ?"; params.append(start)
        if end:
            q += " AND time <= ?"; params.append(end)
        q += " ORDER BY time ASC"
        cur = self.conn.execute(q, params)
        return [dict(r) for r in cur.fetchall()]

    def last_candle_time(self, pair, granularity):
        cur = self.conn.execute(
            "SELECT MAX(time) AS t FROM candles WHERE pair=? AND granularity=?",
            (pair, granularity),
        )
        row = cur.fetchone()
        return row["t"] if row else None

    # -- taux quotidiens (Frankfurter) --------------------------------------
    def upsert_fx_daily(self, pair, series):
        """series : liste de (date, rate)."""
        rows = [(pair, d, r) for d, r in series]
        self.conn.executemany(
            "INSERT OR REPLACE INTO fx_daily (pair, date, rate) VALUES (?,?,?)",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def get_fx_daily(self, pair, start=None, end=None):
        q = "SELECT date, rate FROM fx_daily WHERE pair=?"
        params = [pair]
        if start:
            q += " AND date >= ?"; params.append(start)
        if end:
            q += " AND date <= ?"; params.append(end)
        q += " ORDER BY date ASC"
        cur = self.conn.execute(q, params)
        return [(r["date"], r["rate"]) for r in cur.fetchall()]

    def close(self):
        self.conn.close()
