"""Única fronteira do projeto com a libciss.

Nada com formato de libciss ou requests atravessa este módulo em direção
a core/. A lib é alpha (0.1.0a6) e sem testes; isolar a superfície faz
uma quebra de API custar um arquivo.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import requests
from libciss import CISSClient
from libciss.exceptions import CISSClientError

logger = logging.getLogger(__name__)

# O padrão da lib é (5, 30). 30s de leitura seguram um worker do Flask
# por tentativa de login; o login precisa falhar rápido.
LOGIN_TIMEOUT = (5.0, 10.0)

LOGIN_REJECTED_STATUSES = frozenset({401, 403})


@dataclass(frozen=True, slots=True)
class CissIdentity:
    token: str
    user_id: int
    group_id: int
    username: str
    enabled: bool
    company_id: int
    company_cnpj: str
    company_name: str
    permissions: tuple[str, ...]


class CissAuthError(Exception):
    """Base das falhas de autenticação no CISS."""


class InvalidCredentials(CissAuthError):
    """O CISS rejeitou usuário ou senha."""


class CissUnavailable(CissAuthError):
    """O CISS está inalcançável, com falha, ou respondeu algo inesperado."""


def build_client(api_url: str, ca_bundle: str | None = None) -> CISSClient:
    client = CISSClient(api_url, timeout=LOGIN_TIMEOUT)
    if ca_bundle:
        client.session.verify = ca_bundle
    return client


def authenticate(
    username: str,
    password: str,
    *,
    api_url: str,
    ca_bundle: str | None = None,
) -> CissIdentity:
    """Valida credenciais no CISS. Não mantém estado entre chamadas.

    O CISSClient é descartável de propósito: ele é dono de uma única
    requests.Session e o login escreve o Authorization no header
    compartilhado, então um client global corromperia logins concorrentes.
    """
    client = build_client(api_url, ca_bundle)

    try:
        response = client.cisscontrol.auth.login(username, password)
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else None
        if status in LOGIN_REJECTED_STATUSES:
            logger.info("CISS rejeitou as credenciais de %s", username)
            raise InvalidCredentials from None
        logger.warning("CISS respondeu status %s para %s", status, username)
        raise CissUnavailable from None
    except requests.RequestException as exc:
        logger.warning("CISS inalcançável (%s)", type(exc).__name__)
        raise CissUnavailable from None
    except CISSClientError as exc:
        logger.warning("Falha da libciss (%s)", type(exc).__name__)
        raise CissUnavailable from None
    except (KeyError, TypeError, ValueError) as exc:
        # Defeito de integração, não credencial errada: o usuário não
        # resolve digitando de novo.
        logger.error("Payload de login do CISS em formato inesperado (%s)", type(exc).__name__)
        raise CissUnavailable from None

    return CissIdentity(
        token=response.token_bearer,
        user_id=response.id_user,
        group_id=response.id_grupo,
        username=response.username,
        enabled=response.enabled,
        company_id=response.id_empresa,
        company_cnpj=response.cnpj_empresa,
        company_name=response.razao_social,
        permissions=tuple(response.permissions),
    )
