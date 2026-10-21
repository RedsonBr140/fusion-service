from __future__ import annotations

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
