"""MySQL persistence for channels and overlays (SQLAlchemy Core).

Channels and overlays are stored as rows with a JSON payload carrying the full
Pydantic model, so the schema stays simple while the rich objects round-trip
intact. The in-memory store remains the hot path; this module mirrors writes and
loads everything back on startup. If the database is unreachable the app logs a
warning and continues in-memory (set ``DB_ENABLED=0`` to skip MySQL entirely).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from sqlalchemy import (Column, DateTime, MetaData, String, Table, Text,
                        create_engine, delete, func, insert, select, update)
from sqlalchemy.engine import Engine

from . import config

log = logging.getLogger("overlay.db")

metadata = MetaData()

channels = Table(
    "channels", metadata,
    Column("id", String(64), primary_key=True),
    Column("name", String(255)),
    Column("master_url", Text),
    Column("data", Text),                      # JSON: full Channel model
    Column("created_at", DateTime, server_default=func.now()),
)

overlays = Table(
    "overlays", metadata,
    Column("id", String(64), primary_key=True),
    Column("channel_id", String(64), index=True),
    Column("data", Text),                      # JSON: full OverlayEvent model
    Column("created_at", DateTime, server_default=func.now()),
)

_engine: Optional[Engine] = None


def init() -> bool:
    """Create the database (if missing) and tables. Returns True if the DB is
    usable, False if we should fall back to in-memory only."""
    global _engine
    if not config.DB_ENABLED:
        log.info("DB disabled (DB_ENABLED=0) — running in-memory only")
        return False
    try:
        # Ensure the database exists.
        server = create_engine(config.db_url(include_db=False), pool_pre_ping=True)
        with server.connect() as conn:
            conn.exec_driver_sql(
                f"CREATE DATABASE IF NOT EXISTS `{config.DB_NAME}` "
                "CHARACTER SET utf8mb4")
            conn.commit()
        server.dispose()
        _engine = create_engine(config.db_url(), pool_pre_ping=True, pool_recycle=280)
        metadata.create_all(_engine)
        log.info("MySQL ready at %s:%s/%s", config.DB_HOST, config.DB_PORT, config.DB_NAME)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("MySQL unavailable (%s) — falling back to in-memory store", exc)
        _engine = None
        return False


def available() -> bool:
    return _engine is not None


# --- channels --------------------------------------------------------------

def upsert_channel(channel) -> None:
    if _engine is None:
        return
    payload = channel.model_dump(mode="json")
    body = json.dumps(payload)
    try:
        with _engine.begin() as conn:
            exists = conn.execute(
                select(channels.c.id).where(channels.c.id == channel.id)).first()
            if exists:
                conn.execute(update(channels).where(channels.c.id == channel.id)
                             .values(name=channel.name, master_url=channel.master_url, data=body))
            else:
                conn.execute(insert(channels).values(
                    id=channel.id, name=channel.name,
                    master_url=channel.master_url, data=body))
    except Exception as exc:  # noqa: BLE001
        log.warning("upsert_channel failed: %s", exc)


def delete_channel(channel_id: str) -> None:
    if _engine is None:
        return
    try:
        with _engine.begin() as conn:
            conn.execute(delete(overlays).where(overlays.c.channel_id == channel_id))
            conn.execute(delete(channels).where(channels.c.id == channel_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("delete_channel failed: %s", exc)


def load_channels() -> list[dict]:
    if _engine is None:
        return []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(select(channels.c.data)).fetchall()
        return [json.loads(r[0]) for r in rows if r[0]]
    except Exception as exc:  # noqa: BLE001
        log.warning("load_channels failed: %s", exc)
        return []


# --- overlays --------------------------------------------------------------

def upsert_overlay(overlay) -> None:
    if _engine is None:
        return
    body = json.dumps(overlay.model_dump(mode="json"))
    try:
        with _engine.begin() as conn:
            exists = conn.execute(
                select(overlays.c.id).where(overlays.c.id == overlay.id)).first()
            if exists:
                conn.execute(update(overlays).where(overlays.c.id == overlay.id).values(data=body))
            else:
                conn.execute(insert(overlays).values(
                    id=overlay.id, channel_id=overlay.channel_id, data=body))
    except Exception as exc:  # noqa: BLE001
        log.warning("upsert_overlay failed: %s", exc)


def delete_overlay(overlay_id: str) -> None:
    if _engine is None:
        return
    try:
        with _engine.begin() as conn:
            conn.execute(delete(overlays).where(overlays.c.id == overlay_id))
    except Exception as exc:  # noqa: BLE001
        log.warning("delete_overlay failed: %s", exc)


def load_overlays() -> list[dict]:
    if _engine is None:
        return []
    try:
        with _engine.connect() as conn:
            rows = conn.execute(select(overlays.c.data)).fetchall()
        return [json.loads(r[0]) for r in rows if r[0]]
    except Exception as exc:  # noqa: BLE001
        log.warning("load_overlays failed: %s", exc)
        return []
