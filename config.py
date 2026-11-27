"""Monta a `Config` a partir de um mapeamento chave → texto.

Desde a configuração em banco (`configuracoes.py`), a fonte é a tabela
`configuracoes` do sqlite — o ambiente só fornece a infraestrutura
(`CHAVES_INFRA`: onde está o banco, porta, Redis) e serve de semente
única na primeira subida (`configuracoes.importar_ambiente`). As chaves
continuam com os mesmos nomes das antigas variáveis de ambiente.

Dois modos de validação:

- **estrito** (padrão): combinação inválida levanta `ConfigError` — usado
  ao salvar pela tela (vira 400) e pelos testes.
- **tolerante** (boot a partir do banco): o grupo inválido fica desligado
  (`None`/`False`) com um aviso no log. Derrubar o boot por um valor
  salvo errado deixaria o pod em CrashLoop sem nenhuma tela pra corrigir.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

logger = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Configuração incompleta ou inválida."""


CHAVES_INFRA = ("DATABASE_PATH", "PORT", "REDIS_URL", "LOG_LEVEL")
"""Ficam no ambiente (k8s Secret/`.env`), nunca no banco: `DATABASE_PATH`
é onde o banco está (ovo e galinha); os outros são infraestrutura do pod,
não decisão de quem opera a integração."""


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
    """Assina os tokens de sessão deste serviço (`auth/tokens.py`). Gerada
    e guardada no banco na primeira subida (`configuracoes.garantir_secret_key`)."""

    ciss_api_url: str | None
    """`None` só antes do setup inicial (`auth/setup.py`). O login lê o
    valor atual do banco a cada tentativa (`auth/service.py`), então
    concluir o setup não exige reinício."""
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
    """Canal de **erros** do Discord (só falha). Opcional — sem ele, erro
    só vai pro log."""

    discord_webhook_atividade_url: str | None = None
    """Canal de **atividade**: toda ação da integração (escrita no Fusion
    e no CISS, fila consumida, resumo de ciclo) — `integrations/auditoria.py`.
    Opcional — sem ele, só loga."""

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

    rastreio_int_erp_ativo: bool = False
    """Liga o rastreio (`cargas_fusion/rastreio.py`: `getIntErp` →
    `DBA.CARGAS_FUSION_TRACK`/`ENTREGAS_FUSION_TRACK` → `setIntErp`) — a
    única escrita deste serviço no CISS DB2 — **só pelo botão "Executar
    ciclo agora"**. Sozinho, nunca roda em agenda: isso é
    `rastreio_int_erp_automatico`. Desligado por padrão: `getIntErp` é
    destrutivo. Exige `REDIS_URL` (mesmo lock contra os 2 workers)."""

    rastreio_int_erp_automatico: bool = False
    """Agenda o ciclo do rastreio a cada `rastreio_int_erp_intervalo_segundos`,
    sem ninguém clicar. Desligado por padrão, e separado de
    `rastreio_int_erp_ativo` de propósito (decisão do negócio,
    2026-11-26): ligar o rastreio não pode, por si só, começar a escrever
    no CISS sozinho. Só vale com `rastreio_int_erp_ativo` também ligado."""

    rastreio_int_erp_intervalo_segundos: int = 120


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


def _sem_aspas_envolventes(env: Mapping[str, str]) -> dict[str, str]:
    """Tira um par de aspas (`"..."`/`'...'`) envolvendo o valor inteiro.

    Achado em staging (2026-11-26): o Secret do k8s foi gerado com
    `kubectl create secret --from-env-file` a partir de um `.env` com valores
    entre aspas — `python-dotenv` remove essas aspas ao ler o arquivo, mas
    `--from-env-file` não, então `FUSION_PORTAL_SENHA` chegava ao processo
    com as aspas literais. O login no portal falhava (`{'success': False}`)
    e toda busca de motorista/veículo devolvia 503. Normalizar aqui deixa o
    k8s igual ao `.env` local, venha o ambiente de onde vier. Só o nome da
    chave vai pro log, nunca o valor."""
    normalizado = dict(env)
    corrigidas = []
    for chave, valor in env.items():
        if len(valor) >= 2 and valor[0] == valor[-1] and valor[0] in "\"'":
            normalizado[chave] = valor[1:-1]
            corrigidas.append(chave)
    if corrigidas:
        logger.warning(
            "Variáveis de ambiente com aspas envolvendo o valor — aspas removidas: %s",
            ", ".join(sorted(corrigidas)),
        )
    return normalizado


def _grupo(estrito: bool, nome: str, carregar):
    """Modo tolerante: grupo inválido vira `None` com aviso (só a mensagem
    de `ConfigError`, que cita nomes de chave, nunca valor)."""
    try:
        return carregar()
    except (ConfigError, ValueError) as exc:
        if estrito:
            if isinstance(exc, ConfigError):
                raise
            raise ConfigError(f"{nome}: porta/timeout precisa ser número inteiro.") from None
        logger.warning("Configuração inválida — %s desligado: %s", nome, type(exc).__name__)
        return None


