"""Login direto (allow-list antes do CISS) — único caminho de entrada."""

from __future__ import annotations

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


# --- Decorator / rota protegida -----------------------------------------


def test_me_sem_token_devolve_401(client):
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_me_com_token_valido_devolve_usuario(client, config):
    token = tokens.criar_token_sessao(ciss_code="42", name="Fulano", secret=config.secret_key)
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.get_json()["user"] == {"ciss_code": "42", "name": "Fulano"}


# --- Allow-list (conceder/listar usuarios_locais) -----------------------


def _auth_header(config, *, ciss_code="42", name="Quem Concede"):
    token = tokens.criar_token_sessao(ciss_code=ciss_code, name=name, secret=config.secret_key)
    return {"Authorization": f"Bearer {token}"}


def test_conceder_usuario_sem_token_devolve_401(client):
    resp = client.post("/api/usuarios", json={"username": "novo", "ciss_code": "7", "name": "Novo"})
    assert resp.status_code == 401


def test_conceder_usuario_sem_campos_obrigatorios_devolve_400(client, config):
    resp = client.post("/api/usuarios", json={"username": "novo"}, headers=_auth_header(config))
    assert resp.status_code == 400


def test_conceder_usuario_novo_entra_na_allowlist_e_pode_logar(app, client, config):
    resp = client.post(
        "/api/usuarios",
        json={"username": "novo", "ciss_code": "7", "name": "Pessoa Nova"},
        headers=_auth_header(config),
    )
    assert resp.status_code == 201
    assert resp.get_json()["usuario"] == {
        "username": "novo",
        "ciss_code": "7",
        "name": "Pessoa Nova",
        "active": True,
    }

    with app.app_context():
        from auth import service

        resultado = service.login("7", "qualquer", authenticate=_autenticar_ok)
    assert resultado["user"] == {"ciss_code": "7", "name": "Pessoa Nova"}


def test_conceder_usuario_existente_reativa_e_atualiza_dados(client, config):
    criar_usuario_local(config, ciss_code="8", name="Nome Antigo", active=0)

    resp = client.post(
        "/api/usuarios",
        json={"username": "8", "ciss_code": "8", "name": "Nome Novo"},
        headers=_auth_header(config),
    )
    assert resp.status_code == 201
    assert resp.get_json()["usuario"]["name"] == "Nome Novo"
    assert resp.get_json()["usuario"]["active"] is True


def test_conceder_usuario_com_ciss_code_de_outro_username_devolve_409(client, config):
    criar_usuario_local(config, ciss_code="9", name="Alguem")

    resp = client.post(
        "/api/usuarios",
        json={"username": "outro-username", "ciss_code": "9", "name": "Outra Pessoa"},
        headers=_auth_header(config),
    )
    assert resp.status_code == 409


def test_listar_usuarios_sem_token_devolve_401(client):
    resp = client.get("/api/usuarios")
    assert resp.status_code == 401


def test_listar_usuarios_devolve_allowlist(client, config):
    criar_usuario_local(config, ciss_code="10", name="Fulano")
    resp = client.get("/api/usuarios", headers=_auth_header(config))
    assert resp.status_code == 200
    usuarios = resp.get_json()["usuarios"]
    assert any(u["ciss_code"] == "10" and u["name"] == "Fulano" for u in usuarios)
