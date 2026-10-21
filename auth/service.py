"""Login direto (fallback se o meta-natal estiver fora do ar) e troca de
token (caminho rápido, quando o meta-natal está no ar).

Mesma ordem obrigatória do meta-natal (core/auth/service.py): a allow-list
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
EXCHANGE_ERROR = "Token inválido, expirado, ou usuário sem acesso a este serviço."


class LoginRejected(Exception):
    """Login negado. Sempre apresentado com a mensagem genérica."""


class AuthUnavailable(Exception):
    """O CISS não pôde ser consultado."""


class ExchangeRejected(Exception):
    """Token de troca inválido, expirado, ou ciss_code fora da allow-list."""


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


def exchange(token_meta_natal: str) -> dict:
    """Troca o token de vida curta emitido pelo meta-natal por uma sessão
    deste serviço. Exige que o `ciss_code` do token esteja na allow-list
    local e ativo — o meta-natal já checou `tool_required`, mas este
    serviço não confia nisso sem conferir a própria allow-list."""
    config = current_app.config["APP_CONFIG"]

    try:
        claims = tokens.verificar(token_meta_natal, config.fusion_token_secret)
    except tokens.TokenInvalido:
        raise ExchangeRejected from None

    ciss_code = claims.get("ciss_code")
    if ciss_code is None:
        raise ExchangeRejected

    row = get_db().execute(
        "SELECT ciss_code, name, active FROM usuarios_locais WHERE ciss_code = ?",
        (str(ciss_code),),
    ).fetchone()
    if row is None or row["active"] != 1:
        raise ExchangeRejected

    token = tokens.criar_token_sessao(
        ciss_code=row["ciss_code"], name=row["name"], secret=config.secret_key
    )
    return {"token": token, "user": _public_user(row["ciss_code"], row["name"])}
