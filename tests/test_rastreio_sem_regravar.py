"""Rastreio não regrava no DB2 o que já está igual lá: uma leitura em lote
por tabela por ciclo (`CissClient.ler_track`), comparação por tipo de
coluna (`ciss_escrita.linha_identica`), e MERGE só do que mudou."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import fakeredis
import pytest

from cargas_fusion import rastreio, rastreio_eventos
from database import connect, init_database
from integrations.ciss import CissDbUnavailable, _normalizar_valor
from integrations.ciss_escrita import (
    COLUNAS_CARGAS_FUSION_TRACK,
    COLUNAS_ENTREGAS_FUSION_TRACK,
    TAMANHO_LEITURA_CLOB,
    linha_identica,
)
from tests import test_rastreio as t_romaneio
from tests import test_rastreio_entregas as t_entregas
from tests import test_rastreio_eventos as t_eventos


def _como_db2(linha: dict) -> dict:
    """O que volta do `CissClient`: datetime vira ISO com `-03:00`."""
    return {chave: _normalizar_valor(valor) for chave, valor in linha.items()}


class CissDb2Falso:
    """Leitura que devolve o que a escrita "gravou" — o round-trip real."""

    def __init__(self, *fontes, erro=None):
        self.fontes = fontes
        self.erro = erro
        self.leituras = []

    def ler_track(self, tabela, colunas, coluna_filtro, valores):
        self.leituras.append((tabela, sorted(set(valores))))
        if self.erro:
            raise self.erro
        linhas = [linha for fonte in self.fontes for linha in fonte()]
        return [_como_db2(l) for l in linhas if l.get(coluna_filtro) in set(valores)]


# --- Comparação --------------------------------------------------------------


def _linha_carga(**extra):
    linha, motivo = rastreio.mapear_carga_track(t_romaneio._pendencia(1, **extra))
    assert motivo is None
    return linha


def test_linha_identica_depois_do_round_trip_do_db2():
    linha = _linha_carga()
    assert linha_identica(linha, _como_db2(linha), COLUNAS_CARGAS_FUSION_TRACK)


def test_qualquer_coluna_diferente_nao_e_identica():
    linha = _linha_carga()
    for nome, valor in (("PESO", Decimal("1.000")), ("DESTINO", "OUTRO"), ("DATASAIDAPREVISTA", datetime(2026, 1, 1))):
        db2 = _como_db2({**linha, nome: valor})
        assert not linha_identica(linha, db2, COLUNAS_CARGAS_FUSION_TRACK), nome


def test_decimal_e_texto_normalizados():
    linha = _linha_carga()
    db2 = _como_db2(linha)
    db2["PESO"] = Decimal(str(linha["PESO"])) + Decimal("0.0000")  # escala diferente, mesmo valor
    db2["CODIGOPRACAPRINCIPAL"] = (linha["CODIGOPRACAPRINCIPAL"] or "") + "  "  # espaço à direita
    assert linha_identica(linha, db2, COLUNAS_CARGAS_FUSION_TRACK)


def test_na_duvida_grava():
    linha = _linha_carga()
    assert not linha_identica(linha, None, COLUNAS_CARGAS_FUSION_TRACK)
    sem_coluna = {k: v for k, v in _como_db2(linha).items() if k != "KMTOTAL"}
    assert not linha_identica(linha, sem_coluna, COLUNAS_CARGAS_FUSION_TRACK)
    longa = {**linha, "OBSERVACAO": "x" * TAMANHO_LEITURA_CLOB}
    assert not linha_identica(longa, _como_db2(longa), COLUNAS_CARGAS_FUSION_TRACK)


# --- Romaneios ---------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    init_database(tmp_path / "r.db")
    c = connect(tmp_path / "r.db")
    yield c
    c.close()


def _ciclo(conn, fusion, escrita, ciss):
    return rastreio.executar_ciclo(
        conn, fakeredis.FakeRedis(), fusion, escrita,
        discord_webhook_url=None, lock_timeout_segundos=60, ciss_client=ciss,
    )


def test_romaneio_igual_no_db2_nao_regrava_mas_confirma(conn):
    ja_no_db2 = [_linha_carga()]
    fusion = t_romaneio.FusionFalso([[t_romaneio._pendencia(1)]])
    escrita = t_romaneio.EscritaFalsa()
    ciss = CissDb2Falso(lambda: ja_no_db2)

    resultado = _ciclo(conn, fusion, escrita, ciss)

    assert escrita.linhas == []
    assert resultado["processadas"] == {"SEM_MUDANCA": 1}
    assert fusion.confirmados and fusion.confirmados[0][0] == "1"
    assert conn.execute("SELECT status FROM cargas_fusion_int_erp").fetchone()["status"] == "INTERNALIZADO"


def test_romaneio_que_mudou_e_gravado(conn):
    ja_no_db2 = [_linha_carga(peso="1.0")]
    escrita = t_romaneio.EscritaFalsa()
    resultado = _ciclo(conn, t_romaneio.FusionFalso([[t_romaneio._pendencia(1)]]), escrita, CissDb2Falso(lambda: ja_no_db2))
    assert len(escrita.linhas) == 1 and resultado["processadas"] == {"INTERNALIZADO": 1}


def test_duas_pendencias_iguais_do_mesmo_romaneio_gravam_uma_vez(conn):
    """MONTAGEM e depois ROTEIRIZADO com o mesmo dado, no mesmo ciclo."""
    escrita = t_romaneio.EscritaFalsa()
    fusion = t_romaneio.FusionFalso([[
        t_romaneio._pendencia(1), t_romaneio._pendencia(2, tipo="T10_ROMANEIO_ROTEIRIZADO"),
    ]])
    resultado = _ciclo(conn, fusion, escrita, CissDb2Falso(lambda: []))
    assert len(escrita.linhas) == 1
    assert resultado["processadas"] == {"INTERNALIZADO": 1, "SEM_MUDANCA": 1}


def test_leitura_falhou_grava_tudo_como_antes(conn):
    escrita = t_romaneio.EscritaFalsa()
    ciss = CissDb2Falso(lambda: [_linha_carga()], erro=CissDbUnavailable())
    resultado = _ciclo(conn, t_romaneio.FusionFalso([[t_romaneio._pendencia(1)]]), escrita, ciss)
    assert len(escrita.linhas) == 1 and resultado["processadas"] == {"INTERNALIZADO": 1}


def test_uma_leitura_por_tabela_por_ciclo(conn):
    fusion = t_romaneio.FusionFalso([[t_romaneio._pendencia(i, t10_id=800000 + i) for i in range(1, 6)]])
    ciss = CissDb2Falso(lambda: [])
    _ciclo(conn, fusion, t_romaneio.EscritaFalsa(), ciss)
    cargas = [l for l in ciss.leituras if l[0] == "DBA.CARGAS_FUSION_TRACK"]
    assert cargas == [("DBA.CARGAS_FUSION_TRACK", [800001, 800002, 800003, 800004, 800005])]


# --- Entregas ----------------------------------------------------------------


def test_reatualizacao_sem_mudanca_nao_regrava(conn):
    t_entregas._romaneio(conn)
    escrita = t_entregas.EscritaFalsa()
    fusion = t_entregas.FusionFalso()
    ciss = t_entregas.CissFalso(db2=escrita.linhas)
    ciss.ler_track = CissDb2Falso(lambda: escrita.linhas).ler_track

    primeiro = t_entregas._processar(conn, ciss=ciss, fusion=fusion, escrita=escrita)
    gravadas = len(escrita.linhas)
    segundo = t_entregas._processar(conn, ciss=ciss, fusion=fusion, escrita=escrita)

    assert primeiro.get("GRAVADA") and gravadas > 0
    assert len(escrita.linhas) == gravadas  # nada regravado
    assert segundo.get("SEM_MUDANCA") == primeiro["GRAVADA"] and "GRAVADA" not in segundo
    assert {s for s, _ in t_entregas._situacoes(conn).values()} == {"GRAVADA"}


def test_status_mudou_no_fusion_regrava(conn):
    t_entregas._romaneio(conn)
    escrita = t_entregas.EscritaFalsa()
    fusion = t_entregas.FusionFalso()
    ciss = t_entregas.CissFalso()
    ciss.ler_track = CissDb2Falso(lambda: escrita.linhas).ler_track

    t_entregas._processar(conn, ciss=ciss, fusion=fusion, escrita=escrita)
    gravadas = len(escrita.linhas)
    fusion.detalhe = {**fusion.detalhe, "status": "7"}
    segundo = t_entregas._processar(conn, ciss=ciss, fusion=fusion, escrita=escrita)

    assert segundo.get("GRAVADA") and len(escrita.linhas) > gravadas
    assert escrita.linhas[-1]["STATUSENTREGA"] == 7


# --- Eventos -----------------------------------------------------------------


def test_evento_ja_igual_no_db2_nao_regrava(conn):
    t_eventos._romaneio(conn)
    primeira = t_eventos.EscritaFalsa()
    t_eventos._processar(conn, t_eventos.FusionFalso([[t_eventos._evento(100)]]), primeira)
    evento, itens = primeira.gravados[0]
    # Simula: gravou no DB2 mas falhou antes de marcar no sqlite → retenta.
    conn.execute("UPDATE cargas_fusion_evento SET status = 'ERRO'")
    conn.commit()

    segunda = t_eventos.EscritaFalsa()
    resultado = rastreio_eventos.processar_eventos(
        conn, t_eventos.FusionFalso(), segunda, [], [], agora=t_eventos.AGORA,
        ciss=CissDb2Falso(lambda: [evento]),
    )

    assert segunda.gravados == []
    assert resultado["SEM_MUDANCA"] == 1
    assert conn.execute("SELECT status FROM cargas_fusion_evento").fetchone()["status"] == "GRAVADO"


def test_resumo_no_discord_ignora_ciclo_que_so_conferiu(monkeypatch):
    postados = []
    monkeypatch.setattr("integrations.discord.atividade", lambda *a, **k: postados.append(a))
    rastreio._resumir_ciclo({"processadas": {}, "entregas": {"SEM_MUDANCA": 38, "restantes": 0}, "eventos": {}})
    assert postados == []
    rastreio._resumir_ciclo({"processadas": {}, "entregas": {"SEM_MUDANCA": 37, "GRAVADA": 1}, "eventos": {}})
    assert len(postados) == 1
