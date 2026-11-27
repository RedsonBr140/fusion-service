"""Eventos do rastreio (cargas_fusion/rastreio_eventos.py): obtemEventosPorID
→ cru no sqlite → DBA.EVENTOS_FUSION_TRACK (+ ITENS_DEVOLVIDOS). Tudo
dublê — sem Fusion, sem DB2."""

from datetime import datetime
from decimal import Decimal

import pytest

from cargas_fusion import rastreio, rastreio_eventos
from database import connect, init_database
from integrations.ciss import CissDbUnavailable
from integrations.ciss_escrita import (
    COLUNAS_EVENTOS_FUSION_TRACK,
    COLUNAS_ITENS_DEVOLVIDOS_FUSION_TRACK,
    SQL_MERGE_EVENTOS_FUSION_TRACK,
    SQL_MERGE_ITENS_DEVOLVIDOS_FUSION_TRACK,
    validate_escrita_permitida,
)
from integrations.fusion import FusionUnavailable

AGORA = datetime(2026, 11, 26, 12, 0)
ROMANEIO = 816622


@pytest.fixture
def conn(tmp_path):
    init_database(tmp_path / "ev.db")
    c = connect(tmp_path / "ev.db")
    yield c
    c.close()


def _romaneio(conn, t10_id=ROMANEIO, codigo_int=1):
    """Romaneio já gravado em CARGAS_FUSION_TRACK, com duas entregas."""
    item = {
        "CODIGO_INT": str(codigo_int), "TIPO_INT": "T10_ROMANEIO_MONTAGEM_CARGA", "t10_id": str(t10_id),
        "ENTREGAS": [
            {"t32_pedido_original": "616205_PC796273", "t12_ordemEntrega": "2"},
            {"t32_pedido_original": "616300_22160000", "t12_ordemEntrega": "1"},
        ],
    }
    rastreio._registrar_recebidos(conn, [item])
    conn.execute("UPDATE cargas_fusion_int_erp SET status = 'INTERNALIZADO' WHERE codigo_int = ?", (codigo_int,))
    conn.commit()


def _evento(t15_id, *, romaneio=ROMANEIO, tipo="7", pedido="616205_PC796273", motivo="0", **extra):
    evento = {
        "t15_id": str(t15_id), "t15_tipo": tipo, "t15_descricao": "Entrega Realizada",
        "t15_romaneio_id": str(romaneio), "t32_seq_pedido_erp": pedido,
        "t15_kmAtual": "1234.5", "t15_latitude": "-7.98428", "t15_longitude": "-38.2903233",
        "t15_dataHora": "2026-11-26 10:53:56", "t15_data_registro": "2026-11-26 10:54:23",
        "t15_obsMotorista": "", "t15_motivo_devol_reent_id": motivo, "descricao_motivo_devol_reent": "",
        "t15_entrega_id": "24409424",
    }
    evento.update(extra)
    return evento


class FusionFalso:
    def __init__(self, lotes=(), itens=None, erro=None):
        self.lotes = [list(l) for l in lotes]
        self.itens = itens or {}
        self.erro = erro
        self.ids_iniciais = []
        self.itens_pedidos = []

    def obtem_eventos(self, id_inicial=None):
        self.ids_iniciais.append(id_inicial)
        if self.erro:
            raise self.erro
        return self.lotes.pop(0) if self.lotes else []

    def obtem_itens_devolvidos(self, id_evento):
        self.itens_pedidos.append(id_evento)
        return self.itens.get(id_evento, [])


class EscritaFalsa:
    def __init__(self, erro=None):
        self.erro = erro
        self.gravados = []

    def gravar_evento_fusion_track(self, evento, itens=()):
        if self.erro:
            raise self.erro
        self.gravados.append((evento, list(itens)))


def _processar(conn, fusion, escrita=None, alertas=None, avisos=None, agora=AGORA):
    return rastreio_eventos.processar_eventos(
        conn, fusion, escrita if escrita is not None else EscritaFalsa(),
        alertas if alertas is not None else [], avisos if avisos is not None else [], agora=agora,
    )


def _status(conn):
    return {l["t15_id"]: l["status"] for l in conn.execute("SELECT t15_id, status FROM cargas_fusion_evento")}


def test_merges_passam_na_guarda():
    validate_escrita_permitida(SQL_MERGE_EVENTOS_FUSION_TRACK)
    validate_escrita_permitida(SQL_MERGE_ITENS_DEVOLVIDOS_FUSION_TRACK)


