"""Tela de Administração: configuração em banco (`configuracoes.py`) e
reinício pra aplicar. Só admin."""

from __future__ import annotations

import logging
import os
import signal
import sys

from flask import Blueprint, current_app, g, jsonify, request

import configuracoes
from auth.decorators import admin_required
from config import ConfigError
from database import get_db
from integrations import discord

logger = logging.getLogger(__name__)

bp = Blueprint("admin", __name__, url_prefix="/api/admin")


@bp.get("/configuracoes")
@admin_required
def ver_configuracoes():
    resultado = configuracoes.descrever(get_db(), current_app.config["CONFIG_NO_BOOT"])
    resultado["redis_configurado"] = bool(current_app.config["APP_CONFIG"].redis_url)
    resultado["reinicio_disponivel"] = _reinicio_disponivel()
    return jsonify(resultado), 200


@bp.put("/configuracoes")
@admin_required
def salvar_configuracoes():
    corpo = request.get_json(silent=True) or {}
    valores = corpo.get("valores")
    if not isinstance(valores, dict):
        return jsonify({"error": "Corpo precisa de `valores` (objeto chave → valor)."}), 400
    config = current_app.config["APP_CONFIG"]
    por = g.user.get("username") or g.user.get("name") or "?"
    try:
        resultado = configuracoes.salvar(
            get_db(),
            valores,
            por=por,
            infra=current_app.config["CONFIG_INFRA"],
            confirmar_perigosos=bool(corpo.get("confirmar_perigosos")),
        )
    except configuracoes.ConfirmacaoNecessaria as exc:
        return (
            jsonify(
                {
                    "error": "Estas mudanças precisam de confirmação explícita.",
                    "confirmacao_necessaria": exc.chaves,
                }
            ),
            409,
        )
    except ConfigError as exc:
        return jsonify({"error": str(exc)}), 400

    if resultado["perigosas"]:
        # Mesmo canal de erros, amarelo e sem menção. Só nomes e o valor
        # novo (perigosas nunca são segredo).
        novos = configuracoes.ler_todos(get_db())
        discord.aviso(
            config.discord_webhook_url,
            "Configuração sensível alterada",
            "Vale a partir do próximo reinício.",
            campos=[(chave, novos.get(chave, "(padrão)"), True) for chave in resultado["perigosas"]]
            + [("Por", por, False)],
        )
    resultado.update(configuracoes.descrever(get_db(), current_app.config["CONFIG_NO_BOOT"]))
    return jsonify(resultado), 200


def _reinicio_disponivel() -> bool:
    return "gunicorn" in sys.modules and hasattr(signal, "SIGHUP")


@bp.post("/reiniciar")
@admin_required
def reiniciar():
    """`SIGHUP` no master do gunicorn: sobe workers novos (que releem a
    configuração no `create_app`) e encerra os antigos com calma. Sem
    derrubar o pod. Fora do gunicorn (dev), 501: reinicie à mão."""
    if not _reinicio_disponivel():
        return jsonify({"error": "Reinício pela tela só funciona sob gunicorn. Reinicie o processo."}), 501
    por = g.user.get("username") or "?"
    logger.warning("Reinício dos workers pedido por %s (aplicar configuração)", por)
    os.kill(os.getppid(), signal.SIGHUP)
    return jsonify({"status": "reiniciando"}), 202
