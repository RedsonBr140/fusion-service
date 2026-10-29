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

import database
from auth.decorators import login_required
from auth.routes import bp as auth_bp
from cargas_fusion import auto_envio
from cargas_fusion.routes import bp as cargas_fusion_bp
from config import Config, load_config
from integrations.ciss import CissClient
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


def create_app(config=None) -> Flask:
    _configurar_logging()
    app = Flask(__name__)
    config = config or load_config()
    app.config["APP_CONFIG"] = config
    app.config["SECRET_KEY"] = config.secret_key

    app.config["CISS_DB_CLIENT"] = CissClient(config.ciss_db) if config.ciss_db else None
    app.config["FUSION_CLIENT"] = FusionClient(config.fusion) if config.fusion else None
    app.config["FUSION_PORTAL_CLIENT"] = (
        FusionPortalClient(config.fusion_portal) if config.fusion_portal else None
    )
    # `None` quando o ciclo automático nunca foi ligado (sem REDIS_URL) —
    # `AUTO_ENVIO_REDIS_CLIENT is None` é como a rota de "executar agora"
    # decide se o recurso existe neste ambiente, sem reler `Config`.
    app.config["AUTO_ENVIO_REDIS_CLIENT"] = (
        redis.Redis.from_url(config.redis_url) if config.auto_envio_pre_carga_ativo else None
    )

    database.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(cargas_fusion_bp)

    if config.auto_envio_pre_carga_ativo:
        _agendar_auto_envio_pre_carga(
            config,
            app.config["AUTO_ENVIO_REDIS_CLIENT"],
            app.config["CISS_DB_CLIENT"],
            app.config["FUSION_CLIENT"],
            app.config["FUSION_PORTAL_CLIENT"],
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
