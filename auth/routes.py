from __future__ import annotations

import sqlite3

from flask import Blueprint, current_app, g, jsonify, request

import configuracoes
from auth import service, setup
from auth.decorators import NOT_AUTHENTICATED, admin_required, login_required
from database import get_db

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


@bp.get("/me")
@login_required
def me():
    """Lido ao vivo da allow-list: desativado depois do login perde a sessão
    aqui (o frontend chama isto ao restaurar), e `admin` reflete o agora."""
    row = service.usuario_da_sessao(g.claims)
    if row is None or row["active"] != 1:
        return jsonify({"error": NOT_AUTHENTICATED}), 401
    return jsonify({"user": service._public_user(row)}), 200


@bp.post("/logout")
@login_required
def logout():
    return "", 204


# --- Setup inicial (auth/setup.py) -----------------------------------------
# Sem autenticação de propósito: é como o primeiro admin entra. Protegido
# pelo token de uso único gravado no PVC, não pela sessão.


@bp.get("/setup")
def status_setup():
    db = get_db()
    necessario = setup.setup_necessario(db)
    return jsonify(
        {
            "necessario": necessario,
            # Pré-preenche a tela; só exposto enquanto o setup está aberto.
            "ciss_api_url": configuracoes.ler(db, "CISS_API_URL") if necessario else None,
        }
    ), 200


@bp.post("/setup")
def concluir_setup():
    corpo = request.get_json(silent=True) or {}
    try:
        row = setup.concluir(
            get_db(),
            database_path=current_app.config["APP_CONFIG"].database_path,
            token=str(corpo.get("token") or ""),
            ciss_api_url=str(corpo.get("ciss_api_url") or ""),
            ciss_ca_bundle=corpo.get("ciss_ca_bundle") or None,
            username=str(corpo.get("username") or ""),
            password=str(corpo.get("password") or ""),
            name=corpo.get("name") or None,
        )
    except setup.SetupJaConcluido:
        return jsonify({"error": "O setup inicial já foi concluído."}), 409
    except setup.TokenSetupInvalido:
        return jsonify({"error": "Token de setup inválido."}), 403
    except setup.DadosSetupInvalidos as exc:
        return jsonify({"error": str(exc)}), 400
    except setup.LoginSetupRejeitado:
        return jsonify({"error": service.GENERIC_ERROR}), 401
    except setup.CissIndisponivelNoSetup:
        return jsonify({"error": "Não foi possível falar com o CISS nessa URL. Confira e tente de novo."}), 503
    return jsonify(service.sessao_para(row)), 201


# --- Allow-list (usuarios_locais) ------------------------------------------
# Só admin concede/altera — grupo pequeno, mantido à mão (ver docstring de
# auth/service.py: de propósito não sincronizado com user_tools do
# meta-natal). O primeiro admin sai do setup inicial.


@bp.get("/usuarios")
@admin_required
def listar_usuarios():
    return jsonify({"usuarios": service.listar_usuarios()}), 200


@bp.patch("/usuarios/<username>")
@admin_required
def alterar_usuario(username: str):
    corpo = request.get_json(silent=True) or {}
    active = corpo.get("active")
    admin = corpo.get("admin")
    if not isinstance(active, (bool, type(None))) or not isinstance(admin, (bool, type(None))):
        return jsonify({"error": "active/admin precisam ser booleanos."}), 400
    try:
        usuario = service.alterar(username.strip().lower(), active=active, admin=admin)
    except service.UsuarioNaoEncontrado:
        return jsonify({"error": "Usuário não encontrado."}), 404
    except service.UltimoAdmin:
        return jsonify({"error": "Não dá pra ficar sem nenhum admin ativo."}), 409
    return jsonify({"usuario": usuario}), 200


@bp.post("/usuarios")
@admin_required
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
