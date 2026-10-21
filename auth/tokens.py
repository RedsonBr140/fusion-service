"""Tokens assinados (HMAC-SHA256), sem estado no servidor.

Dois usos do mesmo formato, com segredos diferentes:
- sessão própria deste serviço (`criar_token`/`verificar`, assinado com
  `Config.secret_key`), validade ~12h;
- token de troca emitido pelo meta-natal (`verificar` com
  `Config.fusion_token_secret`), validade ~5min — este serviço só
  verifica, nunca emite esse.

Formato: "<payload_b64url>.<hex_hmac_sha256>". `payload` é um JSON com,
no mínimo, `exp` (timestamp unix).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time

SESSAO_VALIDADE_SEGUNDOS = 12 * 60 * 60


class TokenInvalido(Exception):
    """Assinatura não confere, JSON corrompido, ou token expirado."""


def _b64_encode(dado: bytes) -> str:
    return base64.urlsafe_b64encode(dado).decode("ascii").rstrip("=")


def _b64_decode(texto: str) -> bytes:
    pad = "=" * (-len(texto) % 4)
    return base64.urlsafe_b64decode(texto + pad)


def _assinar(payload_b64: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload_b64.encode("ascii"), hashlib.sha256).hexdigest()


def emitir(claims: dict, secret: str) -> str:
    payload_b64 = _b64_encode(json.dumps(claims, ensure_ascii=False).encode("utf-8"))
    assinatura = _assinar(payload_b64, secret)
    return f"{payload_b64}.{assinatura}"


def verificar(token: str, secret: str) -> dict:
    """Devolve os claims se o token for válido e não expirado.

    Levanta `TokenInvalido` para qualquer outro caso — formato errado,
    assinatura que não confere, ou `exp` no passado. Nunca distingue o
    motivo pra fora: um token forjado e um expirado dão o mesmo erro.
    """
    try:
        payload_b64, assinatura = token.split(".", 1)
    except ValueError:
        raise TokenInvalido from None

    esperada = _assinar(payload_b64, secret)
    if not hmac.compare_digest(assinatura, esperada):
        raise TokenInvalido

    try:
        claims = json.loads(_b64_decode(payload_b64))
    except (ValueError, UnicodeDecodeError):
        raise TokenInvalido from None

    if not isinstance(claims, dict) or "exp" not in claims:
        raise TokenInvalido

    if claims["exp"] < time.time():
        raise TokenInvalido

    return claims


def criar_token_sessao(*, ciss_code: str | None, name: str, secret: str) -> str:
    claims = {
        "ciss_code": ciss_code,
        "name": name,
        "exp": time.time() + SESSAO_VALIDADE_SEGUNDOS,
    }
    return emitir(claims, secret)
