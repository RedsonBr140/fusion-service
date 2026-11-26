"""integrations/discord.py (embeds, fila, rate limit) e
integrations/auditoria.py (toda ação → canal de atividade). Sem rede:
`responses` intercepta o POST do webhook."""

import json

import pytest
import responses

from integrations import auditoria, discord

URL = "https://discord.com/api/webhooks/123/TOKEN-SECRETO"


def test_embed_respeita_limites_do_discord():
    embed = discord.montar_embed(
        "x" * 500, "d" * 5000, campos=[("n" * 300, "v" * 2000, True)] * 30
    )
    assert len(embed["title"]) == 256
    assert len(embed["description"]) == 4096
    assert len(embed["fields"]) == 25
    assert len(embed["fields"][0]["name"]) == 256 and len(embed["fields"][0]["value"]) == 1024
    assert embed["author"]["name"] and embed["footer"]["text"].startswith("fusion-service")
    assert embed["timestamp"].endswith("+00:00")


def test_campo_vazio_vira_travessao():
    embed = discord.montar_embed("t", campos=[("a", None, True), ("b", "", True)])
    assert [c["value"] for c in embed["fields"]] == ["—", "—"]


def test_erro_e_vermelho_com_sirene(monkeypatch):
    enviados = []
    monkeypatch.setattr(discord._despachante, "enfileirar", lambda url, embed, extra: enviados.append((url, embed, extra)))
    discord.erro(URL, "Algo falhou", "detalhe")
    url, embed, extra = enviados[0]
    assert url == URL and embed["color"] == discord.COR_ERRO and embed["title"].startswith("🚨")
    assert discord._corpo_extra(extra) == {"content": "@everyone", "allowed_mentions": {"parse": ["everyone"]}}


def test_aviso_vai_pro_canal_de_erros_amarelo_e_sem_mencao(monkeypatch):
    enviados = []
    monkeypatch.setattr(discord._despachante, "enfileirar", lambda url, embed, extra: enviados.append((url, embed, extra)))
    discord.aviso(URL, "Olhar isto")
    url, embed, extra = enviados[0]
    assert url == URL and embed["color"] == discord.COR_AVISO and embed["title"].startswith("⚠️")
    assert discord._corpo_extra(extra) == {"allowed_mentions": {"parse": []}}  # menção desligada


def test_atividade_nunca_menciona(monkeypatch):
    enviados = []
    monkeypatch.setattr(discord._despachante, "enfileirar", lambda url, embed, extra: enviados.append(extra))
    discord.configurar_atividade(URL)
    try:
        discord.atividade("Ação")
    finally:
        discord.configurar_atividade(None)
    assert discord._corpo_extra(enviados[0]) == {"allowed_mentions": {"parse": []}}


def test_sem_webhook_so_loga(monkeypatch, caplog):
    enviados = []
    monkeypatch.setattr(discord._despachante, "enfileirar", lambda *a: enviados.append(a))
    discord.configurar_atividade(None)
    with caplog.at_level("INFO"):
        discord.atividade("Ação qualquer")
        discord.erro(None, "Erro qualquer")
    assert enviados == []
    assert "Ação qualquer" in caplog.text and "Erro qualquer" in caplog.text


@responses.activate
def test_despachante_agrupa_ate_dez_embeds_por_mensagem():
    responses.add(responses.POST, URL, status=204)
    d = discord._Despachante()
    lote = [discord.montar_embed(f"e{i}") for i in range(23)]
    # Enfileira tudo antes da thread acordar, pra agrupar de verdade.
    for embed in lote:
        d._fila.put((URL, discord._SEM_MENCAO, embed))
    d._garantir_thread()
    d.esvaziar()
    import time; time.sleep(0.3)
    tamanhos = [len(json.loads(c.request.body)["embeds"]) for c in responses.calls]
    assert sum(tamanhos) == 23 and max(tamanhos) <= 10


@responses.activate
def test_429_espera_retry_after_e_reenvia(monkeypatch):
    responses.add(responses.POST, URL, status=429, json={"retry_after": 0.01})
    responses.add(responses.POST, URL, status=204)
    esperas = []
    monkeypatch.setattr(discord.time, "sleep", lambda s: esperas.append(s))
    discord._Despachante()._enviar(URL, [discord.montar_embed("x")])
    assert len(responses.calls) == 2 and esperas == [0.5]  # piso de 0.5s


@responses.activate
def test_falha_nunca_loga_url(caplog):
    responses.add(responses.POST, URL, status=404)
    discord._postar(URL, {"embeds": []})
    assert "TOKEN-SECRETO" not in caplog.text and "HTTP 404" in caplog.text


# --- auditoria -------------------------------------------------------------


