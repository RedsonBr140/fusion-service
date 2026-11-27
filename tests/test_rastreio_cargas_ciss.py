"""Vínculo romaneio ↔ carga do CISS (`DBA.CARGAS_CISS_FUSION_TRACK`)."""

from __future__ import annotations

import pytest

from cargas_fusion import rastreio_cargas_ciss as rcc
from cargas_fusion.rastreio_entregas import PedidoIdentificado, identificar_pedido
from integrations.ciss import CissDbUnavailable
from integrations.ciss_escrita import (
    SQL_DELETE_CARGA_CISS_FUSION,
    SQL_INSERIR_CARGA_CISS_FUSION,
    EscritaNaoPermitida,
    validate_escrita_permitida,
)
from tests.test_rastreio_entregas import AGORA, _romaneio, conn  # noqa: F401  (fixture)

ROMANEIO = 816622


class CissFalso:
    """`cargas`: formato/chave do pedido → cargas do CISS. `tabela`: as linhas
    "já gravadas" em CARGAS_CISS_FUSION_TRACK (aponte pra escrita.tabela)."""

    def __init__(self, cargas=None, tabela=None, erro_consulta=None, erro_leitura=None):
        self.cargas = cargas or {}
        self.tabela = tabela if tabela is not None else set()
        self.erro_consulta = erro_consulta
        self.erro_leitura = erro_leitura
        self.consultas = []

    def get_cargas_ciss_do_pedido(self, formato, **kw):
        self.consultas.append((formato, kw))
        if self.erro_consulta:
            raise self.erro_consulta
        chave = kw.get("id_orcamento") or kw.get("id_pre_nota")
        return list(self.cargas.get(chave, []))

    def ler_track(self, tabela, colunas, coluna_filtro, valores):
        if self.erro_leitura:
            raise self.erro_leitura
        return [
            {"IDEMPRESA": e, "IDCARGACISS": c, "IDCARGAFUSION": r}
            for e, c, r in self.tabela if r in set(valores)
        ]


class EscritaFalsa:
    def __init__(self):
        self.tabela: set[tuple[int, int, int]] = set()
        self.chamadas = []

    def sincronizar_cargas_ciss(self, id_carga_fusion, inserir, remover=()):
        self.chamadas.append((id_carga_fusion, list(inserir), list(remover)))
        self.tabela |= {(e, c, id_carga_fusion) for e, c in inserir}
        self.tabela -= {(e, c, id_carga_fusion) for e, c in remover}


def _entrega(pedido, filial="2", **extra):
    return {"t32_pedido_original": pedido, "t43_codigo_filial_erp": filial, **extra}


def _par():
    escrita = EscritaFalsa()
    return escrita, lambda **kw: CissFalso(tabela=escrita.tabela, **kw)


def _rodar(conn, ciss, escrita, **kw):
    return rcc.processar(conn, ciss, escrita, [], agora=AGORA, **kw)


# --- Campos do próprio Fusion ---------------------------------------------------


def test_campos_do_fusion_ignoram_pre_carga_teste_e_o_proprio_romaneio():
    payload = {
        "t10_id": "837188",
        "t10_cargas_erp": ",9_22153768,PC_2_796273,11_22154074",
        "ENTREGAS": [
            {"t32_carga_erp": "9_22153768"}, {"t32_carga_erp": "TESTE_CLAUDE_CARGA_002"},
            {"t32_carga_erp": "837188"}, {"t32_carga_erp": ""}, {"t32_carga_erp": "1_21312456"},
        ],
    }
    assert rcc.cargas_informadas_pelo_fusion(payload) == {(9, 22153768), (11, 22154074), (1, 21312456)}


def test_sufixo_do_pedido_sem_orcamento_restringe_a_carga():
    assert identificar_pedido("PN22171369_22161374", "2") == PedidoIdentificado(
        "pre_nota", id_pre_nota=22171369, id_empresa=2, id_planilha_carga=22161374
    )
    assert identificar_pedido("PN22171369_PC796450", "2").id_precarga == 796450


# --- Ciclo ------------------------------------------------------------------------


def test_vincula_pelo_fusion_e_pelo_ciss(conn):
    _romaneio(conn, entregas=[_entrega("616205_PC796273"), _entrega("613768_22105494")],
              t10_cargas_erp="1_21312456")
    escrita, ciss_com = _par()
    ciss = ciss_com(cargas={616205: [(2, 22200001)], 613768: [(2, 22105494)]})

    resultado = _rodar(conn, ciss, escrita)

    assert escrita.tabela == {(1, 21312456, ROMANEIO), (2, 22200001, ROMANEIO), (2, 22105494, ROMANEIO)}
    assert resultado["VINCULADAS"] == 3 and resultado["REMOVIDAS"] == 0
    # O código do pedido restringe a carga (redespacho): pré-carga e carga.
    assert sorted(ciss.consultas, key=lambda c: c[0]) == [
        ("carga", {"id_orcamento": 613768, "id_pre_nota": None, "id_empresa": None,
                   "id_planilha_carga": 22105494, "id_precarga": None}),
        ("pre_carga", {"id_orcamento": 616205, "id_pre_nota": None, "id_empresa": None,
                       "id_planilha_carga": None, "id_precarga": 796273}),
    ]


