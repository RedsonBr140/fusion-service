"""fusion-service: integração com o Fusion DMS, independente do meta-natal.

Monolito: este mesmo Flask serve a API (`/api/...`) e, em produção, o build
do frontend (`frontend/dist/`) — sem nginx na frente, sem CORS, mesma
origem sempre. Em desenvolvimento, o Vite roda à parte (`npm run dev`) com
proxy de `/api` pra aqui (`frontend/vite.config.ts`), e esta rota estática
nunca entra em jogo porque o navegador fala com o Vite, não com o Flask.
"""

from __future__ import annotations

import logging
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


def _agendar_auto_envio_pre_carga(config: Config) -> None:
    """Só roda quando `AUTO_ENVIO_PRE_CARGA_ATIVO=true` (`load_config` já
    garante `redis_url` presente nesse caso). O lock no Redis
    (`cargas_fusion/auto_envio.py`) é o que evita os 2 workers do
    gunicorn disparando o mesmo tick em dobro — nada aqui depende de
    quantos workers existem."""
    ciss_client = CissClient(config.ciss_db) if config.ciss_db else None
    fusion_client = FusionClient(config.fusion) if config.fusion else None
    fusion_portal_client = FusionPortalClient(config.fusion_portal) if config.fusion_portal else None
    redis_client = redis.Redis.from_url(config.redis_url)

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
    app = Flask(__name__)
    config = config or load_config()
    app.config["APP_CONFIG"] = config
    app.config["SECRET_KEY"] = config.secret_key

    app.config["CISS_DB_CLIENT"] = CissClient(config.ciss_db) if config.ciss_db else None
    app.config["FUSION_CLIENT"] = FusionClient(config.fusion) if config.fusion else None
    app.config["FUSION_PORTAL_CLIENT"] = (
        FusionPortalClient(config.fusion_portal) if config.fusion_portal else None
    )

    database.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(cargas_fusion_bp)

    if config.auto_envio_pre_carga_ativo:
        _agendar_auto_envio_pre_carga(config)

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
