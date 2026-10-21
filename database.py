"""Acesso ao SQLite do fusion-service. sqlite3 nativo, sem ORM."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from flask import Flask, current_app, g

SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def connect(path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _colunas(conn: sqlite3.Connection, tabela: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({tabela})")}


def _tabela_existe(conn: sqlite3.Connection, tabela: str) -> bool:
    achado = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (tabela,)
    ).fetchone()
    return achado is not None


def _migrar(conn: sqlite3.Connection) -> None:
    """Idempotente: roda em todo boot, só altera o que ainda não existe.
    Vazio hoje — é onde `ALTER TABLE ... ADD COLUMN` entra quando uma coluna
    nova chegar depois do release inicial deste serviço."""


def init_database(path: str | Path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            _migrar(conn)
            conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["APP_CONFIG"].database_path)
    return g.db


def close_db(exception: BaseException | None = None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_app(app: Flask) -> None:
    app.teardown_appcontext(close_db)
    init_database(app.config["APP_CONFIG"].database_path)
