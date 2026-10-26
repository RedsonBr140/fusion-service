"""Testes da cópia reduzida de integrations/ciss.py — porte de
meta-natal/backend/tests/test_ciss_db.py, focado no guard de leitura
(`validate_readonly_query`) e nos tijolos privados (`_fetch_all`/
`_fetch_one`/`_connect`), mais um representante dos métodos de domínio
ported (`get_empresas`, `get_cargas`, `get_cliente_fusion`) — não os 12
inteiros, só o suficiente pra provar que o porte preservou o padrão.
"""

import logging

import pytest

from config import CissDbConfig
from integrations import ciss
from integrations.ciss import (
    CissClient,
    CissDbUnavailable,
    ReadOnlyViolation,
    validate_readonly_query,
)

VERBOS_PROIBIDOS = [
    "INSERT INTO t VALUES (1)",
    "UPDATE t SET x = 1",
    "DELETE FROM t",
    "MERGE INTO t USING s ON (1=1)",
    "TRUNCATE TABLE t",
    "CREATE TABLE t (x INT)",
    "ALTER TABLE t ADD x INT",
    "DROP TABLE t",
    "GRANT SELECT ON t TO u",
    "REVOKE SELECT ON t FROM u",
    "CALL MINHA_PROCEDURE()",
    "EXEC MINHA_PROCEDURE",
    "EXECUTE IMMEDIATE 'x'",
    "SET CURRENT SCHEMA = X",
]


def test_aceita_select_simples():
    validate_readonly_query("SELECT 1 FROM SYSIBM.SYSDUMMY1")


def test_aceita_cte_com_with():
    validate_readonly_query("WITH x AS (SELECT 1 FROM t) SELECT * FROM x")


def test_aceita_ponto_e_virgula_no_fim():
    validate_readonly_query("SELECT * FROM PRODUTO_GRADE WHERE IDSUBPRODUTO = ?;")


@pytest.mark.parametrize("sql", VERBOS_PROIBIDOS)
def test_rejeita_verbo_de_escrita(sql):
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_query(sql)


def test_rejeita_verbo_escondido_dentro_de_um_select():
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_query("SELECT * FROM t WHERE x = (DELETE FROM outra)")


def test_rejeita_comandos_empilhados():
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_query("SELECT 1 FROM SYSIBM.SYSDUMMY1; DROP TABLE t")


def test_rejeita_o_que_nao_comeca_com_select_ou_with():
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_query("   ")


def test_aceita_literal_que_parece_comando():
    validate_readonly_query("SELECT * FROM t WHERE nome = 'DROP TABLE x'")


def test_aceita_coluna_cujo_nome_contem_verbo_proibido():
    validate_readonly_query("SELECT UPDATED_AT, CREATED_BY FROM t")


def test_rejeita_comando_escondido_depois_de_comentario_de_linha():
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_query("-- comentário\nDROP TABLE t")


def test_rejeita_comando_escondido_em_comentario_de_bloco():
    with pytest.raises(ReadOnlyViolation):
        validate_readonly_query("/* nada aqui */ DROP TABLE t")


def test_comentario_nao_transforma_select_valido_em_violacao():
    validate_readonly_query("SELECT 1 FROM SYSIBM.SYSDUMMY1 -- pode apagar depois")


def test_apostrofo_escapado_dentro_do_literal_nao_confunde_a_leitura():
    validate_readonly_query("SELECT * FROM t WHERE nome = 'O''DONNELL' AND x = 1")


SENHA_SENTINELA = "SENHA-QUE-NUNCA-PODE-APARECER-EM-LOG"

DB = CissDbConfig(
    driver="IBM DB2 ODBC DRIVER",
    host="172.16.32.112",
    port=50000,
    database="TESTE",
    user="dba",
    password=SENHA_SENTINELA,
    timeout=10,
)


class ErroFalsoDoDriver(Exception):
    """Faz o papel de pyodbc.Error nos testes."""


class CursorFalso:
    def __init__(self, description=None, linhas=(), erro=None):
        self.description = description
        self._linhas = list(linhas)
        self._erro = erro
        self.fechado = False
        self.sql_executado = None
        self.params_recebidos = None

    def execute(self, sql, params):
        self.sql_executado = sql
        self.params_recebidos = params
        if self._erro is not None:
            raise self._erro
        return self

    def fetchall(self):
        return self._linhas

    def close(self):
        self.fechado = True