class Falso:
    def __init__(self, resposta=None, erro=None):
        self.resposta, self.erro = resposta, erro

    def save_entrega_servico(self, entregas):
        if self.erro:
            raise self.erro
        return self.resposta

    def get_int_erp(self, limite=50):
        return self.resposta

    def detalhe_entrega(self, pedido):
        return {"status": "1"}

    def upsert_entrega_fusion_track(self, linha):
        return None


@pytest.fixture
def atividades(monkeypatch):
    lista = []
    monkeypatch.setattr(
        auditoria.discord, "atividade",
        lambda titulo, descricao=None, cor=None, campos=None: lista.append({"titulo": titulo, "cor": cor, "campos": dict((n, v) for n, v, _ in campos or [])}),
    )
    return lista


def test_escrita_no_fusion_vira_atividade_verde(atividades):
    cliente = auditoria.ClienteAuditado(Falso(resposta={"success": ["OK"], "errors": []}))
    cliente.save_entrega_servico([{"pedido_erp": "616205_PC796273", "empresa_fat": "2", "itens": [1, 2]}])
    a = atividades[0]
    assert a["titulo"].startswith("📦") and a["cor"] == auditoria.discord.COR_SUCESSO
    assert "616205_PC796273" in a["campos"]["Pedidos"]
    assert a["campos"]["Por"] == "automático (scheduler)"


def test_fusion_recusando_sem_excecao_fica_amarelo(atividades):
    cliente = auditoria.ClienteAuditado(Falso(resposta={"msg": "cliente nao localizado"}))
    cliente.save_entrega_servico([{"pedido_erp": "1_2"}])
    assert atividades[0]["cor"] == auditoria.discord.COR_AVISO
    assert "recusado" in atividades[0]["titulo"]


def test_excecao_vira_atividade_vermelha_e_propaga(atividades):
    cliente = auditoria.ClienteAuditado(Falso(erro=RuntimeError("caiu")))
    with pytest.raises(RuntimeError):
        cliente.save_entrega_servico([{"pedido_erp": "1_2"}])
    assert atividades[0]["cor"] == auditoria.discord.COR_ERRO
    assert atividades[0]["campos"]["Falhou"] == "`RuntimeError`"


def test_leitura_e_fila_vazia_nao_sao_acao(atividades):
    cliente = auditoria.ClienteAuditado(Falso(resposta=[]))
    assert cliente.get_int_erp() == []
    assert cliente.detalhe_entrega("1_2") == {"status": "1"}
    assert atividades == []


def test_fila_consumida_e_gravacao_no_ciss_sao_acao(atividades):
    cliente = auditoria.ClienteAuditado(
        Falso(resposta=[{"CODIGO_INT": "1", "TIPO_INT": "T10_ROMANEIO_MONTAGEM_CARGA", "t10_id": "9"}])
    )
    cliente.get_int_erp()
    cliente.upsert_entrega_fusion_track({"IDCARGAFUSION": 9, "NUMNOTA": 167888, "IDPLANILHA": 1, "STATUSENTREGA": 7})
    assert [a["titulo"][:1] for a in atividades] == ["📥", "📍"]
    assert atividades[0]["campos"]["Tipos"] == "MONTAGEM_CARGA: 1"
    assert "167888" in atividades[1]["campos"]["NF"]


def test_auditoria_quebrada_nunca_derruba_a_acao(monkeypatch):
    monkeypatch.setattr(auditoria.discord, "atividade", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("discord")))
    cliente = auditoria.ClienteAuditado(Falso(resposta={"success": "1"}))
    assert cliente.save_entrega_servico([{"pedido_erp": "1_2"}]) == {"success": "1"}


@responses.activate
def test_erro_e_aviso_nunca_dividem_mensagem():
    """@everyone vale pra mensagem inteira — um aviso agrupado junto de um
    erro pingaria todo mundo por causa do aviso."""
    responses.add(responses.POST, URL, status=204)
    d = discord._Despachante()
    d._fila.put((URL, discord._COM_MENCAO, discord.montar_embed("erro 1")))
    d._fila.put((URL, discord._SEM_MENCAO, discord.montar_embed("aviso 1")))
    d._fila.put((URL, discord._COM_MENCAO, discord.montar_embed("erro 2")))
    d._garantir_thread()
    d.esvaziar()
    import time; time.sleep(0.3)
    corpos = [json.loads(c.request.body) for c in responses.calls]
    for corpo in corpos:
        titulos = {e["title"].split()[0] for e in corpo["embeds"]}
        assert len(titulos) == 1  # só erro ou só aviso
        assert (corpo.get("content") == "@everyone") == (titulos == {"erro"})
    assert sum(len(c["embeds"]) for c in corpos) == 3
