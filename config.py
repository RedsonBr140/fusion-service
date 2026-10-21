"""Leitura e validação do ambiente. Falha no boot, não no primeiro login."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


class ConfigError(RuntimeError):
    """Ambiente incompleto ou inválido."""


CISS_DB_OBRIGATORIAS = (
    "CISS_DB_DRIVER",
    "CISS_DB_HOST",
    "CISS_DB_NAME",
    "CISS_DB_USER",
    "CISS_DB_PASSWORD",
)

FUSION_OBRIGATORIAS = (
    "FUSION_API_URL",
    "FUSION_LOGIN",
    "FUSION_SENHA",
)

FUSION_PORTAL_OBRIGATORIAS = (
    "FUSION_PORTAL_URL",
    "FUSION_PORTAL_LOGIN",
    "FUSION_PORTAL_SENHA",
)


@dataclass(frozen=True, slots=True, repr=False)
class CissDbConfig:
    """Conexão ODBC com o CISS DB2.

    A senha é campo, mas a string de conexão é método: assim ela não aparece
    em `asdict`, nem em dump de configuração, nem no `repr`.
    """

    driver: str
    host: str
    port: int
    database: str
    user: str
    password: str
    timeout: int

    def connection_string(self) -> str:
        return (
            f"DRIVER={{{self.driver}}};"
            f"DATABASE={self.database};"
            f"HOSTNAME={self.host};"
            f"PORT={self.port};"
            "PROTOCOL=TCPIP;"
            f"UID={self.user};"
            f"PWD={self.password};"
        )

    def __repr__(self) -> str:
        return (
            f"CissDbConfig(driver={self.driver!r}, host={self.host!r}, "
            f"port={self.port}, database={self.database!r}, "
            f"user={self.user!r}, password='***', timeout={self.timeout})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class FusionConfig:
    """Credenciais do webservice SOAP do Fusion (saveCarga e companhia)."""

    api_url: str
    login: str
    senha: str

    def __repr__(self) -> str:
        return f"FusionConfig(api_url={self.api_url!r}, login={self.login!r}, senha='***')"


@dataclass(frozen=True, slots=True, repr=False)
class FusionPortalConfig:
    """Credenciais do portal web do Fusion DMS (fusion-py), login humano."""

    url: str
    login: str
    senha: str

    def __repr__(self) -> str:
        return f"FusionPortalConfig(url={self.url!r}, login={self.login!r}, senha='***')"


@dataclass(frozen=True, slots=True)
class Config:
    secret_key: str
    """Assina os tokens de sessão deste serviço (`auth/tokens.py`)."""

    fusion_token_secret: str
    """Segredo compartilhado com o meta-natal, para verificar o token de
    troca que ele emite em `POST /api/tools/cargas-fusion/token`. Valor
    igual nos dois `.env`s — é a única coisa que os dois serviços
    compartilham."""

    ciss_api_url: str
    ciss_ca_bundle: str | None
    ciss_db: CissDbConfig | None
    fusion: FusionConfig | None
    fusion_portal: FusionPortalConfig | None

    allowed_origins: tuple[str, ...] = field(default_factory=tuple)
    database_path: str = "fusion_service.db"
    port: int = 5050


def _required(env: Mapping[str, str], nome: str) -> str:
    valor = (env.get(nome) or "").strip()
    if not valor:
        raise ConfigError(f"Variável de ambiente obrigatória ausente: {nome}")
    return valor


def _optional(env: Mapping[str, str], nome: str) -> str | None:
    valor = (env.get(nome) or "").strip()
    return valor or None


def _load_ciss_db(env: Mapping[str, str]) -> CissDbConfig | None:
    """Todas as obrigatórias, ou nenhuma. Pela metade é erro de digitação."""
    valores = {nome: (env.get(nome) or "").strip() for nome in CISS_DB_OBRIGATORIAS}

    if not any(valores.values()):
        return None

    faltando = [nome for nome, valor in valores.items() if not valor]
    if faltando:
        raise ConfigError(
            "Configuração do CISS DB2 incompleta — preencha todas ou nenhuma. "
            f"Faltando: {', '.join(faltando)}"
        )

    return CissDbConfig(
        driver=valores["CISS_DB_DRIVER"],
        host=valores["CISS_DB_HOST"],
        database=valores["CISS_DB_NAME"],
        user=valores["CISS_DB_USER"],
        password=valores["CISS_DB_PASSWORD"],
        port=int(env.get("CISS_DB_PORT") or 50000),
        timeout=int(env.get("CISS_DB_TIMEOUT") or 10),
    )


def _load_fusion(env: Mapping[str, str]) -> FusionConfig | None:
    """Todas as obrigatórias, ou nenhuma — mesmo critério do CISS DB2."""
    valores = {nome: (env.get(nome) or "").strip() for nome in FUSION_OBRIGATORIAS}

    if not any(valores.values()):
        return None

    faltando = [nome for nome, valor in valores.items() if not valor]
    if faltando:
        raise ConfigError(
            "Configuração do Fusion incompleta — preencha todas ou nenhuma. "
            f"Faltando: {', '.join(faltando)}"
        )

    return FusionConfig(
        api_url=valores["FUSION_API_URL"].rstrip("/"),
        login=valores["FUSION_LOGIN"],
        senha=valores["FUSION_SENHA"],
    )


def _load_fusion_portal(env: Mapping[str, str]) -> FusionPortalConfig | None:
    """Todas as obrigatórias, ou nenhuma — mesmo critério do CISS DB2."""
    valores = {nome: (env.get(nome) or "").strip() for nome in FUSION_PORTAL_OBRIGATORIAS}

    if not any(valores.values()):
        return None

    faltando = [nome for nome, valor in valores.items() if not valor]
    if faltando:
        raise ConfigError(
            "Configuração do portal Fusion incompleta — preencha todas ou nenhuma. "
            f"Faltando: {', '.join(faltando)}"
        )

    return FusionPortalConfig(
        url=valores["FUSION_PORTAL_URL"].rstrip("/"),
        login=valores["FUSION_PORTAL_LOGIN"],
        senha=valores["FUSION_PORTAL_SENHA"],
    )


def _load_allowed_origins(env: Mapping[str, str]) -> tuple[str, ...]:
    bruto = env.get("ALLOWED_ORIGINS") or "http://localhost:5173"
    return tuple(origem.strip() for origem in bruto.split(",") if origem.strip())


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        load_dotenv(Path(__file__).resolve().parent / ".env")
        env = os.environ

    return Config(
        secret_key=_required(env, "SECRET_KEY"),
        fusion_token_secret=_required(env, "FUSION_TOKEN_SECRET"),
        ciss_api_url=_required(env, "CISS_API_URL").rstrip("/"),
        ciss_ca_bundle=_optional(env, "CISS_CA_BUNDLE"),
        ciss_db=_load_ciss_db(env),
        fusion=_load_fusion(env),
        fusion_portal=_load_fusion_portal(env),
        allowed_origins=_load_allowed_origins(env),
        database_path=(env.get("DATABASE_PATH") or "fusion_service.db"),
        port=int(env.get("PORT") or 5050),
    )
