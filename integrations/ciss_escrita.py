"""Única fronteira de **escrita** deste serviço com o CISS DB2.

Até 2026-11 o CISS era somente leitura sem exceção. Passou a existir uma
exceção deliberada e estreita: as tabelas de rastreio que o próprio CISS
mantém para a Fusion (`DBA.*_FUSION_TRACK`), populadas a partir do
`getIntErp` (ver `cargas_fusion/rastreio.py`). Todo o resto do CISS
continua somente leitura — `integrations/ciss.py::CissClient` não ganhou
nenhum caminho de escrita, e nunca deve ganhar.

A conexão usa o mesmo usuário do `CissClient` (hoje `DBA`, decisão do
negócio), então a permissão do DB2 **não** protege nada aqui — a
allow-list de tabelas abaixo é a única defesa. Este módulo também não
aceita SQL de fora: cada método público tem o seu SQL fixo, e
`validate_escrita_permitida` ainda confere esse SQL antes de executar,
pra que um erro de edição futuro (tabela errada, `DELETE` num `MERGE`)
falhe alto em vez de escrever.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from typing import TYPE_CHECKING

from integrations.ciss import CissDbError, CissDbUnavailable, _limpar, _load_driver

if TYPE_CHECKING:
    from config import CissDbConfig

logger = logging.getLogger(__name__)

# Ampliar isto é decisão de negócio, não de código — ver CLAUDE.md
# ("Regras que não se negociam").
TABELAS_ESCRITA_PERMITIDAS = frozenset({"DBA.CARGAS_FUSION_TRACK", "DBA.ENTREGAS_FUSION_TRACK"})

_VERBOS_PROIBIDOS_ESCRITA = frozenset(
    {
        "DELETE",
        "TRUNCATE",
        "CREATE",
        "ALTER",
        "DROP",
        "GRANT",
        "REVOKE",
        "CALL",
        "EXEC",
        "EXECUTE",
    }
)

_ALVO_ESCRITA = re.compile(
    r"(?:MERGE\s+INTO|INSERT\s+INTO|UPDATE)\s+([A-Z_][A-Z_0-9]*\s*\.\s*[A-Z_][A-Z_0-9]*)\b"
)
_PALAVRA = re.compile(r"[A-Z_][A-Z_0-9]*")


class EscritaNaoPermitida(CissDbError):
    """SQL de escrita fora da allow-list. Erro de programação, nunca de usuário."""


def validate_escrita_permitida(sql: str) -> None:
    limpo = _limpar(sql).strip()
    if limpo.endswith(";"):
        limpo = limpo[:-1].rstrip()
    if ";" in limpo:
        raise EscritaNaoPermitida("Comandos empilhados não são aceitos.")

    maiusculo = limpo.upper()
    alvo = _ALVO_ESCRITA.match(maiusculo)
    if alvo is None:
        raise EscritaNaoPermitida("Só MERGE INTO / INSERT INTO / UPDATE são aceitos.")

    tabela = re.sub(r"\s+", "", alvo.group(1))
    if tabela not in TABELAS_ESCRITA_PERMITIDAS:
        raise EscritaNaoPermitida(f"Tabela fora da allow-list de escrita: {tabela}")

    encontrados = _VERBOS_PROIBIDOS_ESCRITA & set(_PALAVRA.findall(maiusculo))
    if encontrados:
        raise EscritaNaoPermitida(f"Comando rejeitado: {', '.join(sorted(encontrados))}")


# (coluna, tipo do CAST) — DB2 não aceita marcador `?` sem tipo dentro de
# `MERGE ... USING (VALUES ...)` (SQL0418N), por isso todo parâmetro vai
# com CAST explícito. Tipos/tamanhos lidos de SYSCAT.COLUMNS em produção.
COLUNAS_CARGAS_FUSION_TRACK: tuple[tuple[str, str], ...] = (
    ("IDCARGAFUSION", "INTEGER"),
    ("IDEMPRESA", "INTEGER"),
    ("DESTINO", "VARCHAR(45)"),
    ("STATUSROMANEIO", "INTEGER"),
    ("OBSERVACAO", "CLOB(1M)"),
    ("DATASAIDAPREVISTA", "TIMESTAMP"),
    ("IDMOTORISTA", "INTEGER"),
    ("PESO", "DECIMAL(10,3)"),
    ("CUSTO", "DECIMAL(10,3)"),
    ("CUSTOESTIMADO", "DECIMAL(10,3)"),
    ("VOLUME", "DECIMAL(10,3)"),
    ("VALORTOTAL", "DECIMAL(10,3)"),
    ("NUMEROENTREGAS", "INTEGER"),
    ("NUMEROCLIENTES", "INTEGER"),
    ("NUMEROCIDADES", "INTEGER"),
    ("CODIGOPRACAPRINCIPAL", "VARCHAR(45)"),
    ("IDVEICULO", "INTEGER"),
    ("CUSTOPEDAGIO", "DECIMAL(10,3)"),
    ("DATACANCELAMENTO", "TIMESTAMP"),
    ("DATAGRAVA", "TIMESTAMP"),
    ("DATAINICIO", "TIMESTAMP"),
    ("DATAPREVISTATERMINO", "TIMESTAMP"),
    ("DATATERMINO", "TIMESTAMP"),
    ("KMREALIZADO", "DECIMAL(18,7)"),
    ("KMPREVISTO", "DECIMAL(18,7)"),
    ("KMTOTAL", "DECIMAL(18,7)"),
)


def _sql_merge_cargas_fusion_track() -> str:
    nomes = [nome for nome, _ in COLUNAS_CARGAS_FUSION_TRACK]
    valores = ", ".join(f"CAST(? AS {tipo})" for _, tipo in COLUNAS_CARGAS_FUSION_TRACK)
    atualiza = ", ".join(f"T.{nome} = S.{nome}" for nome in nomes if nome != "IDCARGAFUSION")
    return (
        "MERGE INTO DBA.CARGAS_FUSION_TRACK T "
        f"USING (VALUES ({valores})) AS S ({', '.join(nomes)}) "
        "ON T.IDCARGAFUSION = S.IDCARGAFUSION "
        f"WHEN MATCHED THEN UPDATE SET {atualiza} "
        f"WHEN NOT MATCHED THEN INSERT ({', '.join(nomes)}) "
        f"VALUES ({', '.join('S.' + nome for nome in nomes)})"
    )


SQL_MERGE_CARGAS_FUSION_TRACK = _sql_merge_cargas_fusion_track()

# Chave do MERGE = índice único IUX_IDCARGAFUSION_IDPLANILHA (IDPLANILHA da
# NF + IDCARGAFUSION) — uma linha por NF por romaneio. `OBERSERVACAOENTREGA`
# é assim mesmo no DB2 (typo do schema, não corrigir aqui).
COLUNAS_ENTREGAS_FUSION_TRACK: tuple[tuple[str, str], ...] = (
    ("IDEMPRESA", "INTEGER"),
    ("IDPLANILHA", "INTEGER"),
    ("IDCARGAFUSION", "INTEGER"),
    ("NUMNOTA", "INTEGER"),
    ("STATUSENTREGA", "INTEGER"),
    ("DATAPREVISTAENTREGA", "TIMESTAMP"),
    ("DATAPREVISTAENTREGAATUALIZADA", "TIMESTAMP"),
    ("DATAREALENTREGA", "TIMESTAMP"),
    ("OBERSERVACAOENTREGA", "CLOB(1M)"),
    ("IDDEVOLUCAO", "INTEGER"),
    ("DESCRDEVOLUCAO", "VARCHAR(45)"),
    ("IDENTREGAFUSION", "INTEGER"),
)
_CHAVE_ENTREGAS = ("IDPLANILHA", "IDCARGAFUSION")


def _sql_merge_entregas_fusion_track() -> str:
    nomes = [nome for nome, _ in COLUNAS_ENTREGAS_FUSION_TRACK]
    valores = ", ".join(f"CAST(? AS {tipo})" for _, tipo in COLUNAS_ENTREGAS_FUSION_TRACK)
    atualiza = ", ".join(f"T.{nome} = S.{nome}" for nome in nomes if nome not in _CHAVE_ENTREGAS)
    return (
        "MERGE INTO DBA.ENTREGAS_FUSION_TRACK T "
        f"USING (VALUES ({valores})) AS S ({', '.join(nomes)}) "
        "ON T.IDPLANILHA = S.IDPLANILHA AND T.IDCARGAFUSION = S.IDCARGAFUSION "
        f"WHEN MATCHED THEN UPDATE SET {atualiza} "
        f"WHEN NOT MATCHED THEN INSERT ({', '.join(nomes)}) "
        f"VALUES ({', '.join('S.' + nome for nome in nomes)})"
    )


SQL_MERGE_ENTREGAS_FUSION_TRACK = _sql_merge_entregas_fusion_track()


class CissEscritaClient:
    """Uma conexão por escrita, com commit explícito (pyodbc abre em
    transação, `autocommit=False`) e rollback em qualquer falha."""

    def __init__(self, db: "CissDbConfig") -> None:
        self._db = db

    def upsert_carga_fusion_track(self, linha: dict[str, object]) -> None:
        """Upsert por `IDCARGAFUSION` (PK, = `t10_id` do Fusion). Toda
        coluna é sobrescrita no MATCHED — a pendência mais nova do Fusion
        é a fonte da verdade sobre o romaneio."""
        faltando = [nome for nome, _ in COLUNAS_CARGAS_FUSION_TRACK if nome not in linha]
        if faltando:
            raise ValueError(f"Linha sem as colunas: {', '.join(faltando)}")
        params = [linha[nome] for nome, _ in COLUNAS_CARGAS_FUSION_TRACK]
        self._executar(SQL_MERGE_CARGAS_FUSION_TRACK, params)

    def upsert_entrega_fusion_track(self, linha: dict[str, object]) -> None:
        """Upsert por `(IDPLANILHA, IDCARGAFUSION)` — uma linha por NF por
        romaneio; reenviar atualiza status/datas/observação."""
        faltando = [nome for nome, _ in COLUNAS_ENTREGAS_FUSION_TRACK if nome not in linha]
        if faltando:
            raise ValueError(f"Linha sem as colunas: {', '.join(faltando)}")
        params = [linha[nome] for nome, _ in COLUNAS_ENTREGAS_FUSION_TRACK]
        self._executar(SQL_MERGE_ENTREGAS_FUSION_TRACK, params)

    def _executar(self, sql: str, params: Sequence[object] = ()) -> None:
        validate_escrita_permitida(sql)

        driver = _load_driver()
        try:
            conexao = driver.connect(self._db.connection_string(), timeout=self._db.timeout)
        except driver.Error:
            logger.warning(
                "Conexão ODBC (escrita) com o CISS DB2 falhou (%s em %s)",
                self._db.database,
                self._db.host,
            )
            raise CissDbUnavailable from None

        cursor = None
        try:
            cursor = conexao.cursor()
            cursor.execute(sql, params)
            conexao.commit()
        except driver.Error as exc:
            try:
                conexao.rollback()
            except driver.Error:
                pass
            # A mensagem do driver DB2 (SQLSTATE/SQLCODE) não carrega
            # credencial — é o que permite diagnosticar um CAST/overflow.
            logger.warning(
                "Escrita no CISS DB2 falhou (%s em %s): %s",
                self._db.database,
                self._db.host,
                exc,
            )
            raise CissDbUnavailable(str(exc)) from None
        finally:
            if cursor is not None:
                cursor.close()
            conexao.close()
