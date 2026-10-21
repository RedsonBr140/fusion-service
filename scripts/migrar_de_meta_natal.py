"""Migração única das 5 tabelas de negócio do SQLite do meta-natal para o
SQLite deste serviço. Roda uma vez no corte; `INSERT OR IGNORE` faz
reexecução segura (idempotente), sem duplicar linhas.

Não migra `usuarios_locais` — é allow-list mantida manualmente pela TI,
não um recorte de `user_tools` do meta-natal (sincronizar recriaria a
dependência que este serviço existe para remover).

Uso:
    python scripts/migrar_de_meta_natal.py <sqlite_meta_natal> <sqlite_fusion_service>
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import init_database  # noqa: E402

TABELAS = (
    "fusion_veiculos",
    "fusion_ajudantes",
    "fusion_clientes_sincronizados",
    "cargas_fusion_fila",
    "cargas_fusion_precarga_fila",
)


def _migrar_tabela(origem: sqlite3.Connection, destino: sqlite3.Connection, tabela: str) -> int:
    linhas = origem.execute(f"SELECT * FROM {tabela}").fetchall()
    if not linhas:
        return 0

    colunas = linhas[0].keys()
    marcadores = ", ".join("?" for _ in colunas)
    nomes_colunas = ", ".join(colunas)

    with destino:
        for linha in linhas:
            destino.execute(
                f"INSERT OR IGNORE INTO {tabela} ({nomes_colunas}) VALUES ({marcadores})",
                tuple(linha[coluna] for coluna in colunas),
            )
    return len(linhas)


def migrar(caminho_origem: str, caminho_destino: str) -> None:
    init_database(caminho_destino)

    origem = sqlite3.connect(caminho_origem)
    origem.row_factory = sqlite3.Row
    destino = sqlite3.connect(caminho_destino)
    destino.row_factory = sqlite3.Row

    try:
        for tabela in TABELAS:
            total = _migrar_tabela(origem, destino, tabela)
            print(f"{tabela}: {total} linha(s) migrada(s)")
    finally:
        origem.close()
        destino.close()


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    migrar(sys.argv[1], sys.argv[2])
