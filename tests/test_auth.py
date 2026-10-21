"""Login direto (allow-list antes do CISS) e troca de token do meta-natal."""

from __future__ import annotations

import time

import pytest

from auth import tokens
from database import connect
from integrations.ciss_auth import CissUnavailable, InvalidCredentials


def criar_usuario_local(config, ciss_code="123", name="Fulano", active=1):
    conn = connect(config.database_path)
    with conn:
        conn.execute(
            "INSERT INTO usuarios_locais (username, ciss_code, name, active) VALUES (?, ?, ?, ?)",
            (ciss_code, ciss_code, name, active),
        )
    conn.close()


class IdentidadeFalsa:
    def __init__(self, enabled=True):
        self.enabled = enabled


def _autenticar_ok(username, password):
    return IdentidadeFalsa()


def _autenticar_credenciais_invalidas(username, password):
    raise InvalidCredentials


def _autenticar_ciss_fora_do_ar(username, password):
    raise CissUnavailable


# --- Login direto -----------------------------------------------------


def test_login_usuario_fora_da_allowlist_e_rejeitado_sem_chamar_ciss(app, config):
    chamou_ciss = {"valor": False}

    def authenticate(username, password):
        chamou_ciss["valor"] = True
        return IdentidadeFalsa()

    with app.app_context():
        from auth import service

        with pytest.raises(service.LoginRejected):
            service.login("quem-nao-existe", "qualquer", authenticate=authenticate)

    assert chamou_ciss["valor"] is False


def test_login_usuario_inativo_e_rejeitado_sem_chamar_ciss(app, config):
    criar_usuario_local(config, ciss_code="999", active=0)
    chamou_ciss = {"valor": False}

    def authenticate(username, password):
        chamou_ciss["valor"] = True
        return IdentidadeFalsa()

    with app.app_context():
        from auth import service

        with pytest.raises(service.LoginRejected):
            service.login("999", "qualquer", authenticate=authenticate)

    assert chamou_ciss["valor"] is False


def test_login_credenciais_invalidas_no_ciss_e_rejeitado(app, config):
    criar_usuario_local(config, ciss_code="42")

    with app.app_context():
        from auth import service

        with pytest.raises(service.LoginRejected):
            service.login("42", "senha-errada", authenticate=_autenticar_credenciais_invalidas)


def test_login_ciss_fora_do_ar_vira_auth_unavailable(app, config):
    criar_usuario_local(config, ciss_code="42")

    with app.app_context():
        from auth import service

        with pytest.raises(service.AuthUnavailable):
            service.login("42", "senha", authenticate=_autenticar_ciss_fora_do_ar)


def test_login_bem_sucedido_devolve_token_e_usuario(app, config):
    criar_usuario_local(config, ciss_code="42", name="Fulano")

    with app.app_context():
        from auth import service

        resultado = service.login("42", "senha", authenticate=_autenticar_ok)

    assert resultado["user"] == {"ciss_code": "42", "name": "Fulano"}
    claims = tokens.verificar(resultado["token"], config.secret_key)
    assert claims["ciss_code"] == "42"


def test_login_route_sem_credenciais_devolve_401(client):
    resp = client.post("/api/login", json={})
    assert resp.status_code == 401


# --- Troca de token (meta-natal -> fusion-service) ---------------------


def _token_meta_natal(config, *, ciss_code="42", name="Fulano", exp=None, secret=None):
    claims = {
        "ciss_code": ciss_code,
        "name": name,
        "exp": exp if exp is not None else time.time() + 300,
    }
    return tokens.emitir(claims, secret or config.fusion_token_secret)


def test_exchange_token_valido_e_na_allowlist_emite_sessao(app, config):
    criar_usuario_local(config, ciss_code="42", name="Fulano")
    token = _token_meta_natal(config)

    with app.app_context():
        from auth import service

        resultado = service.exchange(token)

    assert resultado["user"] == {"ciss_code": "42", "name": "Fulano"}


def test_exchange_token_expirado_e_rejeitado(app, config):
    criar_usuario_local(config, ciss_code="42")
    token = _token_meta_natal(config, exp=time.time() - 1)

    with app.app_context():
        from auth import service

        with pytest.raises(service.ExchangeRejected):
            service.exchange(token)


def test_exchange_token_com_assinatura_adulterada_e_rejeitado(app, config):
    criar_usuario_local(config, ciss_code="42")
    token = _token_meta_natal(config, secret="segredo-errado")

    with app.app_context():
        from auth import service

        with pytest.raises(service.ExchangeRejected):
            service.exchange(token)


def test_exchange_ciss_code_fora_da_allowlist_e_rejeitado(app, config):
    token = _token_meta_natal(config, ciss_code="nao-cadastrado")

    with app.app_context():
        from auth import service

        with pytest.raises(service.ExchangeRejected):
            service.exchange(token)


def test_exchange_route_devolve_401_para_token_invalido(client):
    resp = client.post("/api/token/exchange", json={"token": "lixo"})
    assert resp.status_code == 401


# --- Decorator / rota protegida -----------------------------------------


def test_me_sem_token_devolve_401(client):
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_me_com_token_valido_devolve_usuario(client, config):
    token = tokens.criar_token_sessao(ciss_code="42", name="Fulano", secret=config.secret_key)
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.get_json()["user"] == {"ciss_code": "42", "name": "Fulano"}
