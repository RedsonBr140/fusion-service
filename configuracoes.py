"""Configuração da integração guardada no sqlite (tabela `configuracoes`).

Substitui o `.env` para tudo que não é infraestrutura do pod
(`config.CHAVES_INFRA` continua no ambiente). As chaves têm o mesmo nome
das antigas variáveis de ambiente, então `config.load_config` monta a
`Config` igualzinho, só que a partir de `{**infra, **banco}`.

- **Lida uma vez no boot.** Salvar pela tela grava aqui, mas o processo
  só passa a usar depois de reiniciar (`POST /api/admin/reiniciar` ou
  rollout). Exceção: `CISS_API_URL`/`CISS_CA_BUNDLE`, que o login lê ao
  vivo (`auth/service.py`), senão o setup inicial exigiria reinício.
- **Semente do ambiente, uma vez só.** Na primeira subida com esta tabela,
  `importar_ambiente` copia o que existir no ambiente (deploy antigo
  continua funcionando sem redigitar nada) e marca `_IMPORTADO_DO_AMBIENTE`.
  Depois disso o ambiente é ignorado para estas chaves: apagar um valor na
  tela não "ressuscita" o do Secret no próximo boot.
- **Segredo em texto puro** (decisão do negócio, 2026-11-27): a proteção é
  o acesso ao PVC. A API nunca devolve o valor de um campo `segredo` (só
  se está definido), e o histórico nunca guarda o valor.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass

import config as config_mod
from config import CHAVES_INFRA, Config, ConfigError

logger = logging.getLogger(__name__)

CHAVE_IMPORTADO = "_IMPORTADO_DO_AMBIENTE"
CHAVE_SECRET = "SECRET_KEY"


@dataclass(frozen=True, slots=True)
class Campo:
    chave: str
    rotulo: str
    grupo: str
    tipo: str  # "texto" | "segredo" | "bool" | "inteiro" | "datahora"
    descricao: str = ""
    perigoso: bool = False
    """Exige confirmação explícita ao mudar e gera aviso no Discord."""
    padrao: str | None = None


GRUPOS: tuple[tuple[str, str], ...] = (
    ("ciss", "CISS — autenticação"),
    ("ciss_db", "CISS DB2"),
    ("fusion", "Fusion — webservice SOAP"),
    ("fusion_portal", "Fusion — portal web"),
    ("discord", "Discord"),
    ("recursos", "Recursos"),
    ("ciclos", "Ciclos automáticos"),
)

CAMPOS: tuple[Campo, ...] = (
    Campo("CISS_API_URL", "URL da API do CISS", "ciss", "texto",
          "Usada no login. Lida ao vivo: mudar não exige reinício."),
    Campo("CISS_CA_BUNDLE", "CA bundle", "ciss", "texto",
          "Caminho de um certificado CA dentro do pod (opcional)."),
    Campo("CISS_DB_DRIVER", "Driver ODBC", "ciss_db", "texto", "Todos os campos do grupo, ou nenhum."),
    Campo("CISS_DB_HOST", "Host", "ciss_db", "texto"),
    Campo("CISS_DB_PORT", "Porta", "ciss_db", "inteiro", padrao="50000"),
    Campo("CISS_DB_NAME", "Banco", "ciss_db", "texto"),
    Campo("CISS_DB_USER", "Usuário", "ciss_db", "texto"),
    Campo("CISS_DB_PASSWORD", "Senha", "ciss_db", "segredo"),
    Campo("CISS_DB_TIMEOUT", "Timeout (s)", "ciss_db", "inteiro", padrao="10"),
    Campo("FUSION_API_URL", "URL do webservice", "fusion", "texto", "Todos os campos do grupo, ou nenhum."),
    Campo("FUSION_LOGIN", "Login", "fusion", "texto"),
    Campo("FUSION_SENHA", "Senha", "fusion", "segredo"),
    Campo("FUSION_EMPRESA_ID", "Id da empresa no Fusion", "fusion", "texto",
          "Id interno do Fusion (não o CNPJ), usado no heartbeat."),
    Campo("FUSION_PORTAL_URL", "URL do portal", "fusion_portal", "texto", "Todos os campos do grupo, ou nenhum."),
    Campo("FUSION_PORTAL_LOGIN", "Login", "fusion_portal", "texto"),
    Campo("FUSION_PORTAL_SENHA", "Senha", "fusion_portal", "segredo"),
    Campo("DISCORD_WEBHOOK_URL", "Webhook de erros", "discord", "segredo",
          "Erros (com @everyone) e avisos. A URL carrega o token do webhook."),
    Campo("DISCORD_WEBHOOK_ATIVIDADE_URL", "Webhook de atividade", "discord", "segredo",
          "Toda ação da integração."),
    Campo("MOSTRAR_CARGAS_CISS", "Mostrar cargas reais do CISS", "recursos", "bool",
          "Caminho legado: carga real (RETIRADA_PRODUTOS) na API e na tela.", perigoso=True, padrao="false"),
    Campo("SOMENTE_PEDIDOS_SOLTOS", "Somente pedidos soltos", "recursos", "bool",
          "Bloqueia o envio empacotado em saveCarga. Desligar reabre o caminho legado.",
          perigoso=True, padrao="true"),
    Campo("AUTO_ENVIO_PRE_CARGA_ATIVO", "Envio automático de pré-carga", "recursos", "bool",
          "Ciclo em background que manda pré-carga de pedido único ao Fusion sozinho. Exige REDIS_URL.",
          perigoso=True, padrao="false"),
    Campo("RASTREIO_INT_ERP_ATIVO", "Rastreio getIntErp", "recursos", "bool",
          "Única escrita no CISS DB2. getIntErp é DESTRUTIVO. Sozinho, só roda pelo botão. Exige REDIS_URL.",
          perigoso=True, padrao="false"),
    Campo("RASTREIO_INT_ERP_AUTOMATICO", "Rastreio em agenda", "recursos", "bool",
          "Roda o rastreio sozinho, escrevendo no CISS sem ninguém clicar. Exige o rastreio ligado.",
          perigoso=True, padrao="false"),
    Campo("AUTO_ENVIO_PRE_CARGA_INTERVALO_SEGUNDOS", "Envio automático — intervalo (s)", "ciclos", "inteiro",
          padrao="300"),
    Campo("AUTO_ENVIO_PRE_CARGA_JANELA_HORAS", "Envio automático — janela (h)", "ciclos", "inteiro",
          padrao="24"),
    Campo("AUTO_ENVIO_PRE_CARGA_CORTE", "Envio automático — corte", "ciclos", "datahora",
          "Pré-carga anterior a isto nunca é tocada. Vazio = 2026-10-28T08:00:00.", perigoso=True),
    Campo("RASTREIO_INT_ERP_INTERVALO_SEGUNDOS", "Rastreio — intervalo (s)", "ciclos", "inteiro",
          padrao="120"),
)

POR_CHAVE: dict[str, Campo] = {campo.chave: campo for campo in CAMPOS}

_VERDADEIRO = {"1", "true", "sim", "yes"}


def ler_todos(conn: sqlite3.Connection) -> dict[str, str]:
    """Só as chaves públicas (sem `_...` internas)."""
    return {
        linha["chave"]: linha["valor"]
        for linha in conn.execute("SELECT chave, valor FROM configuracoes WHERE chave NOT LIKE '\\_%' ESCAPE '\\'")
    }


def ler(conn: sqlite3.Connection, chave: str) -> str | None:
    linha = conn.execute("SELECT valor FROM configuracoes WHERE chave = ?", (chave,)).fetchone()
    return linha["valor"] if linha else None


def importar_ambiente(conn: sqlite3.Connection, env: Mapping[str, str]) -> list[str]:
    """Semente única do ambiente pro banco. Devolve as chaves importadas
    (só nomes vão pro log). Idempotente e seguro com 2 workers subindo
    juntos: o marcador entra com `INSERT OR IGNORE` na mesma transação."""
    env = config_mod._sem_aspas_envolventes({k: v for k, v in env.items() if isinstance(v, str)})
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if ler(conn, CHAVE_IMPORTADO) is not None:
            return []
        importadas = []
        for chave in [*POR_CHAVE, CHAVE_SECRET]:
            valor = (env.get(chave) or "").strip()
            if not valor:
                continue
            cursor = conn.execute(
                "INSERT OR IGNORE INTO configuracoes (chave, valor, atualizado_por) VALUES (?, ?, 'ambiente')",
                (chave, valor),
            )
            if cursor.rowcount:
                importadas.append(chave)
        conn.execute("INSERT INTO configuracoes (chave, valor) VALUES (?, '1')", (CHAVE_IMPORTADO,))
    if importadas:
        logger.info("Configuração importada do ambiente (uma vez só): %s", ", ".join(importadas))
    return importadas


def garantir_secret_key(conn: sqlite3.Connection) -> str:
    """Gera a `SECRET_KEY` na primeira subida. `INSERT OR IGNORE` + releitura:
    dois workers subindo juntos acabam com a mesma chave."""
    with conn:
        conn.execute(
            "INSERT OR IGNORE INTO configuracoes (chave, valor, atualizado_por) VALUES (?, ?, 'gerada')",
            (CHAVE_SECRET, secrets.token_urlsafe(48)),
        )
    return ler(conn, CHAVE_SECRET)


def infra_do_ambiente(env: Mapping[str, str]) -> dict[str, str]:
    env = config_mod._sem_aspas_envolventes({k: v for k, v in env.items() if isinstance(v, str)})
    return {chave: env[chave] for chave in CHAVES_INFRA if env.get(chave)}


def carregar_config(conn: sqlite3.Connection, infra: Mapping[str, str]) -> Config:
    """Boot: banco + infra, modo tolerante (ver `config.py`)."""
    valores = {**ler_todos(conn), CHAVE_SECRET: garantir_secret_key(conn), **infra}
    return config_mod.load_config(valores, estrito=False)


def _normalizar(campo: Campo, valor: object) -> str | None:
    """`None`/vazio = apagar (volta ao padrão). Levanta `ConfigError`."""
    if valor is None:
        return None
    if campo.tipo == "bool":
        if isinstance(valor, bool):
            return "true" if valor else "false"
        texto = str(valor).strip().lower()
        if not texto:
            return None
        return "true" if texto in _VERDADEIRO else "false"
    texto = str(valor).strip()
    if not texto:
        return None
    if campo.tipo == "inteiro":
        try:
            if int(texto) <= 0:
                raise ValueError
        except ValueError:
            raise ConfigError(f"{campo.rotulo} precisa ser um inteiro positivo.") from None
    return texto


def _efetivo(campo: Campo, valor: str | None) -> str | None:
    return valor if valor is not None else campo.padrao


def salvar(
    conn: sqlite3.Connection,
    mudancas: Mapping[str, object],
    *,
    por: str,
    infra: Mapping[str, str],
    confirmar_perigosos: bool = False,
) -> dict:
    """Valida o conjunto inteiro (modo estrito) antes de gravar qualquer
    coisa. Levanta `ConfigError` (400) ou `ConfirmacaoNecessaria` (409).
    Devolve `{"alteradas": [...], "perigosas": [...]}`."""
    desconhecidas = [chave for chave in mudancas if chave not in POR_CHAVE]
    if desconhecidas:
        raise ConfigError(f"Chave desconhecida: {', '.join(sorted(desconhecidas))}")

    atuais = ler_todos(conn)
    novos = {chave: _normalizar(POR_CHAVE[chave], valor) for chave, valor in mudancas.items()}
    alteradas = [chave for chave, valor in novos.items() if atuais.get(chave) != valor]
    if not alteradas:
        return {"alteradas": [], "perigosas": []}

    candidato = {chave: valor for chave, valor in atuais.items()}
    for chave in alteradas:
        if novos[chave] is None:
            candidato.pop(chave, None)
        else:
            candidato[chave] = novos[chave]
    config_mod.load_config(
        {**candidato, CHAVE_SECRET: ler(conn, CHAVE_SECRET) or "x", **infra}, estrito=True
    )

    perigosas = [
        chave
        for chave in alteradas
        if POR_CHAVE[chave].perigoso
        and _efetivo(POR_CHAVE[chave], atuais.get(chave)) != _efetivo(POR_CHAVE[chave], novos[chave])
    ]
    if perigosas and not confirmar_perigosos:
        raise ConfirmacaoNecessaria(perigosas)

    with conn:
        for chave in alteradas:
            campo = POR_CHAVE[chave]
            anterior, novo = atuais.get(chave), novos[chave]
            if novo is None:
                conn.execute("DELETE FROM configuracoes WHERE chave = ?", (chave,))
            else:
                conn.execute(
                    "INSERT INTO configuracoes (chave, valor, atualizado_por) VALUES (?, ?, ?) "
                    "ON CONFLICT(chave) DO UPDATE SET valor = excluded.valor, "
                    "atualizado_em = CURRENT_TIMESTAMP, atualizado_por = excluded.atualizado_por",
                    (chave, novo, por),
                )
            conn.execute(
                "INSERT INTO configuracoes_historico (chave, valor_anterior, valor_novo, alterado_por) "
                "VALUES (?, ?, ?, ?)",
                (
                    chave,
                    None if campo.tipo == "segredo" else anterior,
                    None if campo.tipo == "segredo" else novo,
                    por,
                ),
            )
    logger.info("Configuração alterada por %s: %s", por, ", ".join(alteradas))
    return {"alteradas": alteradas, "perigosas": perigosas}


class ConfirmacaoNecessaria(Exception):
    def __init__(self, chaves: list[str]):
        super().__init__(", ".join(chaves))
        self.chaves = chaves


def descrever(conn: sqlite3.Connection, valores_no_boot: Mapping[str, str]) -> dict:
    """Pra tela. Segredo nunca sai: só `definido`. `pendente_reinicio`
    compara com o que este processo leu no boot."""
    atuais = ler_todos(conn)
    linhas = {
        linha["chave"]: linha
        for linha in conn.execute("SELECT chave, atualizado_em, atualizado_por FROM configuracoes")
    }
    grupos = []
    for chave_grupo, rotulo in GRUPOS:
        campos = []
        for campo in CAMPOS:
            if campo.grupo != chave_grupo:
                continue
            valor = atuais.get(campo.chave)
            linha = linhas.get(campo.chave)
            campos.append(
                {
                    "chave": campo.chave,
                    "rotulo": campo.rotulo,
                    "tipo": campo.tipo,
                    "descricao": campo.descricao,
                    "perigoso": campo.perigoso,
                    "padrao": campo.padrao,
                    "definido": valor is not None,
                    "valor": None if campo.tipo == "segredo" else valor,
                    "atualizado_por": linha["atualizado_por"] if linha else None,
                    "pendente_reinicio": (
                        campo.chave not in ("CISS_API_URL", "CISS_CA_BUNDLE")
                        and valor != valores_no_boot.get(campo.chave)
                    ),
                }
            )
        grupos.append({"chave": chave_grupo, "rotulo": rotulo, "campos": campos})
    return {
        "grupos": grupos,
        "pendente_reinicio": any(c["pendente_reinicio"] for g in grupos for c in g["campos"]),
    }
