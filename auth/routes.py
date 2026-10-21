from __future__ import annotations

import sqlite3

from flask import Blueprint, g, jsonify, request

from auth import service
from auth.decorators import login_required

bp = Blueprint("auth", __name__, url_prefix="/api")


@bp.post("/login")
def login():
    corpo = request.get_json(silent=True) or {}
    username = corpo.get("username") or ""
    password = corpo.get("password") or ""
    if not username or not password:
        return jsonify({"error": service.GENERIC_ERROR}), 401

    try:
        resultado = service.login(username, password)
    except service.LoginRejected:
        return jsonify({"error": service.GENERIC_ERROR}), 401
    except service.AuthUnavailable:
        return jsonify({"error": service.UNAVAILABLE_ERROR}), 503

    return jsonify(resultado), 200


@bp.post("/token/exchange")
def exchange():
    corpo = request.get_json(silent=True) or {}
    token = corpo.get("token") or ""
    if not token:
        return jsonify({"error": service.EXCHANGE_ERROR}), 401

    try:
        resultado = service.exchange(token)
    except service.ExchangeRejected:
        return jsonify({"error": service.EXCHANGE_ERROR}), 401

    return jsonify(resultado), 200


@bp.get("/me")
@login_required
def me():
    return jsonify({"user": g.user}), 200


@bp.post("/logout")
@login_required
def logout():
    return "", 204


# --- Allow-list (usuarios_locais) ------------------------------------------
# Quem já tem acesso pode conceder a outros — grupo pequeno, mantido pela TI
# à mão (ver docstring de auth/service.py: de propósito não sincronizado
# com user_tools do meta-natal).


@bp.get("/usuarios")
@login_required
def listar_usuarios():
    return jsonify({"usuarios": service.listar_usuarios()}), 200


@bp.post("/usuarios")
@login_required
def conceder_usuario():
    corpo = request.get_json(silent=True) or {}
    username = (corpo.get("username") or "").strip()
    ciss_code = (corpo.get("ciss_code") or "").strip()
    name = (corpo.get("name") or "").strip()
    if not username or not ciss_code or not name:
        return jsonify({"error": "username, ciss_code e name são obrigatórios."}), 400

    try:
        usuario = service.conceder(username, ciss_code, name)
    except sqlite3.IntegrityError:
        return jsonify({"error": "ciss_code já está vinculado a outro username."}), 409

    return jsonify({"usuario": usuario}), 201
