"""Setup inicial: como o primeiro admin entra num banco sem admin nenhum.

Sem isto, uma instalação nova não tinha como logar: o login exige estar
na allow-list (`usuarios_locais`), e só quem já está nela concede acesso.

Fluxo (decisão do negócio, 2026-11-27):

1. No boot, enquanto não houver admin ativo, `preparar` gera um token de
   uso único (`setup_inicial`) e o grava num arquivo ao lado do banco
   (`setup-token.txt`, no PVC). **O token nunca vai pro log** (regra do
   CLAUDE.md): o log só diz onde está o arquivo. Ler o arquivo exige
   acesso ao pod (`kubectl exec ... cat`), e é isso que impede quem só
   alcança a URL de reivindicar a instalação.
2. A tela `/setup` pede o token, a URL da API do CISS e um login do CISS.
   Login continua sendo só CISS, sem senha local.
3. `concluir` autentica no CISS com a URL informada, grava a URL na
   configuração, coloca a pessoa na allow-list como admin (ou promove, se
   já estava lá: banco migrado de antes dos admins) e apaga o token.

Banco existente (de antes deste recurso) também passa por aqui: tem
usuários mas nenhum admin, então o setup fica disponível até alguém
reivindicar. O login normal continua funcionando nesse meio-tempo.
"""

from __future__ import annotations

import hmac
import logging
import secrets
import sqlite3
from pathlib import Path

from integrations import ciss_auth
from integrations.ciss_auth import CissUnavailable, InvalidCredentials

logger = logging.getLogger(__name__)

NOME_ARQUIVO_TOKEN = "setup-token.txt"


class SetupJaConcluido(Exception):
    """Já existe admin ativo — setup não está mais disponível."""


class TokenSetupInvalido(Exception):
    pass


class LoginSetupRejeitado(Exception):
    pass


class CissIndisponivelNoSetup(Exception):
    pass


class DadosSetupInvalidos(Exception):
    pass


def caminho_token(database_path: str) -> Path:
    return Path(database_path).resolve().parent / NOME_ARQUIVO_TOKEN


def setup_necessario(conn: sqlite3.Connection) -> bool:
    return conn.execute("SELECT 1 FROM usuarios_locais WHERE admin = 1 AND active = 1").fetchone() is None


def _apagar_arquivo(caminho: Path) -> None:
    try:
        caminho.unlink(missing_ok=True)
    except OSError as exc:
        logger.warning("Não consegui apagar %s (%s)", caminho, type(exc).__name__)


def preparar(conn: sqlite3.Connection, database_path: str) -> None:
    """Roda em todo boot (cada worker). `INSERT OR IGNORE` + releitura:
    os 2 workers acabam com o mesmo token."""
    caminho = caminho_token(database_path)
    if not setup_necessario(conn):
        with conn:
            conn.execute("DELETE FROM setup_inicial")
        if caminho.exists():
            _apagar_arquivo(caminho)
        return

    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO setup_inicial (id, token) VALUES (1, ?)", (secrets.token_urlsafe(32),)
        )
    token = conn.execute("SELECT token FROM setup_inicial WHERE id = 1").fetchone()["token"]
    try:
        if not caminho.exists() or caminho.read_text(encoding="utf-8").strip() != token:
            caminho.write_text(token + "\n", encoding="utf-8")
            try:
                caminho.chmod(0o600)
            except OSError:
                pass
    except OSError as exc:
        logger.error("Setup inicial pendente, mas não consegui gravar o token em %s (%s)", caminho, type(exc).__name__)
        return
    logger.warning(
        "Setup inicial pendente (nenhum admin). Abra /setup e use o token em %s "
        "(ex.: kubectl exec deploy/fusion-service-backend -- cat %s).",
        caminho,
        caminho,
    )


def concluir(
    conn: sqlite3.Connection,
    *,
    database_path: str,
    token: str,
    ciss_api_url: str,
    ciss_ca_bundle: str | None,
    username: str,
    password: str,
    name: str | None = None,
    authenticate=None,
) -> sqlite3.Row:
    authenticate = authenticate or ciss_auth.authenticate
    if not setup_necessario(conn):
        raise SetupJaConcluido

    linha = conn.execute("SELECT token FROM setup_inicial WHERE id = 1").fetchone()
    if linha is None or not hmac.compare_digest(linha["token"], token.strip()):
        logger.warning("Setup inicial: token inválido")
        raise TokenSetupInvalido

    url = ciss_api_url.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        raise DadosSetupInvalidos("A URL da API do CISS precisa começar com http:// ou https://.")
    bundle = (ciss_ca_bundle or "").strip() or None
    informado = username.strip()
    if not informado or not password:
        raise DadosSetupInvalidos("Usuário e senha do CISS são obrigatórios.")

    try:
        identidade = authenticate(informado, password, api_url=url, ca_bundle=bundle)
    except InvalidCredentials:
        raise LoginSetupRejeitado from None
    except CissUnavailable:
        raise CissIndisponivelNoSetup from None
    if not identidade.enabled:
        raise LoginSetupRejeitado

    normalizado = informado.lower()
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        # Revalida dentro da transação: dois setups simultâneos, só um ganha.
        if conn.execute("SELECT 1 FROM setup_inicial WHERE id = 1").fetchone() is None:
            raise SetupJaConcluido
        for chave, valor in (("CISS_API_URL", url), ("CISS_CA_BUNDLE", bundle)):
            if valor is None:
                conn.execute("DELETE FROM configuracoes WHERE chave = ?", (chave,))
            else:
                conn.execute(
                    "INSERT INTO configuracoes (chave, valor, atualizado_por) VALUES (?, ?, ?) "
                    "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, "
                    "atualizado_em = CURRENT_TIMESTAMP, atualizado_por = excluded.atualizado_por",
                    (chave, valor, normalizado),
                )
        codigo_ciss = str(getattr(identidade, "user_id", "") or "") or None
        existente = conn.execute(
            "SELECT id FROM usuarios_locais WHERE username = ? OR ciss_code = ? OR ciss_code = ? "
            "ORDER BY username = ? DESC LIMIT 1",
            (normalizado, normalizado, codigo_ciss, normalizado),
        ).fetchone()
        if existente is not None:
            conn.execute("UPDATE usuarios_locais SET admin = 1, active = 1 WHERE id = ?", (existente["id"],))
            id_usuario = existente["id"]
        else:
            cursor = conn.execute(
                "INSERT INTO usuarios_locais (username, ciss_code, name, active, admin) VALUES (?, ?, ?, 1, 1)",
                (normalizado, codigo_ciss, (name or "").strip() or informado),
            )
            id_usuario = cursor.lastrowid
        conn.execute("DELETE FROM setup_inicial")

    _apagar_arquivo(caminho_token(database_path))
    logger.info("Setup inicial concluído: %s é admin", normalizado)
    return conn.execute(
        "SELECT username, ciss_code, name, active, admin FROM usuarios_locais WHERE id = ?", (id_usuario,)
    ).fetchone()
