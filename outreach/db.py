
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

JSON_COLS = {"recent_videos", "themes", "fail_reasons", "notes", "signals"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS influencers (
  channel_id TEXT PRIMARY KEY,
  name TEXT, platform TEXT DEFAULT 'YouTube', profile_url TEXT, handle TEXT,
  followers INTEGER, total_views INTEGER, video_count INTEGER, country TEXT,
  description TEXT, keywords TEXT, uploads_playlist TEXT, discovered_via TEXT,
  fetched_at TEXT, recent_videos TEXT, engagement_rate REAL, engagement_videos INTEGER,
  last_upload_days INTEGER, primary_niche TEXT, niche_score INTEGER, themes TEXT,
  status TEXT, fail_reasons TEXT, notes TEXT,
  email TEXT, email_source TEXT, instagram_url TEXT, instagram_handle TEXT,
  tiktok_url TEXT, website TEXT, audience_age TEXT, audience_gender TEXT,
  audience_geography TEXT, enriched_at TEXT
);
CREATE TABLE IF NOT EXISTS messages (
  channel_id TEXT PRIMARY KEY,
  subject TEXT, email_body TEXT, instagram_dm TEXT, angle TEXT, source TEXT,
  email_words INTEGER, dm_words INTEGER, status TEXT, generated_at TEXT, signals TEXT
);
CREATE TABLE IF NOT EXISTS outreach_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  channel_id TEXT, influencer TEXT, email TEXT, channel TEXT,
  message_generated INTEGER, sent INTEGER, mode TEXT, status TEXT, detail TEXT, ts TEXT
);
-- Hard guarantee against duplicate real sends, even if app logic has a bug.
CREATE UNIQUE INDEX IF NOT EXISTS uq_real_email_send
  ON outreach_log(email) WHERE channel='email' AND status='sent';
"""


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    @staticmethod
    def _encode(v):
        return json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v

    @staticmethod
    def _decode(row) -> dict:
        d = dict(row)
        for c in JSON_COLS & d.keys():
            if isinstance(d[c], str):
                try:
                    d[c] = json.loads(d[c])
                except json.JSONDecodeError:
                    d[c] = None
        return d

    def upsert(self, table: str, row: dict, pk: str) -> None:
        cols = list(row)
        updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != pk)
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))}) "
               f"ON CONFLICT({pk}) DO UPDATE SET {updates}" if updates else
               f"INSERT OR IGNORE INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})")
        self.conn.execute(sql, [self._encode(row[c]) for c in cols])
        self.conn.commit()

    def get(self, table: str, pk: str, value) -> dict | None:
        r = self.conn.execute(f"SELECT * FROM {table} WHERE {pk}=?", (value,)).fetchone()
        return self._decode(r) if r else None

    def all(self, table: str, where: str = "", params: tuple = (), order: str = "") -> list[dict]:
        sql = f"SELECT * FROM {table}" + (f" WHERE {where}" if where else "") + (f" ORDER BY {order}" if order else "")
        return [self._decode(r) for r in self.conn.execute(sql, params).fetchall()]

    def count(self, table: str, where: str = "", params: tuple = ()) -> int:
        sql = f"SELECT COUNT(*) FROM {table}" + (f" WHERE {where}" if where else "")
        return self.conn.execute(sql, params).fetchone()[0]

    def exists(self, table: str, pk: str, value) -> bool:
        return self.conn.execute(f"SELECT 1 FROM {table} WHERE {pk}=?", (value,)).fetchone() is not None

    
    def log_outreach(self, **kw) -> None:
        kw.setdefault("ts", now_iso())
        cols = list(kw)
        self.conn.execute(
            f"INSERT INTO outreach_log ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [kw[c] for c in cols])
        self.conn.commit()

    def already_contacted(self, email: str, channel_id: str, blocking: tuple[str, ...]) -> bool:
        q = ",".join("?" * len(blocking))
        r = self.conn.execute(
            f"SELECT 1 FROM outreach_log WHERE channel='email' AND status IN ({q}) "
            f"AND (lower(email)=lower(?) OR channel_id=?) LIMIT 1",
            (*blocking, email, channel_id)).fetchone()
        return r is not None

    def latest_log(self, channel_id: str, channel: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM outreach_log WHERE channel_id=? AND channel=? ORDER BY id DESC LIMIT 1",
            (channel_id, channel)).fetchone()
        return dict(r) if r else None
