"""Testes do rastreio getIntErp → DBA.CARGAS_FUSION_TRACK
(cargas_fusion/rastreio.py) e da guarda de escrita
(integrations/ciss_escrita.py). Fusion, CISS e Discord são dublês; o lock
usa `fakeredis`. Nenhum teste toca a fila real do Fusion (destrutiva) nem
o DB2.
"""

import json
from datetime import datetime
from decimal import Decimal

import fakeredis
import pytest

from cargas_fusion import rastreio
from database import connect, init_database
from integrations.ciss import CissDbUnavailable
from integrations.ciss_escrita import (
    COLUNAS_CARGAS_FUSION_TRACK,
    SQL_MERGE_CARGAS_FUSION_TRACK,
    EscritaNaoPermitida,
    validate_escrita_permitida,
)
from integrations.fusion import FusionUnavailable
from tests.test_cargas_fusion_routes import usuario_com_a_tool


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "rastreio.db"
    init_database(db_path)
    conexao = connect(db_path)
    yield conexao
    conexao.close()


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()


def _pendencia(codigo_int, t10_id=823636, tipo="T10_ROMANEIO_MONTAGEM_CARGA", **extra):
    item = {
        "CODIGO_INT": str(codigo_int),
        "TIPO_INT": tipo,
        "t10_id": str(t10_id),
        "t10_destino": "TRIUNFO/PE - JAF CONSTRUCAO E CONSULTORIA EIRELI",
        "t10_obs": "obs da carga",
        "t10_data_saida": "2026-11-27 07:00:00",
        "t05_codigo_erp": "52057",
        "t06_codigo_erp": "14",
        "t43_codigo_filial_erp": "1",
        "peso": "1234.5678",
        "volume": "2.5",
        "valortotal": "2387.28",
        "custo": "10.1234567",
        "t10_custo_estimado": "7.80724",
        "numclientes": "2",
        "numcidades": "1",
        "codpracaprinc": "1",
        "t10_data_cancelamento": "0000-00-00 00:00:00",
        "t10_data_grava": "2026-11-26 10:00:00",
        "t10_situacao": "Ativo",
        "ENTREGAS": [{"t12_ordemEntrega": "1"}, {"t12_ordemEntrega": "2"}],
    }
    item.update(extra)
    return item


class FusionFalso:
    def __init__(self, lotes=(), *, erro_get=None, confirmacao="OK"):
        self._lotes = [list(lote) for lote in lotes]
        self._erro_get = erro_get
        self.confirmacao = confirmacao
        self.chamadas_get = 0
        self.confirmados = []
        self.cargas = []

    def get_int_erp(self, limite=50):
        self.chamadas_get += 1
        if self._erro_get is not None:
            raise self._erro_get
        return self._lotes.pop(0) if self._lotes else []

    def set_int_erp(self, int_id, retorno_carga, carga=None):
        if isinstance(self.confirmacao, Exception):
            raise self.confirmacao
        self.confirmados.append((int_id, retorno_carga))
        self.cargas.append(carga)
        return self.confirmacao


class EscritaFalsa:
    def __init__(self, *, erro=None):
        self.erro = erro
        self.linhas = []

    def upsert_carga_fusion_track(self, linha):
        if self.erro is not None:
            raise self.erro
        self.linhas.append(linha)


def executar(conn, redis_client, fusion, escrita):
    return rastreio.executar_ciclo(
        conn, redis_client, fusion, escrita, discord_webhook_url=None, lock_timeout_segundos=60
    )


def _status(conn):
    return {
        linha["codigo_int"]: (linha["status"], linha["confirmado_em"] is not None)
        for linha in conn.execute("SELECT * FROM cargas_fusion_int_erp")
    }


# --- Guarda de escrita ---------------------------------------------------


def test_merge_gerado_passa_na_guarda():
    validate_escrita_permitida(SQL_MERGE_CARGAS_FUSION_TRACK)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM DBA.CARGAS_FUSION_TRACK",
        "DELETE FROM DBA.CARGAS_FUSION_TRACK",
        "UPDATE DBA.RETIRADA_PRODUTOS SET OBSERVACAO = 'x'",
        "INSERT INTO DBA.EVENTOS_FUSION_TRACK (IDCARGAFUSION) VALUES (1)",
        "MERGE INTO DBA.CARGAS_FUSION_TRACK T USING X S ON 1=1 WHEN MATCHED THEN DELETE",
        "UPDATE DBA.CARGAS_FUSION_TRACK SET DESTINO = 'x'; DROP TABLE DBA.CLIENTE",
        "UPDATE CARGAS_FUSION_TRACK SET DESTINO = 'x'",
    ],
)
def test_guarda_rejeita_fora_da_allow_list(sql):
    with pytest.raises(EscritaNaoPermitida):
        validate_escrita_permitida(sql)


