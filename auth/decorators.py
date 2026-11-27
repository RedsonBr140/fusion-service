"""Proteção de rotas via `Authorization: Bearer <token>` — sem cookie,
sem sessão Flask. O token é assinado e autocontido; não há tabela de
sessões a consultar, então "revogar" aqui é esperar o token expirar
(~12h) ou desativar a pessoa em `usuarios_locais` (afeta a próxima troca
de token ou login, não uma sessão já emitida — mesmo limite que o logout
do CISS no meta-natal tem para o token do CISS)."""

from __future__ import annotations

from functools import wraps

from flask import current_app, g, jsonify, request

from auth import tokens

NOT_AUTHENTICATED = "Não autenticado."
NOT_ADMIN = "Só administradores."


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer "):
            return jsonify({"error": NOT_AUTHENTICATED}), 401

        token = header[len("Bearer ") :].strip()
        config = current_app.config["APP_CONFIG"]
        try:
            claims = tokens.verificar(token, config.secret_key)
        except tokens.TokenInvalido:
            return jsonify({"error": NOT_AUTHENTICATED}), 401

        g.user = {
            "username": claims.get("username"),
            "ciss_code": claims.get("ciss_code"),
            "name": claims.get("name"),
        }
        g.claims = claims
        return view(*args, **kwargs)

    return wrapper


def admin_required(view):
    """Papel conferido ao vivo no banco, nunca confiado ao token: tirar
    admin de alguém vale na próxima requisição, não em 12h."""

    @login_required
    @wraps(view)
    def wrapper(*args, **kwargs):
        from auth.service import usuario_da_sessao

        row = usuario_da_sessao(g.claims)
        if row is None or row["active"] != 1 or row["admin"] != 1:
            return jsonify({"error": NOT_ADMIN}), 403
        g.user["username"] = row["username"]
        return view(*args, **kwargs)

    return wrapper