class ConexaoFalsa:
    def __init__(self, cursor):
        self._cursor = cursor
        self.fechada = False

    def cursor(self):
        return self._cursor

    def close(self):
        self.fechada = True


class DriverFalso:
    """Faz o papel do módulo pyodbc."""

    Error = ErroFalsoDoDriver

    def __init__(self, conexao=None, erro_ao_conectar=None):
        self.conexao = conexao
        self._erro_ao_conectar = erro_ao_conectar
        self.connection_string_recebida = None

    def connect(self, connection_string, timeout):
        self.connection_string_recebida = connection_string
        if self._erro_ao_conectar is not None:
            raise self._erro_ao_conectar
        return self.conexao


def montar_driver(monkeypatch, *, description=None, linhas=(), erro=None, erro_ao_conectar=None):
    cursor = CursorFalso(description=description, linhas=linhas, erro=erro)
    conexao = ConexaoFalsa(cursor)
    driver = DriverFalso(conexao=conexao, erro_ao_conectar=erro_ao_conectar)
    monkeypatch.setattr(ciss, "_load_driver", lambda: driver)
    return driver, conexao, cursor


def test_fetch_all_converte_linhas_em_dicionarios(monkeypatch):
    montar_driver(
        monkeypatch,
        description=[("IDEMPRESA",), ("NOMEFANTASIA",)],
        linhas=[(1, "Loja A"), (2, "Loja B")],
    )

    resultado = CissClient(DB)._fetch_all("SELECT IDEMPRESA, NOMEFANTASIA FROM t")

    assert resultado == [
        {"IDEMPRESA": 1, "NOMEFANTASIA": "Loja A"},
        {"IDEMPRESA": 2, "NOMEFANTASIA": "Loja B"},
    ]


def test_fetch_all_marca_datetime_cru_com_o_fuso_do_ciss(monkeypatch):
    """Sem isso, o provedor JSON do Flask serializa datetime ingênuo como
    `"...GMT"` (RFC 822) — o frontend lê como UTC de verdade e desconta
    3h ao exibir no fuso local, mostrando uma carga confirmada às 11h
    como se fosse às 8h. O servidor do CISS roda no horário de Brasília,
    não UTC."""
    from datetime import date, datetime

    montar_driver(
        monkeypatch,
        description=[("DTALTERACAO",), ("DATA_CADASTRO_CLIENTE",)],
        linhas=[(datetime(2026, 10, 21, 11, 19, 14), date(2023, 1, 13))],
    )

    resultado = CissClient(DB)._fetch_all("SELECT DTALTERACAO, DATA_CADASTRO_CLIENTE FROM t")

    assert resultado == [
        {"DTALTERACAO": "2026-10-21T11:19:14-03:00", "DATA_CADASTRO_CLIENTE": "2023-01-13"}
    ]


def test_fetch_all_repassa_os_parametros_sem_interpolar(monkeypatch):
    _, _, cursor = montar_driver(monkeypatch, description=[("X",)], linhas=[])

    CissClient(DB)._fetch_all("SELECT X FROM t WHERE ID = ?", (21214,))

    assert cursor.sql_executado == "SELECT X FROM t WHERE ID = ?"
    assert cursor.params_recebidos == (21214,)


def test_fetch_one_devolve_none_quando_nao_ha_linha(monkeypatch):
    montar_driver(monkeypatch, description=[("X",)], linhas=[])

    assert CissClient(DB)._fetch_one("SELECT X FROM t") is None


def test_fetch_one_devolve_a_primeira_linha(monkeypatch):
    montar_driver(monkeypatch, description=[("X",)], linhas=[(1,), (2,)])

    assert CissClient(DB)._fetch_one("SELECT X FROM t") == {"X": 1}


def test_cursor_e_conexao_fecham_mesmo_quando_o_execute_levanta(monkeypatch):
    _, conexao, cursor = montar_driver(monkeypatch, erro=ErroFalsoDoDriver("falhou"))

    with pytest.raises(CissDbUnavailable):
        CissClient(DB)._fetch_all("SELECT X FROM t")

    assert cursor.fechado is True
    assert conexao.fechada is True