def test_segundo_ciclo_nao_consulta_nem_grava_de_novo(conn):
    _romaneio(conn, entregas=[_entrega("613768_22105494")])
    escrita, ciss_com = _par()
    _rodar(conn, ciss_com(cargas={613768: [(2, 22105494)]}), escrita)
    ciss = ciss_com(cargas={613768: [(2, 22105494)]})

    resultado = _rodar(conn, ciss, escrita)

    assert ciss.consultas == [] and len(escrita.chamadas) == 1
    assert resultado["VINCULADAS"] == 0


def test_pre_carga_ainda_sem_carga_vincula_quando_o_ciss_forma(conn):
    _romaneio(conn, entregas=[_entrega("616205_PC796273")])
    escrita, ciss_com = _par()
    primeiro = _rodar(conn, ciss_com(), escrita)
    assert escrita.tabela == set() and primeiro["sem_carga"] == 1

    _rodar(conn, ciss_com(cargas={616205: [(2, 22200001)]}), escrita)
    assert escrita.tabela == {(2, 22200001, ROMANEIO)}


def test_carga_que_saiu_do_romaneio_e_removida(conn):
    _romaneio(conn, entregas=[_entrega("1_10"), _entrega("2_20")])
    escrita, ciss_com = _par()
    cargas = {1: [(2, 10)], 2: [(2, 20)]}
    _rodar(conn, ciss_com(cargas=cargas), escrita)
    assert escrita.tabela == {(2, 10, ROMANEIO), (2, 20, ROMANEIO)}

    # Fusion resequenciou: o pedido 2_20 saiu deste romaneio.
    _romaneio(conn, entregas=[_entrega("1_10")], codigo_int=2)
    resultado = _rodar(conn, ciss_com(cargas=cargas), escrita)

    assert escrita.tabela == {(2, 10, ROMANEIO)}
    assert resultado["REMOVIDAS"] == 1


def test_falha_na_consulta_nunca_remove(conn):
    _romaneio(conn, entregas=[_entrega("1_10")])
    escrita, ciss_com = _par()
    escrita.tabela.add((2, 99, ROMANEIO))  # vínculo que já estava lá

    resultado = _rodar(conn, ciss_com(erro_consulta=CissDbUnavailable()), escrita)

    assert (2, 99, ROMANEIO) in escrita.tabela
    assert resultado["erro_consulta"] == 1 and resultado["REMOVIDAS"] == 0


def test_tabela_ilegivel_pula_a_etapa_sem_gravar(conn):
    """O caso comum: a tabela ainda não foi criada no DB2."""
    _romaneio(conn, entregas=[_entrega("1_10")])
    escrita = EscritaFalsa()
    resultado = _rodar(conn, CissFalso(erro_leitura=CissDbUnavailable()), escrita)
    assert resultado == {"status": "indisponivel"} and escrita.chamadas == []


def test_limite_de_consultas_por_ciclo(conn):
    _romaneio(conn, entregas=[_entrega(f"{i}_{100 + i}") for i in range(1, 6)])
    escrita, ciss_com = _par()
    ciss = ciss_com(cargas={i: [(2, 100 + i)] for i in range(1, 6)})
    resultado = _rodar(conn, ciss, escrita, limite_consultas=2)
    assert len(ciss.consultas) == 2 and resultado["restantes"] == 3
    # Só o que foi consultado entra; o resto do romaneio é desconhecido, sem remoção.
    assert len(escrita.tabela) == 2


# --- Guarda de escrita ------------------------------------------------------------


def test_sql_do_vinculo_passa_na_guarda():
    validate_escrita_permitida(SQL_INSERIR_CARGA_CISS_FUSION)
    validate_escrita_permitida(SQL_DELETE_CARGA_CISS_FUSION)


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM DBA.CARGAS_FUSION_TRACK WHERE IDCARGAFUSION = 1",
        "DELETE FROM DBA.CARGAS_CISS_FUSION_TRACK",
        "DELETE FROM DBA.NOTAS WHERE IDEMPRESA = 1",
        "DELETE FROM DBA.CARGAS_CISS_FUSION_TRACK WHERE 1 = 1; DROP TABLE DBA.NOTAS",
        "UPDATE DBA.CARGAS_CISS_FUSION_TRACK SET IDEMPRESA = 1 WHERE IDEMPRESA IN (SELECT DELETE FROM X)",
    ],
)
def test_delete_so_na_tabela_de_vinculo_e_com_where(sql):
    with pytest.raises(EscritaNaoPermitida):
        validate_escrita_permitida(sql)
