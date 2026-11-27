"""Login direto — único caminho de entrada deste serviço, independente do
meta-natal em todo request (Cargas Fusion não é mais uma tool do Toolkit).

Mesma ordem herdada do meta-natal (core/auth/service.py): a allow-list
local é consultada antes de qualquer contato com o CISS — usuário não
cadastrado aqui não gera tráfego para o CISS.
"""

from __future__ import annotations

import logging

from flask import current_app

import configuracoes
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


class UltimoAdmin(Exception):
    """Mudança deixaria a instalação sem nenhum admin ativo."""


class UsuarioNaoEncontrado(Exception):
    pass


def _public_user(row) -> dict:
    return {
        "username": row["username"],
        "ciss_code": row["ciss_code"],
        "name": row["name"],
        "admin": bool(row["admin"]),
    }


def _buscar_usuario_local(normalized: str):
    return get_db().execute(
        "SELECT id, username, name, ciss_code, active, admin FROM usuarios_locais "
        "WHERE username = ? OR ciss_code = ?",
        (normalized, normalized),
    ).fetchone()


def usuario_da_sessao(claims: dict):
    """Linha da allow-list de quem está no token, ou `None`. Por `username`
    quando o token tem; token antigo (sem `username`) cai no `ciss_code`."""
    db = get_db()
    if claims.get("username"):
        return db.execute(
            "SELECT username, name, ciss_code, active, admin FROM usuarios_locais WHERE username = ?",
            (claims["username"],),
        ).fetchone()
    if claims.get("ciss_code"):
        return db.execute(
            "SELECT username, name, ciss_code, active, admin FROM usuarios_locais WHERE ciss_code = ?",
            (str(claims["ciss_code"]),),
        ).fetchone()
    return None


def _default_authenticate(username: str, password: str):
    """URL do CISS lida ao vivo do banco: o setup inicial grava ali, e o
    login precisa funcionar sem reiniciar o processo."""
    config = current_app.config["APP_CONFIG"]
    db = get_db()
    api_url = configuracoes.ler(db, "CISS_API_URL") or config.ciss_api_url
    if not api_url:
        logger.warning("Login sem CISS_API_URL configurada — setup inicial pendente?")
        raise CissUnavailable
    return ciss_auth.authenticate(
        username,
        password,
        api_url=api_url.rstrip("/"),
        ca_bundle=configuracoes.ler(db, "CISS_CA_BUNDLE") or config.ciss_ca_bundle,
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

    logger.info("Login concluído: %s", normalized)
    return sessao_para(row)


def sessao_para(row) -> dict:
    """`{token, user}` — usado pelo login e pelo setup inicial."""
    config = current_app.config["APP_CONFIG"]
    token = tokens.criar_token_sessao(
        username=row["username"], ciss_code=row["ciss_code"], name=row["name"], secret=config.secret_key
    )
    return {"token": token, "user": _public_user(row)}


def _usuario_publico(row) -> dict:
    return {
        "username": row["username"],
        "ciss_code": row["ciss_code"],
        "name": row["name"],
        "active": bool(row["active"]),
        "admin": bool(row["admin"]),
    }


def listar_usuarios() -> list[dict]:
    rows = get_db().execute(
        "SELECT username, ciss_code, name, active, admin FROM usuarios_locais ORDER BY username"
    ).fetchall()
    return [_usuario_publico(row) for row in rows]


def alterar(username: str, *, active: bool | None = None, admin: bool | None = None) -> dict:
    """Ativa/desativa ou dá/tira admin. Nunca deixa a instalação sem
    nenhum admin ativo (senão só o setup inicial resolveria, e ele só
    reabre depois de um reinício)."""
    conn = get_db()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT id, active, admin FROM usuarios_locais WHERE username = ?", (username,)
        ).fetchone()
        if row is None:
            raise UsuarioNaoEncontrado
        novo_active = row["active"] if active is None else int(active)
        novo_admin = row["admin"] if admin is None else int(admin)
        era_admin_ativo = row["admin"] == 1 and row["active"] == 1
        continua_admin_ativo = novo_admin == 1 and novo_active == 1
        if era_admin_ativo and not continua_admin_ativo:
            outros = conn.execute(
                "SELECT COUNT(*) FROM usuarios_locais WHERE admin = 1 AND active = 1 AND id <> ?", (row["id"],)
            ).fetchone()[0]
            if outros == 0:
                raise UltimoAdmin
        conn.execute(
            "UPDATE usuarios_locais SET active = ?, admin = ? WHERE id = ?", (novo_active, novo_admin, row["id"])
        )
    logger.info("Allow-list: %s alterado (active=%s, admin=%s)", username, novo_active, novo_admin)
    return _usuario_publico(_buscar_usuario_local(username))


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
