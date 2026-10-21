"""fusion-service: integração com o Fusion DMS, independente do meta-natal.

Monolito: este mesmo Flask serve a API (`/api/...`) e, em produção, o build
do frontend (`frontend/dist/`) — sem nginx na frente, sem CORS, mesma
origem sempre. Em desenvolvimento, o Vite roda à parte (`npm run dev`) com
proxy de `/api` pra aqui (`frontend/vite.config.ts`), e esta rota estática
nunca entra em jogo porque o navegador fala com o Vite, não com o Flask.
"""

from __future__ import annotations

from pathlib import Path

from flask import Flask, send_from_directory

import database
from auth.routes import bp as auth_bp
from cargas_fusion.routes import bp as cargas_fusion_bp
from config import load_config
from integrations.ciss import CissClient
from integrations.fusion import FusionClient
from integrations.fusion_portal import FusionPortalClient

FRONTEND_DIST = Path(__file__).resolve().parent / "frontend" / "dist"


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