def test_guarda_ignora_palavra_proibida_dentro_de_literal():
    validate_escrita_permitida("UPDATE DBA.CARGAS_FUSION_TRACK SET DESTINO = 'DELETE; DROP'")


# --- Mapeamento ----------------------------------------------------------


def test_mapeia_pendencia_para_todas_as_colunas():
    linha, motivo = rastreio.mapear_carga_track(_pendencia(1))

    assert motivo is None
    assert set(linha) == {nome for nome, _ in COLUNAS_CARGAS_FUSION_TRACK}
    assert linha["IDCARGAFUSION"] == 823636
    assert linha["IDEMPRESA"] == 1
    assert linha["IDMOTORISTA"] == 52057
    assert linha["IDVEICULO"] == 14
    assert linha["DESTINO"] == "TRIUNFO/PE - JAF CONSTRUCAO E CONSULTORIA EIR"  # VARCHAR(45)
    assert linha["PESO"] == Decimal("1234.568")
    assert linha["CUSTO"] == Decimal("10.123")
    assert linha["NUMEROENTREGAS"] == 2
    assert linha["NUMEROCLIENTES"] == 2
    assert linha["DATASAIDAPREVISTA"] == datetime(2026, 11, 27, 7, 0)
    assert linha["DATACANCELAMENTO"] is None  # "0000-00-00 ..." não é data
    assert linha["STATUSROMANEIO"] is None  # mapeamento texto→inteiro ainda não definido


def test_campos_ausentes_viram_null_sem_erro():
    item = {"t10_id": "5", "t43_codigo_filial_erp": "2", "t05_codigo_erp": "9"}
    linha, motivo = rastreio.mapear_carga_track(item)
    assert motivo is None
    assert linha["IDVEICULO"] is None and linha["PESO"] is None and linha["NUMEROENTREGAS"] is None


def test_km_aceita_variacao_de_caixa_do_nome():
    linha, _ = rastreio.mapear_carga_track(_pendencia(1, t10_kmtotal="20", t10_kmRealizado="3.5"))
    assert linha["KMTOTAL"] == Decimal("20.0000000")
    assert linha["KMREALIZADO"] == Decimal("3.5000000")


@pytest.mark.parametrize("campo", ["t05_codigo_erp", "t43_codigo_filial_erp", "t10_id"])
def test_obrigatorio_ausente_ou_nao_numerico_e_erro(campo):
    linha, motivo = rastreio.mapear_carga_track(_pendencia(1, **{campo: "ABC"}))
    assert linha is None
    assert campo in motivo


# --- Ciclo ---------------------------------------------------------------


def test_grava_no_ciss_e_confirma(conn, redis_client):
    fusion = FusionFalso([[_pendencia(10), _pendencia(11, t10_id=900)]])
    escrita = EscritaFalsa()

    resultado = executar(conn, redis_client, fusion, escrita)

    assert resultado["recebidas"] == 2
    assert resultado["confirmadas"] == 2
    assert [l["IDCARGAFUSION"] for l in escrita.linhas] == [823636, 900]
    assert [c[0] for c in fusion.confirmados] == ["10", "11"]
    assert _status(conn) == {10: ("INTERNALIZADO", True), 11: ("INTERNALIZADO", True)}


def test_payload_cru_fica_guardado(conn, redis_client):
    executar(conn, redis_client, FusionFalso([[_pendencia(10)]]), EscritaFalsa())
    payload = json.loads(conn.execute("SELECT payload FROM cargas_fusion_int_erp").fetchone()["payload"])
    assert payload["t10_situacao"] == "Ativo"


def test_pagina_ate_lote_incompleto(conn, redis_client):
    cheio = [_pendencia(i, t10_id=i) for i in range(1, 51)]
    fusion = FusionFalso([cheio, [_pendencia(99, t10_id=99)]])

    resultado = executar(conn, redis_client, fusion, EscritaFalsa())

    assert resultado["recebidas"] == 51
    assert fusion.chamadas_get == 2


