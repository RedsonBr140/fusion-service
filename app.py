"""fusion-service: integração com o Fusion DMS, independente do meta-natal.

Monolito: este mesmo Flask serve a API (`/api/...`) e, em produção, o build
do frontend (`frontend/dist/`) — sem nginx na frente, sem CORS, mesma
origem sempre. Em desenvolvimento, o Vite roda à parte (`npm run dev`) com
proxy de `/api` pra aqui (`frontend/vite.config.ts`), e esta rota estática
nunca entra em jogo porque o navegador fala com o Vite, não com o Flask.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import redis
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify, send_from_directory

from dotenv import load_dotenv

import configuracoes
import database
from auth import setup
from auth.admin_routes import bp as admin_bp
from auth.decorators import login_required
from auth.routes import bp as auth_bp
from cargas_fusion import auto_envio, rastreio
from cargas_fusion.routes import bp as cargas_fusion_bp
from config import Config
from integrations.ciss import CissClient
from integrations import discord
from integrations.auditoria import ClienteAuditado
from integrations.ciss_escrita import CissEscritaClient
from integrations.fusion import FusionClient
from integrations.fusion_portal import FusionPortalClient

FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"

logger = logging.getLogger(__name__)


def _configurar_logging() -> None:
    """Sem isto, todo `logger.info`/`logger.warning` do projeto (ciclo
    automático, heartbeat do Fusion, chamadas SOAP, ...) ia pro vácuo —
    o root logger não tem handler nenhum por padrão (nível `WARNING`),
    e nada aqui chamava `basicConfig`. Só os logs do próprio gunicorn
    apareciam (ele configura os loggers dele, não o root). `basicConfig`
    é no-op se o root já tiver um handler, então é seguro chamar isto a
    cada `create_app()` (testes incluídos) sem duplicar linha."""
    logging.basicConfig(
        level=(os.environ.get("LOG_LEVEL") or "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _agendar_auto_envio_pre_carga(
    config: Config,
    redis_client,
    ciss_client: CissClient | None,
    fusion_client: FusionClient | None,
    fusion_portal_client: FusionPortalClient | None,
) -> None:
    """Só roda quando `AUTO_ENVIO_PRE_CARGA_ATIVO=true` (`load_config` já
    garante `redis_url` presente nesse caso). O lock no Redis
    (`cargas_fusion/auto_envio.py`) é o que evita os 2 workers do
    gunicorn disparando o mesmo tick em dobro — nada aqui depende de
    quantos workers existem. Clients e `redis_client` vêm de fora
    (`create_app`) em vez de criados aqui, pra serem os mesmos usados
    pelo botão "executar agora" (`POST
    /api/pre-cargas/envio-automatico/executar-agora`) — os dois
    disputam o mesmo lock, nunca duas instâncias independentes dele."""

    def _tick() -> None:
        conn = database.connect(config.database_path)
        try:
            resultado = auto_envio.executar_ciclo(
                conn,
                redis_client,
                ciss_client,
                fusion_client,
                fusion_portal_client,
                janela_horas=config.auto_envio_pre_carga_janela_horas,
                corte=config.auto_envio_pre_carga_corte,
                discord_webhook_url=config.discord_webhook_url,
                lock_timeout_segundos=config.auto_envio_pre_carga_intervalo_segundos * 3,
            )
            logger.info("Ciclo de auto-envio de pré-carga: %s", resultado)
        finally:
            conn.close()

    scheduler = BackgroundScheduler()
    scheduler.add_job(_tick, "interval", seconds=config.auto_envio_pre_carga_intervalo_segundos)
    scheduler.start()


def _agendar_rastreio_int_erp(
    config: Config,
    redis_client,
    fusion_client: FusionClient | None,
    escrita_client: CissEscritaClient | None,
    ciss_client: CissClient | None,
) -> None:
    """Só roda com `RASTREIO_INT_ERP_ATIVO=true` **e**
    `RASTREIO_INT_ERP_AUTOMATICO=true`. Mesmo esquema de
    `_agendar_auto_envio_pre_carga`: lock no Redis compartilhado com a
    rota "executar agora" — `getIntErp` é destrutivo, dois consumidores
    simultâneos dividiriam a fila sem ninguém perceber."""

    def _tick() -> None:
        conn = database.connect(config.database_path)
        try:
            resultado = rastreio.executar_ciclo(
                conn,
                redis_client,
                fusion_client,
                escrita_client,
                discord_webhook_url=config.discord_webhook_url,
                lock_timeout_segundos=config.rastreio_int_erp_intervalo_segundos * 3,
                ciss_client=ciss_client,
            )
            logger.info("Ciclo de rastreio getIntErp: %s", resultado)
        finally:
            conn.close()

    scheduler = BackgroundScheduler()
    scheduler.add_job(_tick, "interval", seconds=config.rastreio_int_erp_intervalo_segundos)
    scheduler.start()


def _carregar_do_banco() -> tuple[Config, dict[str, str]]:
    """Ambiente só pra infra (`config.CHAVES_INFRA`) + semente única; o
    resto vem da tabela `configuracoes` (ver `configuracoes.py`)."""
    load_dotenv(Path(__file__).resolve().parent / ".env")
    infra = configuracoes.infra_do_ambiente(os.environ)
    database.init_database(infra.get("DATABASE_PATH") or "fusion_service.db")
    conn = database.connect(infra.get("DATABASE_PATH") or "fusion_service.db")
    try:
        configuracoes.importar_ambiente(conn, os.environ)
        return configuracoes.carregar_config(conn, infra), infra
    finally:
        conn.close()


def create_app(config=None) -> Flask:
    _configurar_logging()
    app = Flask(__name__)
    if config is None:
        config, infra = _carregar_do_banco()
    else:
        # Testes: `Config` pronta, sem banco como fonte.
        database.init_database(config.database_path)
        infra = {"DATABASE_PATH": config.database_path}
        if config.redis_url:
            infra["REDIS_URL"] = config.redis_url
    conn = database.connect(config.database_path)
    try:
        # O que este processo leu no boot — a tela compara com o banco
        # pra mostrar "pendente de reinício".
        app.config["CONFIG_NO_BOOT"] = configuracoes.ler_todos(conn)
        setup.preparar(conn, config.database_path)
    finally:
        conn.close()
    app.config["CONFIG_INFRA"] = infra
    app.config["APP_CONFIG"] = config
    app.config["SECRET_KEY"] = config.secret_key

    # Toda escrita no Fusion/CISS passa por `ClienteAuditado` → canal de
    # atividade do Discord (integrations/auditoria.py). Leitura do CISS
    # (`CissClient`) não é ação, fica sem proxy.
    discord.configurar_atividade(config.discord_webhook_atividade_url)
    app.config["CISS_DB_CLIENT"] = CissClient(config.ciss_db) if config.ciss_db else None
    app.config["FUSION_CLIENT"] = ClienteAuditado(FusionClient(config.fusion)) if config.fusion else None
    app.config["FUSION_PORTAL_CLIENT"] = (
        FusionPortalClient(config.fusion_portal) if config.fusion_portal else None
    )
    # `None` quando o ciclo automático nunca foi ligado (sem REDIS_URL) —
    # `AUTO_ENVIO_REDIS_CLIENT is None` é como a rota de "executar agora"
    # decide se o recurso existe neste ambiente, sem reler `Config`.
    # Escrita no CISS: só o rastreio (DBA.CARGAS_FUSION_TRACK) usa — ver
    # integrations/ciss_escrita.py. Mesmo CissDbConfig da leitura.
    app.config["CISS_ESCRITA_CLIENT"] = (
        ClienteAuditado(CissEscritaClient(config.ciss_db)) if config.ciss_db else None
    )
    redis_client = (
        redis.Redis.from_url(config.redis_url)
        if config.auto_envio_pre_carga_ativo or config.rastreio_int_erp_ativo
        else None
    )
    app.config["AUTO_ENVIO_REDIS_CLIENT"] = redis_client if config.auto_envio_pre_carga_ativo else None
    # `None` quando o rastreio não foi ligado — mesma convenção de
    # AUTO_ENVIO_REDIS_CLIENT pra rota "executar agora" decidir 409.
    app.config["RASTREIO_REDIS_CLIENT"] = redis_client if config.rastreio_int_erp_ativo else None

    database.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(cargas_fusion_bp)

    if config.auto_envio_pre_carga_ativo:
        _agendar_auto_envio_pre_carga(
            config,
            app.config["AUTO_ENVIO_REDIS_CLIENT"],
            app.config["CISS_DB_CLIENT"],
            app.config["FUSION_CLIENT"],
            app.config["FUSION_PORTAL_CLIENT"],
        )

    # Ativo ≠ automático: com só RASTREIO_INT_ERP_ATIVO, o rastreio existe
    # (botão "Executar ciclo agora") mas nunca roda sozinho.
    if config.rastreio_int_erp_ativo and config.rastreio_int_erp_automatico:
        _agendar_rastreio_int_erp(
            config,
            app.config["RASTREIO_REDIS_CLIENT"],
            app.config["FUSION_CLIENT"],
            app.config["CISS_ESCRITA_CLIENT"],
            app.config["CISS_DB_CLIENT"],
        )

    @app.get("/api/health")
    def health():
        # Sem autenticação de propósito — usado por probes do k8s e pelo
        # HEALTHCHECK do Dockerfile, que não têm sessão nem credencial.
        return jsonify(status="ok")

    @app.get("/api/config")
    @login_required
    def config_publica():
        # Flags que o frontend precisa saber antes de decidir o que
        # mostrar (ex. esconder a aba de carga real quando
        # MOSTRAR_CARGAS_CISS=false) — nunca a `Config` inteira, que
        # carrega credencial.
        return jsonify(
            mostrar_cargas_ciss=app.config["APP_CONFIG"].mostrar_cargas_ciss,
            somente_pedidos_soltos=app.config["APP_CONFIG"].somente_pedidos_soltos,
        )

    @app.route("/", defaults={"caminho": ""})
    @app.route("/<path:caminho>")
    def servir_frontend(caminho: str):
        if not FRONTEND_DIST.is_dir():
            return (
                "frontend/dist não existe — rode `npm run build` em frontend/, "
                "ou use `npm run dev` (Vite) em desenvolvimento.",
                404,
            )
        alvo = FRONTEND_DIST / caminho
        if caminho and alvo.is_file():
            return send_from_directory(FRONTEND_DIST, caminho)
        return send_from_directory(FRONTEND_DIST, "index.html")

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(port=app.config["APP_CONFIG"].port)
