"""Testes do ciclo automático de envio de pré-carga solta
(cargas_fusion/auto_envio.py). CISS, Fusion e Discord são sempre dublês;
o lock usa `fakeredis` — nenhum teste aqui toca rede ou um Redis de
verdade.
"""

from datetime import datetime

import fakeredis
import pytest

from cargas_fusion import auto_envio, service
from database import connect, init_database

# `corte` vem de Config.auto_envio_pre_carga_corte (configurável via
# AUTO_ENVIO_PRE_CARGA_CORTE) — no passado aqui pra exercitar o
# processamento de verdade; ver test_antes_do_corte_nunca_tenta_o_lock
# pro teste do guard com o default real de produção (2026-10-28 08:00).
CORTE_NO_PASSADO = datetime(2000, 1, 1)


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "auto_envio.db"
    init_database(db_path)
    conexao = connect(db_path)
    yield conexao
    conexao.close()


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()


def executar(conn, redis_client, ciss_client, fusion_client, fusion_portal_client, **kwargs):
    """Wrapper só pra não repetir `janela_horas`/`corte`/`lock_timeout_segundos`
    em toda chamada — qualquer teste pode sobrescrever via kwargs."""
    kwargs.setdefault("janela_horas", 24)
    kwargs.setdefault("corte", CORTE_NO_PASSADO)
    kwargs.setdefault("discord_webhook_url", None)
    kwargs.setdefault("lock_timeout_segundos", 60)
    return auto_envio.executar_ciclo(
        conn, redis_client, ciss_client, fusion_client, fusion_portal_client, **kwargs
    )


class CissClientFalso:
    """Dublê mínimo — só os métodos que `executar_ciclo` chama."""

    def __init__(self, *, pre_cargas=(), itens_por_precarga=None, detalhe_por_precarga=None, clientes=None):
        self._pre_cargas = list(pre_cargas)
        self._itens_por_precarga = itens_por_precarga or {}
        self._detalhe_por_precarga = detalhe_por_precarga or {}
        self._clientes = clientes or {}

    def get_pre_cargas(self, data_inicial, data_final, limite=100, id_empresa=None, apenas_fusion=True):
        return self._pre_cargas

    def get_itens_pre_carga(self, id_precarga):
        return self._itens_por_precarga.get(id_precarga, [])

    def get_pre_carga_detalhe(self, id_empresa, id_precarga):
        return self._detalhe_por_precarga.get((id_empresa, id_precarga))

    def get_cliente_fusion(self, id_clifor):
        return self._clientes.get(id_clifor)


class FusionClienteFalso:
    def __init__(self, *, resultado=None, erro=None):
        self._resultado = resultado if resultado is not None else {"success": ["OK"], "errors": []}
        self._erro = erro
        self.chamadas = []

    def save_entrega_servico(self, entregas):
        self.chamadas.append(entregas)
        if self._erro is not None:
            raise self._erro
        return self._resultado

    def send_clientes(self, clientes):
        return {"success": ["OK"], "errors": []}


def _item(id_planilhadocori, id_orcamento, *, idclifor=1):
    return {
        "IDPLANILHADOCORI": id_planilhadocori,
        "IDORCAMENTO": id_orcamento,
        "IDSUBPRODUTO": 9397,
        "IDCLIFOR": idclifor,
        "IDCLIFORENTREGA": idclifor,
        "VALTOTLIQUIDO": 100.0,
        "PESOBRUTO": 0.0,
        "DESCRCOMPRODUTO": "ITEM TESTE",
    }


def _pre_carga(id_empresa, id_precarga, descricao="TESTE"):
    return {"IDEMPRESA": id_empresa, "IDPRECARGA": id_precarga, "DESCRPRECARGA": descricao}


# --- pausa -------------------------------------------------------------


def test_pausar_bloqueia_o_ciclo_sem_tentar_o_lock(conn, redis_client):
    auto_envio.pausar(conn)
    client = CissClientFalso(pre_cargas=[_pre_carga(1, 100)])

    resultado = executar(conn, redis_client, client, None, None)

    assert resultado == {"status": "pausado"}
    # Nunca sequer tentou o lock — outro worker pausado também não disputaria nada.
    assert redis_client.get(auto_envio.NOME_LOCK) is None


def test_retomar_depois_de_pausar_volta_a_executar(conn, redis_client):
    auto_envio.pausar(conn)
    auto_envio.retomar(conn)
    client = CissClientFalso(pre_cargas=[])

    resultado = executar(conn, redis_client, client, None, None)

    assert resultado["status"] == "executado"


def test_antes_do_corte_nunca_tenta_o_lock(conn, redis_client):
    """Default real de `Config.auto_envio_pre_carga_corte` (2026-10-28
    08:00) — sem o guard, `max(agora - janela, corte)` viraria `corte`
    (futuro), e `get_pre_cargas` receberia início > fim: sem erro, só
    uma janela invertida que nunca bate em nada — mas bateria no CISS a
    cada tick à toa até o corte passar de verdade. O guard evita isso, e
    nem chega a tentar o lock (não há nada pra fazer)."""
    client = CissClientFalso(pre_cargas=[_pre_carga(1, 100)])

    resultado = executar(conn, redis_client, client, None, None, corte=datetime(2026, 10, 28, 8, 0, 0))

    assert resultado == {"status": "antes_do_corte"}
    assert redis_client.get(auto_envio.NOME_LOCK) is None