def test_falha_no_ciss_guarda_e_retenta_sem_confirmar(conn, redis_client):
    """getIntErp já consumiu a pendência — falha no DB2 não pode perder
    nada nem confirmar ao Fusion algo que não foi gravado."""
    fusion = FusionFalso([[_pendencia(10)]])
    escrita = EscritaFalsa(erro=CissDbUnavailable())

    executar(conn, redis_client, fusion, escrita)
    assert _status(conn) == {10: ("ERRO", False)}
    assert fusion.confirmados == []

    escrita.erro = None
    executar(conn, redis_client, fusion, escrita)
    assert _status(conn) == {10: ("INTERNALIZADO", True)}
    assert conn.execute("SELECT tentativas FROM cargas_fusion_int_erp").fetchone()["tentativas"] == 2


def test_falha_no_setinterp_nao_regrava_e_so_reconfirma(conn, redis_client):
    fusion = FusionFalso([[_pendencia(10)]], confirmacao="ERRO X")
    escrita = EscritaFalsa()

    executar(conn, redis_client, fusion, escrita)
    assert _status(conn) == {10: ("INTERNALIZADO", False)}

    fusion.confirmacao = "OK"
    resultado = executar(conn, redis_client, fusion, escrita)
    assert resultado["confirmadas"] == 1
    assert len(escrita.linhas) == 1  # nada regravado no CISS
    assert _status(conn) == {10: ("INTERNALIZADO", True)}


def test_setinterp_fusion_fora_do_ar(conn, redis_client):
    fusion = FusionFalso([[_pendencia(10)]], confirmacao=FusionUnavailable())
    resultado = executar(conn, redis_client, fusion, EscritaFalsa())
    assert resultado["falhas_confirmacao"] == 1


def test_tipo_nao_processado_e_ignorado_sem_confirmar(conn, redis_client):
    fusion = FusionFalso([[_pendencia(10, tipo="T10_ROMANEIO_FINALIZADO")]])
    escrita = EscritaFalsa()

    executar(conn, redis_client, fusion, escrita)

    assert escrita.linhas == []
    assert fusion.confirmados == []
    assert _status(conn) == {10: ("IGNORADO", False)}


def test_pendencia_antiga_retentada_nao_sobrescreve_a_nova(conn, redis_client):
    """MONTAGEM (10) falha, ROTEIRIZADO (11) do mesmo romaneio grava —
    retentar a 10 depois sobrescreveria dado novo com velho."""
    fusion = FusionFalso([[_pendencia(10, t10_destino="VELHO")]])
    escrita = EscritaFalsa(erro=CissDbUnavailable())
    executar(conn, redis_client, fusion, escrita)

    escrita.erro = None
    # Simula a 11 já gravada num ciclo anterior com a 10 ainda em ERRO.
    rastreio._registrar_recebidos(conn, [_pendencia(11, tipo="T10_ROMANEIO_ROTEIRIZADO", t10_destino="NOVO")])
    conn.execute("UPDATE cargas_fusion_int_erp SET status = 'INTERNALIZADO' WHERE codigo_int = 11")
    conn.commit()

    executar(conn, redis_client, fusion, escrita)

    assert escrita.linhas == []
    assert _status(conn)[10] == ("SUPERADO", True)


def test_mesmo_ciclo_processa_em_ordem_de_codigo_int(conn, redis_client):
    fusion = FusionFalso([[_pendencia(11, t10_destino="NOVO"), _pendencia(10, t10_destino="VELHO")]])
    escrita = EscritaFalsa()

    executar(conn, redis_client, fusion, escrita)

    assert [l["DESTINO"] for l in escrita.linhas] == ["VELHO", "NOVO"]


def test_erro_de_mapeamento_fica_erro_e_notifica_so_na_primeira(conn, redis_client, monkeypatch):
    notificacoes = []
    monkeypatch.setattr(rastreio.discord, "erro", lambda url, titulo, descricao=None, campos=None: notificacoes.append(f"{titulo}\n{descricao}"))
    fusion = FusionFalso([[_pendencia(10, t05_codigo_erp="")]])

    executar(conn, redis_client, fusion, EscritaFalsa())
    executar(conn, redis_client, fusion, EscritaFalsa())

    assert _status(conn) == {10: ("ERRO", False)}
    assert len(notificacoes) == 1
    assert "t05_codigo_erp" in notificacoes[0]


