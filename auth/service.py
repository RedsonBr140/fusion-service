"""Login direto — único caminho de entrada deste serviço, independente do
meta-natal em todo request (Cargas Fusion não é mais uma tool do Toolkit).

Mesma ordem herdada do meta-natal (core/auth/service.py): a allow-list
local é consultada antes de qualquer contato com o CISS — usuário não
cadastrado aqui não gera tráfego para o CISS.
"""

from __future__ import annotations

import logging

from flask import current_app

from auth import tokens
from database import get_db
from integrations import ciss_auth
from integrations.ciss_auth import CissUnavailable, InvalidCredentials

logger = logging.getLogger(__name__)

GENERIC_ERROR = "Usuário ou senha inválidos."
UNAVAILABLE_ERROR = "Serviço de autenticação indisponível. Tente novamente."


class LoginRejected(Exception):
    """Login negado. Sempre apresentado com a mensagem genérica."""


class AuthUnavailable(Exception):
    """O CISS não pôde ser consultado."""


def _public_user(ciss_code: str | None, name: str) -> dict:
    return {"ciss_code": ciss_code, "name": name}


def _buscar_usuario_local(normalized: str):
    return get_db().execute(
        "SELECT id, username, name, ciss_code, active FROM usuarios_locais "
        "WHERE username = ? OR ciss_code = ?",
        (normalized, normalized),
    ).fetchone()


def _default_authenticate(username: str, password: str):
    config = current_app.config["APP_CONFIG"]
    return ciss_auth.authenticate(
        username,
        password,
        api_url=config.ciss_api_url,
        ca_bundle=config.ciss_ca_bundle,
    )


def login(username: str, password: str, *, authenticate=None) -> dict:
    """Mesma ordem de core/auth/service.py do meta-natal: SQLite local
    antes do CISS. Devolve `{token, user}`."""
    authenticate = authenticate or _default_authenticate
    informado = username.strip()
    normalized = informado.lower()

    row = _buscar_usuario_local(normalized)
    if row is None:
        logger.info("Login negado: %s não está na allow-list local", normalized)
        raise LoginRejected

    if row["active"] != 1:
        logger.info("Login negado: %s está inativo", normalized)
        raise LoginRejected

    try:
        identity = authenticate(informado, password)
    except InvalidCredentials:
        raise LoginRejected from None
    except CissUnavailable:
        raise AuthUnavailable from None

    if not identity.enabled:
        logger.info("Login negado: %s está desabilitado no CISS", normalized)
        raise LoginRejected

    config = current_app.config["APP_CONFIG"]
    token = tokens.criar_token_sessao(
        ciss_code=row["ciss_code"], name=row["name"], secret=config.secret_key
    )
    logger.info("Login concluído: %s", normalized)
    return {"token": token, "user": _public_user(row["ciss_code"], row["name"])}


def _usuario_publico(row) -> dict:
    return {
        "username": row["username"],
        "ciss_code": row["ciss_code"],
        "name": row["name"],
        "active": bool(row["active"]),
    }


def listar_usuarios() -> list[dict]:
    rows = get_db().execute(
        "SELECT username, ciss_code, name, active FROM usuarios_locais ORDER BY username"
    ).fetchall()
    return [_usuario_publico(row) for row in rows]


def conceder(username: str, ciss_code: str, name: str) -> dict:
    """Adiciona alguém na allow-list local, ou reativa quem já estava lá
    com dado novo. Não confere `user_tools` do meta-natal de propósito —
    ver docstring do módulo: essa allow-list é a própria fonte, mantida
    manualmente. Levanta `sqlite3.IntegrityError` se `ciss_code` já
    pertence a outro `username`."""
    normalized = username.strip().lower()
    conn = get_db()
    conn.execute(
        "INSERT INTO usuarios_locais (username, ciss_code, name, active) "
        "VALUES (?, ?, ?, 1) "
        "ON CONFLICT(username) DO UPDATE SET "
        "ciss_code = excluded.ciss_code, name = excluded.name, active = 1",
        (normalized, str(ciss_code).strip(), name.strip()),
    )
    conn.commit()
    logger.info("Allow-list: %s concedido/reativado", normalized)
    return _usuario_publico(_buscar_usuario_local(normalized))