def test_sem_ciss_configurado_nao_crasha(conn, redis_client):
    """Ambiente de teste local pode ligar o ciclo sem CISS configurado só
    pra exercitar o lock/scheduler — não deveria crashar."""
    resultado = executar(conn, redis_client, None, None, None)

    assert resultado == {"status": "ciss_nao_configurado"}
    assert redis_client.get(auto_envio.NOME_LOCK) is None


# --- lock ----------------------------------------------------------------


def test_lock_ja_adquirido_pula_o_tick(conn, redis_client):
    lock = redis_client.lock(auto_envio.NOME_LOCK, timeout=60)
    assert lock.acquire(blocking=False)

    client = CissClientFalso(pre_cargas=[_pre_carga(1, 100)])
    resultado = executar(conn, redis_client, client, None, None)

    assert resultado["status"] == "pulado"
    lock.release()


def test_lock_libera_sozinho_apos_ttl_mesmo_sem_release(conn, redis_client):
    """Simula um worker que travou no meio do ciclo: o TTL expira e o
    próximo tick consegue adquirir de novo, sem intervenção manual."""
    lock_travado = redis_client.lock(auto_envio.NOME_LOCK, timeout=1)
    assert lock_travado.acquire(blocking=False)
    import time

    time.sleep(1.2)

    client = CissClientFalso(pre_cargas=[])
    resultado = executar(conn, redis_client, client, None, None)

    assert resultado["status"] == "executado"


# --- multi-pedido: nunca enviado, sempre contado -------------------------


def test_pre_carga_multi_pedido_nunca_chama_fusion(conn, redis_client):
    itens = [_item(1, 10), _item(2, 20)]  # dois IDPLANILHADOCORI => 2 pedidos
    client = CissClientFalso(
        pre_cargas=[_pre_carga(3, 500)],
        itens_por_precarga={500: itens},
    )
    fusion = FusionClienteFalso()

    resultado = executar(conn, redis_client, client, fusion, None)

    assert fusion.chamadas == []
    assert resultado["candidatas"] == 0
    resumo = auto_envio.resumo_multi_pedido(conn)
    assert resumo["total"] == 1
    assert resumo["por_empresa"] == [{"id_empresa": 3, "qtd": 1}]


def test_pre_carga_multi_pedido_vista_de_novo_nao_duplica_registro(conn, redis_client):
    itens = [_item(1, 10), _item(2, 20)]
    client = CissClientFalso(pre_cargas=[_pre_carga(3, 500)], itens_por_precarga={500: itens})

    for _ in range(3):
        executar(conn, redis_client, client, FusionClienteFalso(), None)

    assert auto_envio.resumo_multi_pedido(conn)["total"] == 1


def test_pre_carga_multi_pedido_notifica_discord_so_na_primeira_vez(conn, redis_client, monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        "cargas_fusion.auto_envio.discord.notificar", lambda url, msg: chamadas.append(msg)
    )
    itens = [_item(1, 10), _item(2, 20)]
    client = CissClientFalso(pre_cargas=[_pre_carga(3, 500)], itens_por_precarga={500: itens})

    for _ in range(2):
        executar(conn, redis_client, client, FusionClienteFalso(), None, discord_webhook_url="https://discord.test/x")

    assert len(chamadas) == 1
    assert "500" in chamadas[0] or "3" in chamadas[0]


# --- pedido único: enviado de verdade ------------------------------------


def test_pre_carga_pedido_unico_envia_e_marca_enviada(conn, redis_client):
    itens = [_item(1, 10)]
    client = CissClientFalso(
        pre_cargas=[_pre_carga(3, 600)],
        itens_por_precarga={600: itens},
        detalhe_por_precarga={(3, 600): _pre_carga(3, 600)},
        clientes={1: {"CODIGO_CLIENTE": "C1", "RAZAO_CLIENTE": "FULANO"}},
    )
    fusion = FusionClienteFalso()

    resultado = executar(conn, redis_client, client, fusion, None)

    assert resultado["candidatas"] == 1
    assert resultado["enviadas"] == 1
    assert len(fusion.chamadas) == 1
    assert service.pre_cargas_ja_enviadas(conn) == {(3, 600)}


def test_pre_carga_ja_enviada_nunca_e_reprocessada(conn, redis_client):
    itens = [_item(1, 10)]
    client = CissClientFalso(
        pre_cargas=[_pre_carga(3, 600)],
        itens_por_precarga={600: itens},
        detalhe_por_precarga={(3, 600): _pre_carga(3, 600)},
        clientes={1: {"CODIGO_CLIENTE": "C1", "RAZAO_CLIENTE": "FULANO"}},
    )
    fusion = FusionClienteFalso()
    executar(conn, redis_client, client, fusion, None)

    resultado = executar(conn, redis_client, client, fusion, None)

    assert resultado["candidatas"] == 0
    assert len(fusion.chamadas) == 1  # não chamou de novo


def test_falha_no_envio_notifica_discord_so_na_primeira_tentativa(conn, redis_client, monkeypatch):
    chamadas = []
    monkeypatch.setattr(
        "cargas_fusion.auto_envio.discord.notificar", lambda url, msg: chamadas.append(msg)
    )
    itens = [_item(1, 10)]
    client = CissClientFalso(
        pre_cargas=[_pre_carga(3, 600)],
        itens_por_precarga={600: itens},
        detalhe_por_precarga={(3, 600): _pre_carga(3, 600)},
        clientes={1: {"CODIGO_CLIENTE": "C1", "RAZAO_CLIENTE": "FULANO"}},
    )
    fusion = FusionClienteFalso(resultado={"msg": "erro qualquer"})

    for _ in range(2):
        executar(conn, redis_client, client, fusion, None, discord_webhook_url="https://discord.test/x")

    assert len(chamadas) == 1