def test_reenvio_manual_do_mesmo_codigo_int_reprocessa(conn, redis_client):
    fusion = FusionFalso([[_pendencia(10, t10_destino="A")], [_pendencia(10, t10_destino="B")]])
    escrita = EscritaFalsa()

    executar(conn, redis_client, fusion, escrita)
    executar(conn, redis_client, fusion, escrita)

    assert [l["DESTINO"] for l in escrita.linhas] == ["A", "B"]
    assert conn.execute("SELECT COUNT(*) AS n FROM cargas_fusion_int_erp").fetchone()["n"] == 1


def test_fusion_fora_do_ar_no_get_ainda_processa_pendentes(conn, redis_client):
    rastreio._registrar_recebidos(conn, [_pendencia(10)])
    fusion = FusionFalso(erro_get=FusionUnavailable())

    resultado = executar(conn, redis_client, fusion, EscritaFalsa())

    assert resultado["erro_coleta"]
    assert resultado["processadas"] == {"INTERNALIZADO": 1}


def test_lock_ocupado_pula_o_ciclo(conn, redis_client):
    redis_client.lock(rastreio.NOME_LOCK, timeout=60).acquire(blocking=False)
    fusion = FusionFalso([[_pendencia(10)]])

    resultado = executar(conn, redis_client, fusion, EscritaFalsa())

    assert resultado["status"] == "pulado"
    assert fusion.chamadas_get == 0


def test_sem_clients_nao_faz_nada(conn, redis_client):
    assert executar(conn, redis_client, None, EscritaFalsa())["status"] == "nao_configurado"


# --- Rotas ---------------------------------------------------------------


def test_rotas_exigem_sessao(client):
    assert client.post("/api/rastreio/int-erp/executar-agora").status_code == 401
    assert client.get("/api/rastreio/int-erp").status_code == 401


def test_executar_agora_desligado_devolve_409(client, config):
    usuario_com_a_tool(client, config)
    assert client.post("/api/rastreio/int-erp/executar-agora").status_code == 409


