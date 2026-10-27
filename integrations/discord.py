"""Única fronteira deste projeto com o Discord — nada de webhook/URL de
Discord fora deste arquivo, mesmo princípio de `integrations/fusion.py`
ser a única fronteira com o SOAP.

Usado por `cargas_fusion/auto_envio.py` pra alertar sobre falha do ciclo
automático e pré-carga multi-pedido ignorada. Nunca levanta — uma falha
de rede/Discord fora do ar não pode derrubar o ciclo que está chamando
isto; loga e segue.
"""

from __future__ import annotations

import logging

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_SEGUNDOS = 10


def notificar(webhook_url: str | None, mensagem: str) -> None:
    """Sem `webhook_url` (não configurado neste ambiente), só loga —
    chamar isto nunca é condicional no call site, a ausência de config
    é tratada aqui."""
    if not webhook_url:
        logger.info("Discord não configurado — mensagem só logada: %s", mensagem)
        return

    try:
        resposta = requests.post(
            webhook_url, json={"content": mensagem}, timeout=_TIMEOUT_SEGUNDOS
        )
        resposta.raise_for_status()
    except requests.RequestException:
        logger.exception("Falha ao notificar o Discord: %s", mensagem)
