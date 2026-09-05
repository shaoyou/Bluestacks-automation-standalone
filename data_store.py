#!/usr/bin/env python3
"""Small SQLite bridge for the desktop app's durable history store."""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


def configure_console_encoding() -> None:
    if sys.platform != "win32":
        return
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


configure_console_encoding()

def init(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS users (
          id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chest_records (
          record_key TEXT PRIMARY KEY, user_id TEXT NOT NULL, captured_at TEXT,
          device TEXT, source_id TEXT, source_name TEXT, screenshot_path TEXT,
          payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS draw_sessions (
          session_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, updated_at TEXT,
          payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS draw_events (
          event_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT NOT NULL,
          timestamp TEXT, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS draw_pairs (
          pair_key TEXT PRIMARY KEY, session_id TEXT NOT NULL, user_id TEXT NOT NULL,
          saved_at TEXT, payload TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS metadata (
          key TEXT PRIMARY KEY, value TEXT NOT NULL
        );
        """
    )


def payload(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    request = json.load(sys.stdin)
    database = Path(str(request["database"]))
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    init(connection)
    operation = request.get("operation")
    if operation != "import":
        raise ValueError("Unsupported operation")

    data = request.get("data", {})
    users = data.get("users", [])
    chest = data.get("chestRecords", [])
    sessions = data.get("drawSessions", [])
    events = data.get("drawEvents", [])
    pairs = data.get("drawPairs", [])
    with connection:
        for user in users:
            connection.execute(
                "INSERT INTO users(id,name,created_at) VALUES(?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET name=excluded.name, created_at=excluded.created_at",
                (str(user.get("id", "default")), str(user.get("name", "默认用户")), str(user.get("createdAt", ""))),
            )
        for record in chest:
            key = str(record.get("event_id") or record.get("screenshot_path") or "")
            if not key:
                continue
            connection.execute(
                "INSERT INTO chest_records(record_key,user_id,captured_at,device,source_id,source_name,screenshot_path,payload) VALUES(?,?,?,?,?,?,?,?) "
                "ON CONFLICT(record_key) DO UPDATE SET user_id=excluded.user_id,captured_at=excluded.captured_at,device=excluded.device,source_id=excluded.source_id,source_name=excluded.source_name,screenshot_path=excluded.screenshot_path,payload=excluded.payload",
                (key, str(record.get("user_id", "default")), str(record.get("captured_at", "")), str(record.get("device", "")), str(record.get("source_id", "")), str(record.get("source_name", "")), str(record.get("screenshot_path", "")), payload(record)),
            )
        for session in sessions:
            session_id = str(session.get("session_id", ""))
            if not session_id:
                continue
            connection.execute(
                "INSERT INTO draw_sessions(session_id,user_id,updated_at,payload) VALUES(?,?,?,?) "
                "ON CONFLICT(session_id) DO UPDATE SET user_id=excluded.user_id,updated_at=excluded.updated_at,payload=excluded.payload",
                (session_id, str(session.get("user_id", "default")), str(session.get("updated_at", "")), payload(session)),
            )
        for index, event in enumerate(events):
            key = str(event.get("event_id") or f"{event.get('session_id','')}/{event.get('timestamp','')}/{event.get('event','')}/{index}")
            connection.execute(
                "INSERT INTO draw_events(event_key,session_id,user_id,timestamp,payload) VALUES(?,?,?,?,?) "
                "ON CONFLICT(event_key) DO UPDATE SET payload=excluded.payload",
                (key, str(event.get("session_id", "")), str(event.get("user_id", "default")), str(event.get("timestamp", "")), payload(event)),
            )
        for pair in pairs:
            key = str(pair.get("pair_prefix") or "")
            if not key:
                continue
            connection.execute(
                "INSERT INTO draw_pairs(pair_key,session_id,user_id,saved_at,payload) VALUES(?,?,?,?,?) "
                "ON CONFLICT(pair_key) DO UPDATE SET user_id=excluded.user_id,saved_at=excluded.saved_at,payload=excluded.payload",
                (key, str(pair.get("session_id", "")), str(pair.get("user_id", "default")), str(pair.get("after_saved_at") or pair.get("before_saved_at") or ""), payload(pair)),
            )
        connection.execute(
            "INSERT INTO metadata(key,value) VALUES('last_import',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (payload({"chest": len(chest), "drawSessions": len(sessions), "drawEvents": len(events), "drawPairs": len(pairs)}),),
        )
    print(json.dumps({"database": str(database), "users": len(users), "chest": len(chest), "drawSessions": len(sessions), "drawEvents": len(events), "drawPairs": len(pairs)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