def test_executar_agora_ligado_roda_o_ciclo(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["RASTREIO_REDIS_CLIENT"] = fakeredis.FakeRedis()
    app.config["FUSION_CLIENT"] = FusionFalso([[_pendencia(10)]])
    app.config["CISS_ESCRITA_CLIENT"] = EscritaFalsa()

    resp = client.post("/api/rastreio/int-erp/executar-agora")

    assert resp.status_code == 200
    assert resp.get_json()["confirmadas"] == 1
    listagem = client.get("/api/rastreio/int-erp").get_json()
    assert listagem["contagem"] == {"INTERNALIZADO": 1}
    assert listagem["itens"][0]["confirmado_em"].endswith("Z")


class CissLeituraFalsa:
    def __init__(self, linha=None):
        self.linha = linha

    def get_carga_fusion_track(self, id_carga_fusion):
        return self.linha


def test_detalhe_mostra_payload_mapeada_e_linha_do_ciss(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["RASTREIO_REDIS_CLIENT"] = fakeredis.FakeRedis()
    app.config["FUSION_CLIENT"] = FusionFalso([[_pendencia(10)]])
    app.config["CISS_ESCRITA_CLIENT"] = EscritaFalsa()
    app.config["CISS_DB_CLIENT"] = CissLeituraFalsa({"IDCARGAFUSION": 823636, "PESO": Decimal("1234.568")})
    client.post("/api/rastreio/int-erp/executar-agora")
    id_ = client.get("/api/rastreio/int-erp").get_json()["itens"][0]["id"]

    corpo = client.get(f"/api/rastreio/int-erp/{id_}").get_json()

    assert corpo["payload"]["CODIGO_INT"] == "10"
    assert corpo["linha_mapeada"]["PESO"] == "1234.568"
    assert corpo["linha_mapeada"]["DATASAIDAPREVISTA"] == "2026-11-27T07:00:00-03:00"
    assert corpo["linha_ciss"] == {"IDCARGAFUSION": 823636, "PESO": "1234.568"}
    assert corpo["erro_ciss"] is None


def test_detalhe_inexistente_404(client, config):
    usuario_com_a_tool(client, config)
    assert client.get("/api/rastreio/int-erp/999").status_code == 404


def test_varias_falhas_viram_uma_notificacao_so_por_ciclo(conn, redis_client, monkeypatch):
    """Primeiro ciclo real: 290 pendências, uma mensagem cada → Discord 429."""
    notificacoes = []
    monkeypatch.setattr(rastreio.discord, "erro", lambda url, titulo, descricao=None, campos=None: notificacoes.append(f"{titulo}\n{descricao}"))
    fusion = FusionFalso([[_pendencia(i, t10_id=i) for i in range(1, 31)]], confirmacao="ERRO")

    executar(conn, redis_client, fusion, EscritaFalsa())

    assert len(notificacoes) == 1
    assert "30 problema(s)" in notificacoes[0]


def test_discord_falha_nao_loga_url_do_webhook(caplog):
    import responses as rsps

    from integrations import discord

    url = "https://discord.com/api/webhooks/123/TOKEN-SECRETO"
    with rsps.RequestsMock() as mock:
        mock.add(rsps.POST, url, status=500)
        discord._postar(url, {"content": "oi"})
    assert "TOKEN-SECRETO" not in caplog.text
    assert "HTTP 500" in caplog.text


def test_setinterp_manda_t10_id_como_carga_contorno(conn, redis_client):
    """Contorno (não o resultado final): sem `carga`, o Fusion grava o
    setIntErp como erro; não temos carga do ERP, então vai o t10_id."""
    fusion = FusionFalso([[_pendencia(10, t10_id=816609)]])
    executar(conn, redis_client, fusion, EscritaFalsa())
    assert fusion.cargas == ["816609"]


@pytest.mark.parametrize(
    "resposta, ok",
    [
        ("OK", True),
        ({"1621200": "OK"}, True),
        ({"1621200": "Erro: Retorno carga: Internalizado em CARGAS_FUSION_TRACK (romaneio 816609)"}, False),
        ({}, False),
        ("Erro", False),
    ],
)
def test_reconhece_formatos_de_resposta_do_setinterp(resposta, ok):
    assert rastreio._confirmacao_ok(resposta) is ok


def test_tipo_nao_processado_vira_aviso_sem_mencao_uma_vez(conn, redis_client, monkeypatch):
    """Ex. real: T10_ROMANEIO_CANCELA_CARGA (fora da doc) no primeiro ciclo."""
    avisos, erros = [], []
    monkeypatch.setattr(rastreio.discord, "aviso", lambda url, titulo, descricao=None, campos=None: avisos.append(descricao))
    monkeypatch.setattr(rastreio.discord, "erro", lambda *a, **kw: erros.append(a))
    fusion = FusionFalso([[_pendencia(10, tipo="T10_ROMANEIO_CANCELA_CARGA")]])

    executar(conn, redis_client, fusion, EscritaFalsa())
    executar(conn, redis_client, fusion, EscritaFalsa())

    assert len(avisos) == 1 and "T10_ROMANEIO_CANCELA_CARGA" in avisos[0]
    assert erros == []


# --- Ativo ≠ automático (decisão do negócio, 2026-11-26) -------------------


def _env_base(**extra):
    return {"SECRET_KEY": "x" * 32, "CISS_API_URL": "https://ciss.test", "REDIS_URL": "redis://x:6379/0", **extra}


def test_automatico_desligado_por_padrao():
    from config import load_config

    cfg = load_config(_env_base(RASTREIO_INT_ERP_ATIVO="true"))
    assert cfg.rastreio_int_erp_ativo is True and cfg.rastreio_int_erp_automatico is False


def test_automatico_sem_ativo_e_erro_de_config():
    from config import ConfigError, load_config

    with pytest.raises(ConfigError):
        load_config(_env_base(RASTREIO_INT_ERP_AUTOMATICO="true"))


@pytest.mark.parametrize("automatico, agenda", [(False, False), (True, True)])
def test_scheduler_so_com_as_duas_flags(config, monkeypatch, automatico, agenda):
    import dataclasses

    import app as app_module

    chamadas = []
    monkeypatch.setattr(app_module, "_agendar_rastreio_int_erp", lambda *a: chamadas.append(a))
    monkeypatch.setattr(app_module.redis.Redis, "from_url", classmethod(lambda cls, url, **kw: fakeredis.FakeRedis()))
    cfg = dataclasses.replace(
        config, redis_url="redis://x:6379/0", rastreio_int_erp_ativo=True, rastreio_int_erp_automatico=automatico
    )

    aplicacao = app_module.create_app(cfg)

    assert bool(chamadas) is agenda
    assert aplicacao.config["RASTREIO_REDIS_CLIENT"] is not None  # botão manual existe nos dois casos


def test_listagem_diz_se_e_manual_ou_automatico(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["RASTREIO_REDIS_CLIENT"] = fakeredis.FakeRedis()
    corpo = client.get("/api/rastreio/int-erp").get_json()
    assert corpo["automatico"] is False and corpo["intervalo_segundos"] == 120
