"""Única fronteira deste projeto com o Discord — nada de webhook/URL de
Discord fora deste arquivo, mesmo princípio de `integrations/fusion.py`
ser a única fronteira com o SOAP.

Dois canais, dois webhooks (decisão do negócio, 2026-11-26):

- **Erros** (`DISCORD_WEBHOOK_URL`): falha — `erro(url, ...)`, embed
  vermelho **com `@everyone`**; e aviso — `aviso(url, ...)`, embed amarelo,
  **sem menção**. Quem chama passa a URL (`Config.discord_webhook_url`).
- **Atividade** (`DISCORD_WEBHOOK_ATIVIDADE_URL`): toda ação que a
  integração executa — `atividade(...)`, configurado uma vez por processo
  em `configurar_atividade` (`create_app`). Alimentado quase todo por
  `integrations/auditoria.py`, que envolve os clients de escrita.

Tudo sai como embed, por uma fila em thread própria (`_Despachante`):
nunca bloqueia nem derruba quem chamou, junta até 10 embeds por mensagem
e respeita o rate limit do Discord (429 → `retry_after`). Uma mensagem
por ação, sem fila, estourou o rate limit (429) no primeiro ciclo real.

Nunca loga a URL do webhook: ela carrega o token (`/webhooks/<id>/<token>`)
— vazou assim em log real via traceback do `requests` (2026-11-26).
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
import time
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)

_TIMEOUT_SEGUNDOS = 10

COR_SUCESSO = 0x2ECC71
COR_ERRO = 0xE74C3C
COR_INFO = 0x3498DB
COR_AVISO = 0xF39C12

_AUTOR = "Integração Fusion ↔ CISS"
_RODAPE = f"fusion-service · {socket.gethostname()}"

# Limites do Discord por embed / por mensagem.
_MAX_TITULO = 256
_MAX_DESCRICAO = 4096
_MAX_CAMPOS = 25
_MAX_NOME_CAMPO = 256
_MAX_VALOR_CAMPO = 1024
_MAX_EMBEDS_POR_MENSAGEM = 10
_MAX_CARACTERES_POR_MENSAGEM = 5500  # teto real 6000, folga pro rodapé/autor
_MAX_FILA = 10000

# Só erro pinga todo mundo. Todo o resto vai com menção explicitamente
# desligada — um "@everyone" que viesse num texto do CISS/Fusion dentro
# de um embed não pode virar ping.
_COM_MENCAO = (("content", "@everyone"), ("allowed_mentions", (("parse", ("everyone",)),)))
_SEM_MENCAO = (("allowed_mentions", (("parse", ()),)),)


def _corpo_extra(chave: tuple) -> dict:
    """`chave` é hashable (vai na fila e decide o agrupamento); vira dict aqui."""
    return {
        nome: ({k: list(v) for k, v in valor} if isinstance(valor, tuple) else valor)
        for nome, valor in chave
    }
_MAX_TENTATIVAS = 5


def _cortar(texto: object, limite: int) -> str:
    texto = str(texto) if texto is not None else ""
    return texto if len(texto) <= limite else texto[: limite - 1] + "…"


def montar_embed(
    titulo: str,
    descricao: str | None = None,
    *,
    cor: int = COR_INFO,
    campos: list[tuple[str, object, bool]] | None = None,
) -> dict:
    """`campos`: `(nome, valor, inline)`; valor vazio/`None` vira `—`."""
    embed: dict = {
        "author": {"name": _AUTOR},
        "title": _cortar(titulo, _MAX_TITULO),
        "color": cor,
        "footer": {"text": _RODAPE},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if descricao:
        embed["description"] = _cortar(descricao, _MAX_DESCRICAO)
    if campos:
        embed["fields"] = [
            {
                "name": _cortar(nome, _MAX_NOME_CAMPO),
                "value": _cortar(valor if valor not in (None, "") else "—", _MAX_VALOR_CAMPO),
                "inline": inline,
            }
            for nome, valor, inline in campos[:_MAX_CAMPOS]
        ]
    return embed


def _tamanho(embed: dict) -> int:
    total = len(embed.get("title", "")) + len(embed.get("description", ""))
    total += len(embed["footer"]["text"]) + len(embed["author"]["name"])
    for campo in embed.get("fields", []):
        total += len(campo["name"]) + len(campo["value"])
    return total


def _postar(webhook_url: str, corpo: dict) -> float | None:
    """Um POST síncrono. Devolve segundos a esperar antes de tentar de
    novo (429), ou `None` quando terminou (sucesso ou falha definitiva)."""
    try:
        resposta = requests.post(webhook_url, json=corpo, timeout=_TIMEOUT_SEGUNDOS)
    except requests.RequestException as exc:
        logger.error("Falha ao notificar o Discord (%s)", type(exc).__name__)
        return None
    if resposta.status_code == 429:
        try:
            espera = float(resposta.json().get("retry_after", 1))
        except ValueError:
            espera = 1.0
        logger.warning("Discord rate limit (HTTP 429) — aguardando %.1fs", espera)
        return max(espera, 0.5)
    if resposta.status_code >= 400:
        # Nunca `raise_for_status()`/traceback: a mensagem leva a URL.
        logger.error("Falha ao notificar o Discord (HTTP %s)", resposta.status_code)
        return None
    # Rate limit preventivo: bucket esgotado → espera o reset antes do próximo.
    if resposta.headers.get("X-RateLimit-Remaining") == "0":
        try:
            time.sleep(float(resposta.headers.get("X-RateLimit-Reset-After", 0)))
        except ValueError:
            pass
    return None


class _Despachante:
    """Fila única por processo (cada worker do gunicorn tem a sua), uma
    thread daemon que agrupa embeds do mesmo webhook por mensagem."""

    def __init__(self) -> None:
        self._fila: queue.Queue[tuple[str, tuple, dict]] = queue.Queue(maxsize=_MAX_FILA)
        self._thread: threading.Thread | None = None
        self._trava = threading.Lock()

    def enfileirar(self, webhook_url: str, embed: dict, extra: tuple = _SEM_MENCAO) -> None:
        self._garantir_thread()
        try:
            self._fila.put_nowait((webhook_url, extra, embed))
        except queue.Full:
            logger.error("Fila do Discord cheia (%s) — embed descartado: %s", _MAX_FILA, embed.get("title"))

    def _garantir_thread(self) -> None:
        with self._trava:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._laco, name="discord-despachante", daemon=True)
                self._thread.start()

    def _laco(self) -> None:
        while True:
            url, extra, primeiro = self._fila.get()
            lote, tamanho = [primeiro], _tamanho(primeiro)
            # Junta o que já estiver na fila pro mesmo webhook E mesma
            # menção (erro com @everyone nunca divide mensagem com aviso),
            # até o teto.
            sobra: list[tuple[str, tuple, dict]] = []
            while len(lote) < _MAX_EMBEDS_POR_MENSAGEM:
                try:
                    outro_url, outro_extra, embed = self._fila.get_nowait()
                except queue.Empty:
                    break
                if (outro_url, outro_extra) == (url, extra) and tamanho + _tamanho(embed) <= _MAX_CARACTERES_POR_MENSAGEM:
                    lote.append(embed)
                    tamanho += _tamanho(embed)
                else:
                    sobra.append((outro_url, outro_extra, embed))
                    break
            for item in sobra:
                try:
                    self._fila.put_nowait(item)
                except queue.Full:
                    pass
            self._enviar(url, lote, extra)

    def _enviar(self, url: str, lote: list[dict], extra: tuple = _SEM_MENCAO) -> None:
        corpo = {**_corpo_extra(extra), "embeds": lote}
        for _ in range(_MAX_TENTATIVAS):
            espera = _postar(url, corpo)
            if espera is None:
                return
            time.sleep(espera)
        logger.error("Discord: lote de %s embed(s) descartado após %s tentativas", len(lote), _MAX_TENTATIVAS)

    def esvaziar(self, timeout: float = 30.0) -> None:
        """Só pra teste/encerramento: espera a fila esvaziar."""
        limite = time.monotonic() + timeout
        while not self._fila.empty() and time.monotonic() < limite:
            time.sleep(0.05)


_despachante = _Despachante()
_webhook_atividade: str | None = None


def configurar_atividade(webhook_url: str | None) -> None:
    global _webhook_atividade
    _webhook_atividade = webhook_url or None


def _publicar(webhook_url: str | None, embed: dict, canal: str, extra: tuple = _SEM_MENCAO) -> None:
    logger.info("Discord[%s] %s", canal, embed.get("title"))
    if webhook_url:
        _despachante.enfileirar(webhook_url, embed, extra)


def erro(
    webhook_url: str | None,
    titulo: str,
    descricao: str | None = None,
    *,
    campos: list[tuple[str, object, bool]] | None = None,
) -> None:
    """Canal de erros, **com `@everyone`**. Sem webhook, só loga."""
    _publicar(
        webhook_url, montar_embed(f"🚨 {titulo}", descricao, cor=COR_ERRO, campos=campos), "erro", _COM_MENCAO
    )


def aviso(
    webhook_url: str | None,
    titulo: str,
    descricao: str | None = None,
    *,
    campos: list[tuple[str, object, bool]] | None = None,
) -> None:
    """Canal de erros também, mas amarelo e **sem menção** — algo que
    alguém deveria olhar, não algo quebrado."""
    _publicar(webhook_url, montar_embed(f"⚠️ {titulo}", descricao, cor=COR_AVISO, campos=campos), "aviso")


def atividade(
    titulo: str,
    descricao: str | None = None,
    *,
    cor: int = COR_SUCESSO,
    campos: list[tuple[str, object, bool]] | None = None,
) -> None:
    """Canal de atividade (toda ação da integração). Sem webhook
    configurado (`configurar_atividade`), só loga."""
    _publicar(_webhook_atividade, montar_embed(titulo, descricao, cor=cor, campos=campos), "atividade")
