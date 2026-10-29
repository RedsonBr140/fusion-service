"""Leitura e validação do ambiente. Falha no boot, não no primeiro login."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
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
    "FUSION_EMPRESA_ID",
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
    """Credenciais do webservice SOAP do Fusion (saveCarga e companhia).

    `empresa_id` é o id interno do Fusion pra esta empresa (não o
    CNPJ) — mesmo campo que o integrador legado em Delphi chama de
    `ID_EMPRESA` em `Config.ini` (seção `[FUSIONTRAK]`), usado como
    `empresa_id` no heartbeat `set_dt_ult_int_erp.php`.
    """

    api_url: str
    login: str
    senha: str
    empresa_id: str

    def __repr__(self) -> str:
        return (
            f"FusionConfig(api_url={self.api_url!r}, login={self.login!r}, "
            f"senha='***', empresa_id={self.empresa_id!r})"
        )


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

    ciss_api_url: str
    ciss_ca_bundle: str | None
    ciss_db: CissDbConfig | None
    fusion: FusionConfig | None
    fusion_portal: FusionPortalConfig | None

    database_path: str = "fusion_service.db"
    port: int = 5050

    mostrar_cargas_ciss: bool = False
    """Liga a carga real (CISS, `RETIRADA_PRODUTOS`) na API e no frontend.
    `False` por padrão: a formação de carga passou a acontecer dentro do
    próprio Fusion ("Formação de carga", a partir de pedidos enviados
    soltos — ver `montar_payload_pre_carga_solta`), então as rotas
    `/cargas*` ficam bloqueadas (403) até alguém religar isto. Pré-carga
    nunca é afetada por esta flag."""

    somente_pedidos_soltos: bool = True
    """Bloqueia o envio empacotado num `cargas[]`/`saveCarga` — tanto
    carga real (`POST /cargas/.../enviar`) quanto pré-carga no modo
    padrão (`POST /pre-cargas/.../enviar`, o `PC_...`). `True` por
    padrão: só `POST /pre-cargas/.../enviar-solta` (`saveEntregaServico`
    sem `cargas[]`) fica disponível, pra forçar a formação de carga a
    acontecer dentro do Fusion. Independente de `mostrar_cargas_ciss` —
    cobre o caso de alguém religar aquela sem pensar nesta."""

    discord_webhook_url: str | None = None
    """Opcional — sem ele, `cargas_fusion/auto_envio.py` só loga as
    falhas/achados, nunca tenta `integrations/discord.py::notificar`."""

    redis_url: str | None = None
    """Backend do lock distribuído (`redis-py`) que evita o ciclo
    automático (abaixo) disparar em dobro nos 2 workers do gunicorn —
    ver `auto_envio_pre_carga_ativo`. Obrigatório só quando essa flag
    está ligada (checado em `load_config`, não aqui)."""

    auto_envio_pre_carga_ativo: bool = False
    """Interruptor de ambiente pro ciclo automático de envio de
    pré-carga solta (`cargas_fusion/auto_envio.py`) — mesmo espírito de
    `mostrar_cargas_ciss`/`somente_pedidos_soltos`: nenhum ambiente
    começa a mandar nada sozinho sem alguém ligar deliberadamente. A
    pausa ao vivo (botão na UI, gravada em
    `cargas_fusion_auto_envio_estado`) é um segundo nível, em cima
    deste — liga aqui, pausa/retoma na tela sem redeploy."""

    auto_envio_pre_carga_intervalo_segundos: int = 300
    """A definir de verdade depois de medir a duração real de um ciclo
    em produção — por ora um palpite conservador."""

    auto_envio_pre_carga_janela_horas: int = 24

    auto_envio_pre_carga_corte: datetime = datetime(2026, 10, 28, 8, 0, 0)
    """Pré-carga com `DTMOVIMENTO` anterior a isto nunca é tocada pelo
    ciclo automático — pedido explícito do negócio: só pré-carga
    "nova" (depois da decisão de automatizar) entra na política
    automática. Configurável via `AUTO_ENVIO_PRE_CARGA_CORTE` (ISO,
    `AAAA-MM-DDTHH:MM:SS`) só pra permitir um teste local/live deliberado
    contra uma janela mais antiga — o default de produção não muda só
    porque isto existe."""


def _required(env: Mapping[str, str], nome: str) -> str:
    valor = (env.get(nome) or "").strip()
    if not valor:
        raise ConfigError(f"Variável de ambiente obrigatória ausente: {nome}")
    return valor


def _optional(env: Mapping[str, str], nome: str) -> str | None:
    valor = (env.get(nome) or "").strip()
    return valor or None


def _bool(env: Mapping[str, str], nome: str, default: bool) -> bool:
    valor = (env.get(nome) or "").strip().lower()
    if not valor:
        return default
    return valor in {"1", "true", "sim", "yes"}


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
        empresa_id=valores["FUSION_EMPRESA_ID"],
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


def load_config(env: Mapping[str, str] | None = None) -> Config:
    if env is None:
        load_dotenv(Path(__file__).resolve().parent / ".env")
        env = os.environ

    auto_envio_pre_carga_ativo = _bool(env, "AUTO_ENVIO_PRE_CARGA_ATIVO", False)
    redis_url = _optional(env, "REDIS_URL")
    if auto_envio_pre_carga_ativo and not redis_url:
        raise ConfigError(
            "AUTO_ENVIO_PRE_CARGA_ATIVO=true exige REDIS_URL — é o lock que evita "
            "o ciclo automático disparar em dobro nos workers do gunicorn."
        )

    return Config(
        secret_key=_required(env, "SECRET_KEY"),
        ciss_api_url=_required(env, "CISS_API_URL").rstrip("/"),
        ciss_ca_bundle=_optional(env, "CISS_CA_BUNDLE"),
        ciss_db=_load_ciss_db(env),
        fusion=_load_fusion(env),
        fusion_portal=_load_fusion_portal(env),
        database_path=(env.get("DATABASE_PATH") or "fusion_service.db"),
        port=int(env.get("PORT") or 5050),
        mostrar_cargas_ciss=_bool(env, "MOSTRAR_CARGAS_CISS", False),
        somente_pedidos_soltos=_bool(env, "SOMENTE_PEDIDOS_SOLTOS", True),
        discord_webhook_url=_optional(env, "DISCORD_WEBHOOK_URL"),
        redis_url=redis_url,
        auto_envio_pre_carga_ativo=auto_envio_pre_carga_ativo,
        auto_envio_pre_carga_intervalo_segundos=int(
            env.get("AUTO_ENVIO_PRE_CARGA_INTERVALO_SEGUNDOS") or 300
        ),
        auto_envio_pre_carga_janela_horas=int(env.get("AUTO_ENVIO_PRE_CARGA_JANELA_HORAS") or 24),
        auto_envio_pre_carga_corte=(
            datetime.fromisoformat(env["AUTO_ENVIO_PRE_CARGA_CORTE"])
            if env.get("AUTO_ENVIO_PRE_CARGA_CORTE")
            else datetime(2026, 10, 28, 8, 0, 0)
        ),
    )
