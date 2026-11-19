"""`FusionPortalClient` cacheia a sessão logada (até 1h) em vez de logar a
cada chamada (achado em produção, 2026-11-19: 82 logins em 12 minutos de
uso normal — ver docstring da classe). `fusion-py` (pacote `fusion`) é
dublê aqui: `_FusionPyClient`/`_FusionPyError` são substituídos por mocks,
não o pacote real."""

from unittest.mock import MagicMock, patch

import pytest

from config import FusionPortalConfig
from integrations.fusion_portal import FusionPortalClient, FusionPortalError


@pytest.fixture
def config() -> FusionPortalConfig:
    return FusionPortalConfig(url="https://fusion.test", login="automacao", senha="segredo")


class ErroFusionPy(Exception):
    """Dublê de `fusion.exceptions.FusionError`."""


def _pagina(rows=(), total=0):
    pagina = MagicMock()
    pagina.rows = list(rows)
    pagina.total = total
    return pagina


@pytest.fixture
def cliente_fabricado():
    """Cada chamada a `_FusionPyClient(...)` devolve um novo `MagicMock`,
    guardado em `criados` pra inspecionar quantos logins aconteceram."""
    criados = []

    def fabrica(*args, **kwargs):
        cliente = MagicMock()
        criados.append(cliente)
        return cliente

    with (
        patch("integrations.fusion_portal._FusionPyClient", side_effect=fabrica) as ctor,
        patch("integrations.fusion_portal._FusionPyError", ErroFusionPy),
    ):
        yield ctor, criados


def test_sessao_reusada_entre_chamadas(config, cliente_fabricado):
    _ctor, criados = cliente_fabricado
    cliente = FusionPortalClient(config)
    cliente._com_sessao(lambda c: None)
    cliente._com_sessao(lambda c: None)
    cliente._com_sessao(lambda c: None)

    assert len(criados) == 1
    criados[0].login.assert_called_once_with("automacao", "segredo")


def test_sessao_expirada_reloga(config, cliente_fabricado, monkeypatch):
    _ctor, criados = cliente_fabricado
    cliente = FusionPortalClient(config)

    relogio = iter([1000.0, 1000.5, 1000.5 + FusionPortalClient._TTL_SESSAO_SEGUNDOS + 1])
    monkeypatch.setattr("integrations.fusion_portal.time.monotonic", lambda: next(relogio))

    cliente._com_sessao(lambda c: None)  # login em t=1000.0
    cliente._com_sessao(lambda c: None)  # t=1000.5, ainda dentro do TTL — reusa
    cliente._com_sessao(lambda c: None)  # TTL expirado — reloga

    assert len(criados) == 2
    criados[0].close.assert_called_once()


def test_falha_na_acao_descarta_sessao_e_tenta_de_novo(config, cliente_fabricado):
    _ctor, criados = cliente_fabricado
    cliente = FusionPortalClient(config)

    chamadas = {"n": 0}

    def acao(c):
        chamadas["n"] += 1
        if chamadas["n"] == 1:
            raise ErroFusionPy("sessão encerrada")
        return "ok"

    resultado = cliente._com_sessao(acao)

    assert resultado == "ok"
    assert len(criados) == 2  # sessão original descartada, nova criada
    criados[0].close.assert_called_once()


def test_falha_persistente_vira_fusion_portal_error(config, cliente_fabricado):
    _ctor, criados = cliente_fabricado
    cliente = FusionPortalClient(config)

    def acao(c):
        raise ErroFusionPy("portal fora do ar")

    with pytest.raises(FusionPortalError):
        cliente._com_sessao(acao)

    assert len(criados) == 2  # tentou de novo antes de desistir


def test_falha_no_login_tambem_tenta_de_novo(config, cliente_fabricado):
    ctor, criados = cliente_fabricado
    cliente = FusionPortalClient(config)

    def fabrica_com_falha_no_primeiro_login(*args, **kwargs):
        c = MagicMock()
        if len(criados) == 0:
            c.login.side_effect = ErroFusionPy("timeout no login")
        criados.append(c)
        return c

    ctor.side_effect = fabrica_com_falha_no_primeiro_login

    resultado = cliente._com_sessao(lambda c: "ok")

    assert resultado == "ok"
    assert len(criados) == 2


def test_metodos_publicos_usam_sessao_cacheada(config, cliente_fabricado):
    """Smoke test de um método público de verdade (não só `_com_sessao`
    direto) — `buscar_motoristas`, chamado duas vezes, loga uma só. O
    primeiro call cria a sessão (e com ela o mock), então configura
    `buscar_motoristas` nele antes de chamar de verdade."""
    ctor, criados = cliente_fabricado
    cliente = FusionPortalClient(config)

    def fabrica_configurada(*args, **kwargs):
        c = MagicMock()
        c.buscar_motoristas.return_value = _pagina(rows=[])
        criados.append(c)
        return c

    ctor.side_effect = fabrica_configurada

    resultado1 = cliente.buscar_motoristas("fulano")
    resultado2 = cliente.buscar_motoristas("fulano")

    assert resultado1 == []
    assert resultado2 == []
    assert len(criados) == 1