def test_grava_evento_do_escopo_com_sequencia_da_entrega(conn):
    _romaneio(conn)
    escrita = EscritaFalsa()

    resultado = _processar(conn, FusionFalso([[_evento(100)]]), escrita)

    assert resultado["GRAVADO"] == 1 and resultado["recebidos"] == 1
    linha, itens = escrita.gravados[0]
    assert set(linha) == {n for n, _ in COLUNAS_EVENTOS_FUSION_TRACK}
    assert linha["IDEVENTOFUSION"] == 100 and linha["IDCARGAFUSION"] == ROMANEIO
    assert linha["IDENTREGAFUSION"] == 2  # t12_ordemEntrega do pedido, não t15_entrega_id
    assert linha["LATITUDE"] == Decimal("-7.9842800") and linha["KMATUAL"] == Decimal("1234.5000000")
    assert linha["DATAHORA"] == datetime(2026, 11, 26, 10, 53, 56)
    assert linha["OBSMOTORISTA"] is None and linha["IDMOTIVODEVOLUCAO"] is None  # "0" = sem motivo
    assert linha["DATAHORAREGISTRO"] == datetime(2026, 11, 26, 10, 54, 23)
    assert linha["STATUS"] == 7 and linha["LINKFOTO"] is None  # link vazio → nulo
    assert itens == []
    assert _status(conn) == {100: "GRAVADO"}


@pytest.mark.parametrize("tipo, esperado", [("21", 21), ("999", 999), ("991", None), ("M", None), ("", None)])
def test_status_e_o_tipo_so_quando_esta_na_tabela(tipo, esperado):
    assert rastreio_eventos.status_evento({"t15_tipo": tipo}) == esperado


def test_link_da_foto_vai_pra_linkfoto(conn):
    _romaneio(conn)
    escrita = EscritaFalsa()
    link = "http://s3-us-west-2.amazonaws.com/fusionfotos/publico/711158/711158_24409427_2026.04.28_10.50.44.jpg"
    _processar(conn, FusionFalso([[_evento(100, t15_link_foto=link)]]), escrita)
    assert escrita.gravados[0][0]["LINKFOTO"] == link


def test_evento_sem_entrega_vai_com_sequencia_nula(conn):
    _romaneio(conn)
    escrita = EscritaFalsa()
    _processar(conn, FusionFalso([[_evento(100, tipo="1", pedido="")]]), escrita)
    assert escrita.gravados[0][0]["IDENTREGAFUSION"] is None


def test_romaneio_fora_do_escopo_fica_so_cru(conn):
    _romaneio(conn)
    escrita = EscritaFalsa()
    antigo = _evento(100, romaneio=705608, t15_dataHora="2026-04-16 11:41:29")

    _processar(conn, FusionFalso([[antigo]]), escrita)

    assert escrita.gravados == []
    assert _status(conn) == {100: "FORA_DO_ESCOPO"}
    assert conn.execute("SELECT payload FROM cargas_fusion_evento").fetchone()["payload"]  # cru guardado


def test_romaneio_recente_desconhecido_espera_e_grava_quando_chega(conn):
    escrita = EscritaFalsa()
    _processar(conn, FusionFalso([[_evento(100)]]), escrita)
    assert _status(conn) == {100: "AGUARDANDO_ROMANEIO"} and escrita.gravados == []

    _romaneio(conn)  # getIntErp do romaneio chegou depois
    _processar(conn, FusionFalso(), escrita)
    assert _status(conn) == {100: "GRAVADO"} and len(escrita.gravados) == 1


def test_espera_pelo_romaneio_tem_limite(conn):
    _processar(conn, FusionFalso([[_evento(100)]]), agora=datetime(2026, 11, 29, 12, 0))
    assert _status(conn) == {100: "FORA_DO_ESCOPO"}


def test_drena_a_fila_em_lotes_passando_o_maior_id_como_piso(conn):
    lote1 = [_evento(i, romaneio=1, t15_dataHora="2026-04-16 11:41:29") for i in range(1, 51)]
    lote2 = [_evento(i, romaneio=1, t15_dataHora="2026-04-16 11:41:29") for i in range(51, 61)]
    fusion = FusionFalso([lote1, lote2])

    resultado = _processar(conn, fusion)

    assert resultado["recebidos"] == 60 and resultado["FORA_DO_ESCOPO"] == 60
    assert fusion.ids_iniciais == [0, 50]  # lote menor que 50 = fila vazia, para


def test_evento_repetido_nao_sobrescreve_o_cru(conn):
    _romaneio(conn)
    _processar(conn, FusionFalso([[_evento(100)]]))
    _processar(conn, FusionFalso([[_evento(100, t15_descricao="OUTRA")]]))
    assert conn.execute("SELECT COUNT(*) n FROM cargas_fusion_evento").fetchone()["n"] == 1
    assert "Entrega Realizada" in conn.execute("SELECT payload FROM cargas_fusion_evento").fetchone()["payload"]


