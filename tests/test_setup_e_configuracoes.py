"""Setup inicial (auth/setup.py), papel de admin e configuração em banco
(configuracoes.py / auth/admin_routes.py)."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import configuracoes
from auth import setup, tokens
from config import ConfigError, load_config
from database import connect, init_database
from integrations.ciss_auth import CissUnavailable, InvalidCredentials


def _token_setup(config) -> str:
    return setup.caminho_token(config.database_path).read_text(encoding="utf-8").strip()


def _identidade(user_id=501, enabled=True):
    return SimpleNamespace(user_id=user_id, enabled=enabled, username="maria")


@pytest.fixture
def ciss_ok(monkeypatch):
    chamadas = []

    def authenticate(username, password, *, api_url, ca_bundle=None):
        chamadas.append({"username": username, "api_url": api_url, "ca_bundle": ca_bundle})
        return _identidade()

    monkeypatch.setattr("integrations.ciss_auth.authenticate", authenticate)
    return chamadas


def _concluir(client, config, **extra):
    corpo = {
        "token": _token_setup(config),
        "ciss_api_url": "https://ciss.novo/",
        "username": "Maria",
        "password": "segredo",
        **extra,
    }
    return client.post("/api/setup", json=corpo)


def _header_admin(config, username="admin1"):
    conn = connect(config.database_path)
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO usuarios_locais (username, ciss_code, name, active, admin) VALUES (?, ?, ?, 1, 1)",
            (username, f"c-{username}", username),
        )
    conn.close()
    token = tokens.criar_token_sessao(username=username, ciss_code=f"c-{username}", name=username, secret=config.secret_key)
    return {"Authorization": f"Bearer {token}"}


# --- Setup inicial --------------------------------------------------------


def test_boot_sem_admin_grava_token_em_arquivo_e_nunca_no_log(config, caplog):
    from app import create_app

    with caplog.at_level(logging.INFO):
        create_app(config)
    token = _token_setup(config)
    assert len(token) >= 32
    assert token not in caplog.text
    assert "setup-token.txt" in caplog.text


def test_status_do_setup_aberto(client):
    resp = client.get("/api/setup")
    assert resp.get_json()["necessario"] is True


def test_setup_com_token_errado_devolve_403_sem_chamar_ciss(client, config, ciss_ok):
    resp = _concluir(client, config, token="errado")
    assert resp.status_code == 403
    assert ciss_ok == []


def test_setup_completo_cria_admin_grava_url_e_fecha(client, config, ciss_ok):
    resp = _concluir(client, config, name="Maria Silva")
    assert resp.status_code == 201
    corpo = resp.get_json()
    assert corpo["user"]["admin"] is True
    assert corpo["user"]["username"] == "maria"
    assert ciss_ok[0]["api_url"] == "https://ciss.novo"

    conn = connect(config.database_path)
    assert configuracoes.ler(conn, "CISS_API_URL") == "https://ciss.novo"
    linha = conn.execute("SELECT ciss_code, name, admin FROM usuarios_locais WHERE username = 'maria'").fetchone()
    assert (linha["ciss_code"], linha["name"], linha["admin"]) == ("501", "Maria Silva", 1)
    assert conn.execute("SELECT COUNT(*) FROM setup_inicial").fetchone()[0] == 0
    conn.close()
    assert not setup.caminho_token(config.database_path).exists()

    # O token de sessão devolvido já é de admin.
    headers = {"Authorization": f"Bearer {corpo['token']}"}
    assert client.get("/api/admin/configuracoes", headers=headers).status_code == 200
    # E o setup fechou.
    assert client.get("/api/setup").get_json()["necessario"] is False
    assert _concluir_sem_arquivo(client).status_code == 409


def _concluir_sem_arquivo(client):
    return client.post(
        "/api/setup",
        json={"token": "x", "ciss_api_url": "https://a", "username": "a", "password": "b"},
    )


def test_setup_promove_usuario_que_ja_estava_na_allowlist(client, config, ciss_ok):
    """Banco migrado de antes dos admins: a pessoa já existe, vira admin."""
    conn = connect(config.database_path)
    with conn:
        conn.execute("INSERT INTO usuarios_locais (username, ciss_code, name) VALUES ('maria', '77', 'Maria')")
    conn.close()
    assert _concluir(client, config).status_code == 201
    conn = connect(config.database_path)
    linhas = conn.execute("SELECT ciss_code, admin FROM usuarios_locais").fetchall()
    conn.close()
    assert [(l["ciss_code"], l["admin"]) for l in linhas] == [("77", 1)]


def test_setup_com_credencial_errada_nao_consome_o_token(client, config, monkeypatch):
    def recusa(*args, **kwargs):
        raise InvalidCredentials

    monkeypatch.setattr("integrations.ciss_auth.authenticate", recusa)
    assert _concluir(client, config).status_code == 401
    assert client.get("/api/setup").get_json()["necessario"] is True


def test_setup_com_ciss_fora_do_ar_devolve_503(client, config, monkeypatch):
    def fora(*args, **kwargs):
        raise CissUnavailable

    monkeypatch.setattr("integrations.ciss_auth.authenticate", fora)
    assert _concluir(client, config).status_code == 503


def test_setup_com_url_invalida_devolve_400(client, config, ciss_ok):
    assert _concluir(client, config, ciss_api_url="ciss.sem.esquema").status_code == 400
    assert ciss_ok == []


def test_login_depois_do_setup_usa_url_do_banco_sem_reiniciar(app, client, config, ciss_ok):
    _concluir(client, config)
    with app.test_request_context():
        from auth import service

        service.login("maria", "segredo")
    assert ciss_ok[-1]["api_url"] == "https://ciss.novo"


# --- Admin ------------------------------------------------------------------


def test_nao_admin_recebe_403_nas_rotas_de_admin(client, config):
    conn = connect(config.database_path)
    with conn:
        conn.execute("INSERT INTO usuarios_locais (username, ciss_code, name) VALUES ('comum', '5', 'Comum')")
    conn.close()
    token = tokens.criar_token_sessao(username="comum", ciss_code="5", name="Comum", secret=config.secret_key)
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/admin/configuracoes", headers=headers).status_code == 403
    assert client.get("/api/usuarios", headers=headers).status_code == 403
    assert client.post("/api/usuarios", json={}, headers=headers).status_code == 403


def test_nao_da_pra_tirar_o_ultimo_admin(client, config):
    headers = _header_admin(config)
    resp = client.patch("/api/usuarios/admin1", json={"admin": False}, headers=headers)
    assert resp.status_code == 409
    resp = client.patch("/api/usuarios/admin1", json={"active": False}, headers=headers)
    assert resp.status_code == 409


def test_admin_promove_outro_e_depois_pode_sair(client, config):
    headers = _header_admin(config)
    _header_admin(config, "admin2")
    assert client.patch("/api/usuarios/admin1", json={"admin": False}, headers=headers).status_code == 200
    # Papel conferido ao vivo: o mesmo token já não é admin.
    assert client.get("/api/admin/configuracoes", headers=headers).status_code == 403


# --- Configuração em banco ------------------------------------------------


def test_segredo_nunca_sai_na_api(client, config):
    headers = _header_admin(config)
    resp = client.put(
        "/api/admin/configuracoes",
        json={"valores": {"FUSION_PORTAL_URL": "https://p", "FUSION_PORTAL_LOGIN": "l", "FUSION_PORTAL_SENHA": "s3nh4"}},
        headers=headers,
    )
    assert resp.status_code == 200
    assert "s3nh4" not in resp.get_data(as_text=True)
    corpo = client.get("/api/admin/configuracoes", headers=headers).get_data(as_text=True)
    assert "s3nh4" not in corpo
    conn = connect(config.database_path)
    historico = conn.execute(
        "SELECT valor_anterior, valor_novo FROM configuracoes_historico WHERE chave = 'FUSION_PORTAL_SENHA'"
    ).fetchone()
    conn.close()
    assert (historico["valor_anterior"], historico["valor_novo"]) == (None, None)


def test_grupo_pela_metade_e_recusado_com_400(client, config):
    headers = _header_admin(config)
    resp = client.put(
        "/api/admin/configuracoes", json={"valores": {"FUSION_PORTAL_URL": "https://p"}}, headers=headers
    )
    assert resp.status_code == 400
    assert "FUSION_PORTAL_LOGIN" in resp.get_json()["error"]


def test_flag_perigosa_exige_confirmacao_e_avisa_no_discord(client, config, monkeypatch):
    avisos = []
    monkeypatch.setattr("integrations.discord.aviso", lambda *a, **k: avisos.append((a, k)))
    headers = _header_admin(config)
    resp = client.put(
        "/api/admin/configuracoes", json={"valores": {"MOSTRAR_CARGAS_CISS": True}}, headers=headers
    )
    assert resp.status_code == 409
    assert resp.get_json()["confirmacao_necessaria"] == ["MOSTRAR_CARGAS_CISS"]
    assert avisos == []

    resp = client.put(
        "/api/admin/configuracoes",
        json={"valores": {"MOSTRAR_CARGAS_CISS": True}, "confirmar_perigosos": True},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.get_json()["pendente_reinicio"] is True
    assert len(avisos) == 1


def test_salvar_o_padrao_explicito_nao_conta_como_perigoso(client, config):
    headers = _header_admin(config)
    resp = client.put(
        "/api/admin/configuracoes", json={"valores": {"SOMENTE_PEDIDOS_SOLTOS": True}}, headers=headers
    )
    assert resp.status_code == 200


def test_rastreio_sem_redis_e_recusado(client, config):
    headers = _header_admin(config)
    resp = client.put(
        "/api/admin/configuracoes",
        json={"valores": {"RASTREIO_INT_ERP_ATIVO": True}, "confirmar_perigosos": True},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "REDIS_URL" in resp.get_json()["error"]


def test_chave_desconhecida_ou_infra_e_recusada(client, config):
    headers = _header_admin(config)
    for chave in ("NAO_EXISTE", "DATABASE_PATH", "SECRET_KEY"):
        resp = client.put("/api/admin/configuracoes", json={"valores": {chave: "x"}}, headers=headers)
        assert resp.status_code == 400, chave


def test_importa_ambiente_uma_vez_so(tmp_path):
    caminho = str(tmp_path / "db.sqlite")
    init_database(caminho)
    conn = connect(caminho)
    env = {"CISS_API_URL": '"https://ciss.env"', "SECRET_KEY": "antiga", "PATH": "/bin"}
    assert sorted(configuracoes.importar_ambiente(conn, env)) == ["CISS_API_URL", "SECRET_KEY"]
    assert configuracoes.ler(conn, "CISS_API_URL") == "https://ciss.env"  # aspas removidas
    assert configuracoes.ler(conn, "PATH") is None

    # Apagado na tela não ressuscita do ambiente no próximo boot.
    with conn:
        conn.execute("DELETE FROM configuracoes WHERE chave = 'CISS_API_URL'")
    assert configuracoes.importar_ambiente(conn, env) == []
    assert configuracoes.ler(conn, "CISS_API_URL") is None
    conn.close()


def test_boot_gera_secret_key_estavel(tmp_path):
    caminho = str(tmp_path / "db.sqlite")
    init_database(caminho)
    conn = connect(caminho)
    primeira = configuracoes.garantir_secret_key(conn)
    assert primeira == configuracoes.garantir_secret_key(conn)
    assert len(primeira) >= 48
    conn.close()


def test_boot_tolerante_desliga_grupo_invalido_em_vez_de_derrubar(tmp_path, caplog):
    caminho = str(tmp_path / "db.sqlite")
    init_database(caminho)
    conn = connect(caminho)
    with conn:
        conn.executemany(
            "INSERT INTO configuracoes (chave, valor) VALUES (?, ?)",
            [
                ("FUSION_PORTAL_URL", "https://p"),
                ("FUSION_PORTAL_SENHA", "nao-logar-isto"),
                ("RASTREIO_INT_ERP_ATIVO", "true"),
                ("AUTO_ENVIO_PRE_CARGA_JANELA_HORAS", "abc"),
            ],
        )
    with caplog.at_level(logging.WARNING):
        config = configuracoes.carregar_config(conn, {"DATABASE_PATH": caminho})
    conn.close()
    assert config.fusion_portal is None
    assert config.rastreio_int_erp_ativo is False
    assert config.auto_envio_pre_carga_janela_horas == 24
    assert config.ciss_api_url is None
    assert "nao-logar-isto" not in caplog.text


def test_estrito_continua_levantando():
    with pytest.raises(ConfigError):
        load_config({"SECRET_KEY": "s", "RASTREIO_INT_ERP_AUTOMATICO": "true"})