def test_falha_ao_conectar_vira_cissdbunavailable(monkeypatch):
    montar_driver(monkeypatch, erro_ao_conectar=ErroFalsoDoDriver("host inalcancavel"))

    with pytest.raises(CissDbUnavailable):
        CissClient(DB)._fetch_all("SELECT X FROM t")


def test_driver_ausente_vira_cissdbunavailable(monkeypatch):
    def sem_driver():
        raise CissDbUnavailable

    monkeypatch.setattr(ciss, "_load_driver", sem_driver)

    with pytest.raises(CissDbUnavailable):
        CissClient(DB)._fetch_all("SELECT X FROM t")


def test_sql_de_escrita_e_barrado_antes_de_abrir_conexao(monkeypatch):
    driver, _, _ = montar_driver(monkeypatch, description=[("X",)], linhas=[])

    with pytest.raises(ReadOnlyViolation):
        CissClient(DB)._fetch_all("DROP TABLE t")

    assert driver.connection_string_recebida is None


def test_a_senha_nao_aparece_em_log_de_falha(monkeypatch, caplog):
    montar_driver(monkeypatch, erro_ao_conectar=ErroFalsoDoDriver("falhou"))

    with caplog.at_level(logging.DEBUG):
        with pytest.raises(CissDbUnavailable):
            CissClient(DB)._fetch_all("SELECT X FROM t")

    assert SENHA_SENTINELA not in caplog.text
    assert "PWD=" not in caplog.text


def test_a_senha_nao_aparece_na_mensagem_da_excecao(monkeypatch):
    montar_driver(monkeypatch, erro_ao_conectar=ErroFalsoDoDriver("falhou"))

    with pytest.raises(CissDbUnavailable) as erro:
        CissClient(DB)._fetch_all("SELECT X FROM t")

    assert SENHA_SENTINELA not in str(erro.value)
    assert SENHA_SENTINELA not in repr(erro.value)


def test_o_cliente_nao_tem_metodo_de_escrita():
    for proibido in ("save", "insert", "update", "delete", "create", "commit", "execute"):
        assert not hasattr(CissClient(DB), proibido)


def test_ping_usa_a_tabela_sentinela_do_db2(monkeypatch):
    _, _, cursor = montar_driver(monkeypatch, description=[("1",)], linhas=[(1,)])

    CissClient(DB).ping()

    assert "SYSIBM.SYSDUMMY1" in cursor.sql_executado
    assert cursor.sql_executado.upper().startswith("SELECT 1")


def test_ping_propaga_indisponibilidade(monkeypatch):
    montar_driver(monkeypatch, erro_ao_conectar=ErroFalsoDoDriver("fora do ar"))

    with pytest.raises(CissDbUnavailable):
        CissClient(DB).ping()


# --- Representantes dos métodos de domínio portados ------------------


def test_get_empresas_mapeia_colunas(monkeypatch):
    montar_driver(
        monkeypatch,
        description=[("IDEMPRESA",), ("NOMEFANTASIA",)],
        linhas=[(3, "Loja Centro")],
    )

    resultado = CissClient(DB).get_empresas()

    assert resultado == [{"id_empresa": 3, "nome": "Loja Centro"}]


def test_get_cargas_eleva_data_inicial_ao_corte_de_integracao_propria(monkeypatch):
    from datetime import datetime

    _, _, cursor = montar_driver(monkeypatch, description=[("IDEMPRESA",)], linhas=[])

    CissClient(DB).get_cargas(datetime(2020, 1, 1), datetime(2026, 12, 31))

    assert cursor.params_recebidos[0] == ciss.CORTE_INTEGRACAO_PROPRIA


def test_get_cargas_filtra_por_empresa_quando_informado(monkeypatch):
    _, _, cursor = montar_driver(monkeypatch, description=[("IDEMPRESA",)], linhas=[])
    from datetime import datetime

    CissClient(DB).get_cargas(datetime(2026, 10, 1), datetime(2026, 10, 14), id_empresa=5)

    assert "RP.IDEMPRESA = ?" in cursor.sql_executado
    assert 5 in cursor.params_recebidos