def test_devolucao_grava_so_itens_devolvidos_agregados_por_subproduto(conn):
    _romaneio(conn)
    escrita = EscritaFalsa()
    itens = [
        {"codigo_erp_produto": "124905@135427", "qtd_original": "1", "qtd_devolvido": None,
         "id_motivo_devolucao": None, "motivo_devolucao": None},  # parcial: não devolvido
        {"codigo_erp_produto": "135793", "qtd_original": "2", "qtd_devolvido": "1",
         "id_motivo_devolucao": "558", "motivo_devolucao": "Quebra/Avaria"},
        {"codigo_erp_produto": "135793@135793", "qtd_original": "3", "qtd_devolvido": "2",
         "id_motivo_devolucao": "558", "motivo_devolucao": "Quebra/Avaria"},
        {"codigo_erp_produto": "999", "qtd_original": "1", "qtd_devolvido": "0"},
    ]
    evento = _evento(200, tipo="10", motivo="558", descricao_motivo_devol_reent="Quebra/Avaria")
    fusion = FusionFalso([[evento]], itens={"200": itens})

    _processar(conn, fusion, escrita)

    linha, gravados = escrita.gravados[0]
    assert linha["IDMOTIVODEVOLUCAO"] == 558 and linha["DESCRDEVOLUCAO"] == "Quebra/Avaria"
    assert len(gravados) == 1
    item = gravados[0]
    assert set(item) == {n for n, _ in COLUNAS_ITENS_DEVOLVIDOS_FUSION_TRACK}
    assert item["IDSUBPRODUTO"] == 135793 and item["IDENTREGAFUSION"] == 2 and item["IDEVENTOFUSION"] == 200
    assert item["QTDDEVOLVIDO"] == Decimal("3") and item["QTDORIGINAL"] == Decimal("5")
    assert item["IDMOTIVO"] == 558
    assert conn.execute("SELECT itens_gravados FROM cargas_fusion_evento").fetchone()["itens_gravados"] == 1


def test_itens_sem_sequencia_da_entrega_nao_entram_e_avisam(conn):
    _romaneio(conn)
    escrita, avisos = EscritaFalsa(), []
    evento = _evento(200, tipo="9", motivo="556", pedido="DESCONHECIDO_1")
    fusion = FusionFalso([[evento]], itens={"200": [{"codigo_erp_produto": "1", "qtd_devolvido": "1"}]})

    _processar(conn, fusion, escrita, avisos=avisos)

    assert escrita.gravados[0][1] == [] and len(avisos) == 1 and "sem sequência" in avisos[0]
    assert _status(conn) == {200: "GRAVADO"}


def test_falha_no_ciss_fica_erro_alerta_uma_vez_e_retenta(conn):
    _romaneio(conn)
    alertas = []
    _processar(conn, FusionFalso([[_evento(100)]]), EscritaFalsa(erro=CissDbUnavailable("SQL0530N")), alertas)
    assert _status(conn) == {100: "ERRO"} and len(alertas) == 1

    _processar(conn, FusionFalso(), EscritaFalsa(erro=CissDbUnavailable("SQL0530N")), alertas)
    assert len(alertas) == 1  # só na primeira falha

    escrita = EscritaFalsa()
    _processar(conn, FusionFalso(), escrita)
    assert _status(conn) == {100: "GRAVADO"} and len(escrita.gravados) == 1


def test_itens_devolvidos_buscados_uma_vez_so(conn):
    _romaneio(conn)
    evento = _evento(200, tipo="9", motivo="556")
    fusion = FusionFalso([[evento]], itens={"200": []})
    _processar(conn, fusion, EscritaFalsa(erro=CissDbUnavailable("x")))
    _processar(conn, fusion, EscritaFalsa())
    assert fusion.itens_pedidos == ["200"]


def test_fusion_fora_do_ar_alerta_e_segue(conn):
    _romaneio(conn)
    alertas = []
    resultado = _processar(conn, FusionFalso(erro=FusionUnavailable()), alertas=alertas)
    assert resultado["recebidos"] == 0 and "obtemEventosPorID" in alertas[0]


def test_teto_de_gravacoes_por_ciclo(conn, monkeypatch):
    monkeypatch.setattr(rastreio_eventos, "LIMITE_GRAVACOES_POR_CICLO", 2)
    _romaneio(conn)
    escrita = EscritaFalsa()
    resultado = _processar(conn, FusionFalso([[_evento(i) for i in range(1, 6)]]), escrita)
    assert len(escrita.gravados) == 2 and resultado["restantes"] == 3
    assert [g[0]["IDEVENTOFUSION"] for g in escrita.gravados] == [1, 2]  # ordem de t15_id


