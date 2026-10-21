"""Migração idempotente (`_migrar`) — roda em todo boot, só altera o que
ainda não existe. Testa especificamente a passagem de `veiculo_id` pra
`veiculo_codigo_erp`/`veiculo_descricao`: achado em produção, um banco
criado antes dessa mudança em schema.sql ficava com a coluna velha pra
sempre (`CREATE TABLE IF NOT EXISTS` é no-op numa tabela que já existe),
e todo envio de pré-carga quebrava com "no such column".
"""

from __future__ import annotations

import sqlite3

from database import _colunas, connect, init_database


def test_migrar_adiciona_veiculo_codigo_erp_num_banco_do_schema_antigo(tmp_path):
    db_path = tmp_path / "antigo.db"

    # Simula o schema de antes da migração do veículo — só a tabela que
    # importa pra este teste, com a coluna velha.
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE cargas_fusion_precarga_fila ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "id_empresa INTEGER NOT NULL, "
        "id_precarga INTEGER NOT NULL, "
        "codigo_erp TEXT NOT NULL, "
        "motorista_codigo_erp TEXT, "
        "motorista_nome TEXT, "
        "veiculo_id INTEGER, "
        "status TEXT NOT NULL DEFAULT 'PENDENTE', "
        "motivo TEXT, "
        "avisos TEXT NOT NULL DEFAULT '[]', "
        "tentativas INTEGER NOT NULL DEFAULT 0, "
        "enviado_em TEXT, "
        "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, "
        "UNIQUE (id_empresa, id_precarga))"
    )
    conn.execute(
        "INSERT INTO cargas_fusion_precarga_fila (id_empresa, id_precarga, codigo_erp, veiculo_id) "
        "VALUES (9, 796186, 'PC_9_796186', 1)"
    )
    conn.commit()
    conn.close()

    init_database(db_path)

    conn = connect(db_path)
    colunas = _colunas(conn, "cargas_fusion_precarga_fila")
    assert "veiculo_codigo_erp" in colunas
    assert "veiculo_descricao" in colunas

    # A linha que já existia sobrevive à migração, só com as colunas novas vazias.
    linha = conn.execute(
        "SELECT * FROM cargas_fusion_precarga_fila WHERE codigo_erp = 'PC_9_796186'"
    ).fetchone()
    assert linha["veiculo_id"] == 1
    assert linha["veiculo_codigo_erp"] is None
    conn.close()


def test_migrar_e_idempotente_rodando_duas_vezes(tmp_path):
    db_path = tmp_path / "novo.db"

    init_database(db_path)
    init_database(db_path)  # não pode levantar "duplicate column name"

    conn = connect(db_path)
    colunas = _colunas(conn, "cargas_fusion_precarga_fila")
    assert "veiculo_codigo_erp" in colunas
    conn.close()