def test_get_cargas_filtra_por_integracao_fusion_por_padrao(monkeypatch):
    """LOCAL_RETIRADA.FLAGUTILIZAINTEGRACAOFUSION — achado em produção: das
    cargas desde o corte de integração, ~89% vêm de local de retirada sem
    a integração marcada (venda balcão/"FR CAIXA"), que nunca deveriam
    aparecer aqui. Filtrado por padrão, com escape via apenas_fusion=False."""
    from datetime import datetime

    _, _, cursor = montar_driver(monkeypatch, description=[("IDEMPRESA",)], linhas=[])

    CissClient(DB).get_cargas(datetime(2026, 10, 1), datetime(2026, 10, 14))

    assert "FLAGUTILIZAINTEGRACAOFUSION" in cursor.sql_executado


def test_get_cargas_apenas_fusion_false_nao_filtra(monkeypatch):
    from datetime import datetime

    _, _, cursor = montar_driver(monkeypatch, description=[("IDEMPRESA",)], linhas=[])

    CissClient(DB).get_cargas(datetime(2026, 10, 1), datetime(2026, 10, 14), apenas_fusion=False)

    assert "FLAGUTILIZAINTEGRACAOFUSION" not in cursor.sql_executado


def test_get_pre_cargas_filtra_por_integracao_fusion_por_padrao(monkeypatch):
    """Mesmo critério de get_cargas, mas PRE_CARGA_PROD usa IDPONTORETIRADA,
    não IDLOCALRETIRADA — mesmo espaço de id de LOCAL_RETIRADA."""
    from datetime import datetime

    _, _, cursor = montar_driver(monkeypatch, description=[("IDEMPRESA",)], linhas=[])

    CissClient(DB).get_pre_cargas(datetime(2026, 10, 1), datetime(2026, 10, 14))

    assert "FLAGUTILIZAINTEGRACAOFUSION" in cursor.sql_executado
    assert "IDPONTORETIRADA" in cursor.sql_executado


def test_get_pre_cargas_apenas_fusion_false_nao_filtra(monkeypatch):
    from datetime import datetime

    _, _, cursor = montar_driver(monkeypatch, description=[("IDEMPRESA",)], linhas=[])

    CissClient(DB).get_pre_cargas(datetime(2026, 10, 1), datetime(2026, 10, 14), apenas_fusion=False)

    assert "FLAGUTILIZAINTEGRACAOFUSION" not in cursor.sql_executado


def test_get_cliente_fusion_filtra_por_idclifor_e_ativo(monkeypatch):
    _, _, cursor = montar_driver(monkeypatch, description=[("CODIGO_CLIENTE",)], linhas=[])

    CissClient(DB).get_cliente_fusion(777)

    assert "CF.IDCLIFOR = ?" in cursor.sql_executado
    assert "FLAGINATIVO = 'F'" in cursor.sql_executado
    assert cursor.params_recebidos == (777,)


# --- busca de carga por documento (código/pedido/NF) ------------------


def test_get_carga_por_pedido_filtra_por_idorcamento(monkeypatch):
    montar_driver(
        monkeypatch,
        description=[("IDEMPRESA",), ("IDPLANILHA",)],
        linhas=[(1, 22144395)],
    )

    assert CissClient(DB).get_carga_por_pedido(616139) == (1, 22144395)


def test_get_carga_por_pedido_sem_resultado_devolve_none(monkeypatch):
    montar_driver(monkeypatch, description=[("IDEMPRESA",), ("IDPLANILHA",)], linhas=[])

    assert CissClient(DB).get_carga_por_pedido(999999) is None


def test_get_carga_por_nota_filtra_por_numnota_e_serie(monkeypatch):
    _, _, cursor = montar_driver(
        monkeypatch,
        description=[("IDEMPRESA",), ("IDPLANILHA",)],
        linhas=[(1, 22144395)],
    )

    assert CissClient(DB).get_carga_por_nota(166105, "2") == (1, 22144395)
    assert cursor.params_recebidos == (166105, "2")