def _inteiro(env: Mapping[str, str], nome: str, default: int, estrito: bool) -> int:
    valor = (env.get(nome) or "").strip()
    if not valor:
        return default
    try:
        numero = int(valor)
        if numero <= 0:
            raise ValueError
        return numero
    except ValueError:
        if estrito:
            raise ConfigError(f"{nome} precisa ser um inteiro positivo.") from None
        logger.warning("Configuração inválida — %s não é inteiro positivo, usando %s", nome, default)
        return default


def _corte(env: Mapping[str, str], estrito: bool) -> datetime:
    padrao = datetime(2026, 10, 28, 8, 0, 0)
    valor = (env.get("AUTO_ENVIO_PRE_CARGA_CORTE") or "").strip()
    if not valor:
        return padrao
    try:
        return datetime.fromisoformat(valor)
    except ValueError:
        if estrito:
            raise ConfigError(
                "AUTO_ENVIO_PRE_CARGA_CORTE precisa estar no formato AAAA-MM-DDTHH:MM:SS."
            ) from None
        logger.warning("Configuração inválida — AUTO_ENVIO_PRE_CARGA_CORTE ilegível, usando o padrão")
        return padrao


def _dependencia(estrito: bool, ligado: bool, ok: bool, mensagem: str, nome: str) -> bool:
    """Flag ligada sem o que ela exige: estrito levanta, tolerante desliga."""
    if ligado and not ok:
        if estrito:
            raise ConfigError(mensagem)
        logger.warning("Configuração inválida — %s desligado: %s", nome, mensagem)
        return False
    return ligado


def load_config(env: Mapping[str, str] | None = None, *, estrito: bool = True) -> Config:
    """`env=None` lê o `.env`/ambiente do processo, só pra uso direto
    (testes, script). `create_app` monta a partir do banco
    (`configuracoes.carregar_config`)."""
    if env is None:
        load_dotenv(Path(__file__).resolve().parent / ".env")
        env = os.environ
    env = _sem_aspas_envolventes(env)

    redis_url = _optional(env, "REDIS_URL")
    auto_envio_pre_carga_ativo = _dependencia(
        estrito,
        _bool(env, "AUTO_ENVIO_PRE_CARGA_ATIVO", False),
        bool(redis_url),
        "AUTO_ENVIO_PRE_CARGA_ATIVO exige REDIS_URL (variável de ambiente) — é o lock que "
        "evita o ciclo automático disparar em dobro nos workers do gunicorn.",
        "AUTO_ENVIO_PRE_CARGA_ATIVO",
    )
    rastreio_int_erp_ativo = _dependencia(
        estrito,
        _bool(env, "RASTREIO_INT_ERP_ATIVO", False),
        bool(redis_url),
        "RASTREIO_INT_ERP_ATIVO exige REDIS_URL (variável de ambiente) — getIntErp é "
        "destrutivo, dois workers consumindo a fila ao mesmo tempo não pode acontecer.",
        "RASTREIO_INT_ERP_ATIVO",
    )
    rastreio_int_erp_automatico = _dependencia(
        estrito,
        _bool(env, "RASTREIO_INT_ERP_AUTOMATICO", False),
        rastreio_int_erp_ativo,
        "RASTREIO_INT_ERP_AUTOMATICO exige RASTREIO_INT_ERP_ATIVO — agendar um rastreio "
        "desligado não faz sentido.",
        "RASTREIO_INT_ERP_AUTOMATICO",
    )

    ciss_api_url = _optional(env, "CISS_API_URL")
    return Config(
        secret_key=_required(env, "SECRET_KEY"),
        ciss_api_url=ciss_api_url.rstrip("/") if ciss_api_url else None,
        ciss_ca_bundle=_optional(env, "CISS_CA_BUNDLE"),
        ciss_db=_grupo(estrito, "CISS DB2", lambda: _load_ciss_db(env)),
        fusion=_grupo(estrito, "Fusion (SOAP)", lambda: _load_fusion(env)),
        fusion_portal=_grupo(estrito, "Fusion (portal)", lambda: _load_fusion_portal(env)),
        database_path=(env.get("DATABASE_PATH") or "fusion_service.db"),
        port=int(env.get("PORT") or 5050),
        mostrar_cargas_ciss=_bool(env, "MOSTRAR_CARGAS_CISS", False),
        somente_pedidos_soltos=_bool(env, "SOMENTE_PEDIDOS_SOLTOS", True),
        discord_webhook_url=_optional(env, "DISCORD_WEBHOOK_URL"),
        discord_webhook_atividade_url=_optional(env, "DISCORD_WEBHOOK_ATIVIDADE_URL"),
        redis_url=redis_url,
        auto_envio_pre_carga_ativo=auto_envio_pre_carga_ativo,
        auto_envio_pre_carga_intervalo_segundos=_inteiro(
            env, "AUTO_ENVIO_PRE_CARGA_INTERVALO_SEGUNDOS", 300, estrito
        ),
        auto_envio_pre_carga_janela_horas=_inteiro(env, "AUTO_ENVIO_PRE_CARGA_JANELA_HORAS", 24, estrito),
        auto_envio_pre_carga_corte=_corte(env, estrito),
        rastreio_int_erp_ativo=rastreio_int_erp_ativo,
        rastreio_int_erp_automatico=rastreio_int_erp_automatico,
        rastreio_int_erp_intervalo_segundos=_inteiro(
            env, "RASTREIO_INT_ERP_INTERVALO_SEGUNDOS", 120, estrito
        ),
    )
