"""fusion-service: integração com o Fusion DMS, independente do meta-natal."""

from __future__ import annotations

from flask import Flask
from flask_cors import CORS

import database
from auth.routes import bp as auth_bp
from cargas_fusion.routes import bp as cargas_fusion_bp
from config import load_config
from integrations.ciss import CissClient
from integrations.fusion import FusionClient
from integrations.fusion_portal import FusionPortalClient


def create_app(config=None) -> Flask:
    app = Flask(__name__)
    config = config or load_config()
    app.config["APP_CONFIG"] = config
    app.config["SECRET_KEY"] = config.secret_key

    CORS(app, origins=list(config.allowed_origins), supports_credentials=False)

    app.config["CISS_DB_CLIENT"] = CissClient(config.ciss_db) if config.ciss_db else None
    app.config["FUSION_CLIENT"] = FusionClient(config.fusion) if config.fusion else None
    app.config["FUSION_PORTAL_CLIENT"] = (
        FusionPortalClient(config.fusion_portal) if config.fusion_portal else None
    )

    database.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(cargas_fusion_bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(port=app.config["APP_CONFIG"].port)
