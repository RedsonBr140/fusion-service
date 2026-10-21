from pathlib import Path

import pytest

from app import create_app
from config import Config


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config(
        secret_key="chave-de-teste-suficientemente-longa",
        ciss_api_url="https://ciss.test",
        ciss_ca_bundle=None,
        ciss_db=None,
        fusion=None,
        fusion_portal=None,
        database_path=str(tmp_path / "fusion_service.db"),
        port=5050,
    )


@pytest.fixture
def app(config: Config):
    application = create_app(config)
    application.config["TESTING"] = True
    return application


@pytest.fixture
def client(app):
    return app.test_client()