def test_get_carga_por_nota_sem_resultado_devolve_none(monkeypatch):
    montar_driver(monkeypatch, description=[("IDEMPRESA",), ("IDPLANILHA",)], linhas=[])

    assert CissClient(DB).get_carga_por_nota(1, "9") is None


# --- filtro por item: só local de retirada com integração Fusion -----------


def test_get_itens_carga_filtra_item_por_local_de_retirada(monkeypatch):
    """Achado em produção (histórico, IDORCAMENTO 50369/50508): um item
    pode ter IDLOCALRETIRADA sem a integração marcada mesmo dentro de uma
    carga com outros itens que têm — ex. um item retirado no balcão ("FR
    CAIXA"), junto de itens de entrega de verdade, mesma NF. O filtro é
    por item (JOIN), não por carga (EXISTS) como em get_cargas."""
    _, _, cursor = montar_driver(monkeypatch, description=[("NUMSEQUENCIA",)], linhas=[])

    CissClient(DB).get_itens_carga(1, 6329109)

    assert "LR_ITEM.FLAGUTILIZAINTEGRACAOFUSION = 'T'" in cursor.sql_executado
    assert "JOIN LOCAL_RETIRADA LR_ITEM ON LR_ITEM.IDLOCALRETIRADA = PR.IDLOCALRETIRADA" in cursor.sql_executado


# --- colisão de IDPLANILHA entre empresas (não é entrega entre filiais) ---


def test_get_itens_carga_so_usa_idempresabaixaest_sem_cabecalho_proprio(monkeypatch):
    """Achado em produção: cargas 2_22160293 e 9_22160293 têm o mesmo
    IDPLANILHA por coincidência de sequência entre empresas diferentes —
    não é a mesma carga, não é entrega entre filiais. Um item com
    cabeçalho direto na própria empresa (IDEMPRESA) não pode vazar pro
    cabeçalho de outra empresa só porque IDEMPRESABAIXAEST bate — por
    isso o NOT EXISTS, limitando o fallback a item sem cabeçalho na
    própria empresa."""
    _, _, cursor = montar_driver(monkeypatch, description=[("NUMSEQUENCIA",)], linhas=[])

    CissClient(DB).get_itens_carga(9, 22160293)

    assert (
        "(PR.IDEMPRESA = ? OR (PR.IDEMPRESABAIXAEST = ? "
        "AND NOT EXISTS (SELECT 1 FROM RETIRADA_PRODUTOS RP_OWN "
        "WHERE RP_OWN.IDEMPRESA = PR.IDEMPRESA AND RP_OWN.IDPLANILHA = PR.IDPLANILHA)))"
    ) in cursor.sql_executado


def test_get_carga_por_pedido_so_usa_idempresabaixaest_sem_cabecalho_proprio(monkeypatch):
    _, _, cursor = montar_driver(
        monkeypatch, description=[("IDEMPRESA",), ("IDPLANILHA",)], linhas=[]
    )

    CissClient(DB).get_carga_por_pedido(616672)

    assert (
        "(RP.IDEMPRESA = PR.IDEMPRESA OR (RP.IDEMPRESA = PR.IDEMPRESABAIXAEST "
        "AND NOT EXISTS (SELECT 1 FROM RETIRADA_PRODUTOS RP_OWN "
        "WHERE RP_OWN.IDEMPRESA = PR.IDEMPRESA AND RP_OWN.IDPLANILHA = PR.IDPLANILHA)))"
    ) in cursor.sql_executado


def test_get_itens_pre_carga_filtra_item_por_ponto_de_retirada(monkeypatch):
    """Mesmo filtro de get_itens_carga, mas PRE_CARGA_PROD usa
    IDPONTORETIRADA — mesmo espaço de id de LOCAL_RETIRADA.IDLOCALRETIRADA."""
    _, _, cursor = montar_driver(monkeypatch, description=[("NUMSEQUENCIA",)], linhas=[])

    CissClient(DB).get_itens_pre_carga(796109)

    assert "LR_ITEM.FLAGUTILIZAINTEGRACAOFUSION = 'T'" in cursor.sql_executado
    assert "JOIN LOCAL_RETIRADA LR_ITEM ON LR_ITEM.IDLOCALRETIRADA = PCP.IDPONTORETIRADA" in cursor.sql_executado
