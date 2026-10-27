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
    """Idempotente: roda em todo boot, só altera o que ainda não existe —
    é onde `ALTER TABLE ... ADD COLUMN` entra quando uma coluna nova chega
    depois do release inicial deste serviço.

    `veiculo_id` → `veiculo_codigo_erp`/`veiculo_descricao`: a migração do
    veículo pro portal do Fusion (sem cadastro local) trocou essas colunas
    em `schema.sql`, mas `CREATE TABLE IF NOT EXISTS` é no-op numa tabela
    que já existe — um banco criado antes dessa mudança continuava com a
    coluna velha pra sempre, e todo INSERT/UPDATE que já fala
    `veiculo_codigo_erp` quebraria com "no such column". `veiculo_id`
    fica, sem uso — sqlite3 não tem `DROP COLUMN` barato, e não há dado
    nela que valha migrar (era só uma FK pra uma tabela que não existe
    mais).

    `modo`: coluna nova pro envio "solto" de pré-carga de pedido único
    (só `saveEntregaServico`, sem `saveCarga`) — banco existente nunca
    tinha essa coluna, mesmo caso do `veiculo_codigo_erp` acima."""
    if _tabela_existe(conn, "cargas_fusion_precarga_fila"):
        colunas = _colunas(conn, "cargas_fusion_precarga_fila")
        if "veiculo_codigo_erp" not in colunas:
            conn.execute("ALTER TABLE cargas_fusion_precarga_fila ADD COLUMN veiculo_codigo_erp TEXT")
        if "veiculo_descricao" not in colunas:
            conn.execute("ALTER TABLE cargas_fusion_precarga_fila ADD COLUMN veiculo_descricao TEXT")
        if "modo" not in colunas:
            conn.execute(
                "ALTER TABLE cargas_fusion_precarga_fila "
                "ADD COLUMN modo TEXT NOT NULL DEFAULT 'BUNDLE' CHECK (modo IN ('BUNDLE', 'SOLTA'))"
            )


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
