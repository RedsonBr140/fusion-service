import logging

from config import load_config

BASE = {"SECRET_KEY": "s", "CISS_API_URL": "https://ciss.example"}

PORTAL = {
    "FUSION_PORTAL_URL": "https://portal.example",
    "FUSION_PORTAL_LOGIN": "automacao",
}


def test_aspas_duplas_envolventes_sao_removidas():
    config = load_config({**BASE, **PORTAL, "FUSION_PORTAL_SENHA": '"segredo1"'})
    assert config.fusion_portal.senha == "segredo1"


def test_aspas_simples_envolventes_sao_removidas():
    config = load_config({**BASE, "DISCORD_WEBHOOK_URL": "'https://discord.example/hook'"})
    assert config.discord_webhook_url == "https://discord.example/hook"


def test_aspas_no_meio_ou_desemparelhadas_ficam_intactas():
    config = load_config({**BASE, **PORTAL, "FUSION_PORTAL_SENHA": "\"abc'"})
    assert config.fusion_portal.senha == "\"abc'"
    config = load_config({**BASE, **PORTAL, "FUSION_PORTAL_SENHA": 'a"b"c'})
    assert config.fusion_portal.senha == 'a"b"c'


def test_log_cita_so_a_chave_nunca_o_valor(caplog):
    with caplog.at_level(logging.WARNING, logger="config"):
        load_config({**BASE, **PORTAL, "FUSION_PORTAL_SENHA": '"segredo1"'})
    assert "FUSION_PORTAL_SENHA" in caplog.text
    assert "segredo1" not in caplog.text