def test_listar_do_romaneio_tem_data_com_fuso(conn):
    _romaneio(conn)
    _processar(conn, FusionFalso([[_evento(100)]]))
    [evento] = rastreio_eventos.listar_do_romaneio(conn, ROMANEIO)
    assert evento["data_hora"] == "2026-11-26T10:53:56-03:00" and evento["status"] == "GRAVADO"


# --- Transporte ----------------------------------------------------------


class _DriverFalso:
    class Error(Exception):
        pass

    def __init__(self, falhar_no=None):
        self.falhar_no = falhar_no
        self.executados, self.commits, self.rollbacks = [], 0, 0

    def connect(self, *_a, **_kw):
        driver = self

        class Cursor:
            def execute(self, sql, params):
                if len(driver.executados) == driver.falhar_no:
                    raise driver.Error("SQL0530N")
                driver.executados.append(sql)

            def close(self):
                pass

        class Conexao:
            def cursor(self):
                return Cursor()

            def commit(self):
                driver.commits += 1

            def rollback(self):
                driver.rollbacks += 1

            def close(self):
                pass

        return Conexao()


class _Db:
    database, host, timeout = "X", "h", 1

    def connection_string(self):
        return "DSN=x"


def _linhas_para_gravar():
    payload = {"ENTREGAS": [{"t32_pedido_original": "616205_PC796273", "t12_ordemEntrega": "2"}]}
    evento = rastreio_eventos.mapear_evento(_evento(200, tipo="9", motivo="556"), payload)
    itens, _ = rastreio_eventos.mapear_itens(evento, [
        {"codigo_erp_produto": "1", "qtd_devolvido": "1"}, {"codigo_erp_produto": "2", "qtd_devolvido": "1"},
    ])
    return evento, itens


def test_evento_e_itens_numa_transacao_so(monkeypatch):
    from integrations import ciss_escrita

    driver = _DriverFalso()
    monkeypatch.setattr(ciss_escrita, "_load_driver", lambda: driver)
    ciss_escrita.CissEscritaClient(_Db()).gravar_evento_fusion_track(*_linhas_para_gravar())

    assert driver.commits == 1 and len(driver.executados) == 3
    assert "EVENTOS_FUSION_TRACK" in driver.executados[0]  # evento antes dos itens (FK)
    assert all("ITENS_DEVOLVIDOS_FUSION_TRACK" in sql for sql in driver.executados[1:])


def test_falha_num_item_desfaz_o_evento_tambem(monkeypatch):
    from integrations import ciss_escrita

    driver = _DriverFalso(falhar_no=2)
    monkeypatch.setattr(ciss_escrita, "_load_driver", lambda: driver)
    with pytest.raises(CissDbUnavailable):
        ciss_escrita.CissEscritaClient(_Db()).gravar_evento_fusion_track(*_linhas_para_gravar())
    assert driver.commits == 0 and driver.rollbacks == 1


def test_fora_do_escopo_volta_quando_o_romaneio_entra_em_cargas(conn):
    escrita = EscritaFalsa()
    _processar(conn, FusionFalso([[_evento(100, t15_dataHora="2026-04-16 11:41:29")]]), escrita)
    assert _status(conn) == {100: "FORA_DO_ESCOPO"}

    _romaneio(conn)  # o romaneio entrou em CARGAS_FUSION_TRACK depois
    _processar(conn, FusionFalso(), escrita)

    assert _status(conn) == {100: "GRAVADO"} and len(escrita.gravados) == 1


def test_pedido_que_saiu_do_romaneio_usa_a_ultima_sequencia_que_teve(conn):
    """Visto em produção: entrega adiada sai do romaneio; o payload mais
    novo não lista mais o pedido, mas o evento dele é deste romaneio."""
    _romaneio(conn, codigo_int=1)  # 616205_PC796273 na sequência 2
    rastreio._registrar_recebidos(conn, [{
        "CODIGO_INT": "2", "TIPO_INT": "T10_ROMANEIO_ROTEIRIZADO", "t10_id": str(ROMANEIO),
        "ENTREGAS": [{"t32_pedido_original": "616300_22160000", "t12_ordemEntrega": "1"}],
    }])
    conn.execute("UPDATE cargas_fusion_int_erp SET status = 'INTERNALIZADO' WHERE codigo_int = 2")
    conn.commit()
    escrita = EscritaFalsa()

    _processar(conn, FusionFalso([[_evento(100, tipo="8", pedido="616205_PC796273")]]), escrita)

    assert escrita.gravados[0][0]["IDENTREGAFUSION"] == 2
