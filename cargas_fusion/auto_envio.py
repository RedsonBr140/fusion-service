"""Ciclo automático de envio de pré-carga solta — `saveEntregaServico`
sem `saveCarga`, mesmo modo de `service.processar_envio_pre_carga_solta`,
só que sem alguém clicando "Enviar pedido solto" na tela.

Só pré-carga de pedido único entra aqui. Pré-carga real (carga do
`RETIRADA_PRODUTOS`) e pré-carga em modo bundle (`PC_...`, `saveCarga`)
são legado — este módulo nunca os toca, não importa o que
`SOMENTE_PEDIDOS_SOLTOS`/`MOSTRAR_CARGAS_CISS` estejam configurados.

O lock contra os 2 workers do gunicorn (`k8s/backend.yaml`, 1 réplica,
`Dockerfile` com `--workers 2`) dispararem o mesmo tick em dobro vive no
Redis (`redis-py`, `Lock.acquire(blocking=False)`) — um mutex comum não
resolveria isso: ele só evitaria os dois rodarem *ao mesmo tempo*, não
evitaria que um rodasse depois do outro soltar o lock, duplicando o
envio mesmo assim. O que resolve é "só quem reivindica o tick executa, o
outro pula o tick inteiro", que é o que `acquire(blocking=False)` faz —
devolve `False` na hora se outro processo já tem o lock.

Pausa (`pausar`/`retomar`) e estatísticas da última execução ficam em
sqlite (`cargas_fusion_auto_envio_estado`) — não tem disputa de
concorrência real aí (é só configuração/leitura ocasional), diferente do
lock em si.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta

from integrations import discord
from integrations.ciss import CissClient
from integrations.fusion import FusionClient
from integrations.fusion_portal import FusionPortalClient

from . import service

logger = logging.getLogger(__name__)

NOME_LOCK = "fusion-service:auto-envio-pre-carga"

# Teto de segurança pra get_pre_cargas — alto o bastante pra nunca
# truncar uma janela de verdade, mas finito.
_LIMITE_PRE_CARGAS_POR_CICLO = 1000

# Quantas pré-cargas mais lentas guardar por ciclo, pra responder "por
# quê" sem guardar uma linha por item processado (ilimitado, cresceria
# sem controle) — top N já mostra se é sempre a mesma empresa/padrão.
_TOP_N_ITENS_LENTOS = 10

# Quantos ciclos manter em `cargas_fusion_auto_envio_execucoes` — poda a
# cada INSERT (ver `_podar_execucoes`), não um job separado.
_RETENCAO_EXECUCOES = 200


class _ClienteCronometrado:
    """Proxy transparente em volta de um client (CISS/Fusion/portal) que
    cronometra toda chamada de método e acumula em `chamadas` (lista
    compartilhada entre os três clients do ciclo) — método, não o quê a
    chamada fez, por isso não precisa tocar em `service.py` nenhuma vez:
    `get_itens_pre_carga` chamado duas vezes por pré-carga (uma em
    `executar_ciclo`, outra dentro de `preparar_envio_pre_carga_solta`)
    aparece como dois registros iguais, exatamente a duplicação real.

    `prefixo` rotula o método (`"ciss.get_itens_pre_carga"`) pra não
    confundir métodos de nomes iguais em clients diferentes (não há caso
    hoje, mas não custa)."""

    def __init__(self, cliente, chamadas: list[dict], prefixo: str) -> None:
        self._cliente = cliente
        self._chamadas = chamadas
        self._prefixo = prefixo

    def __getattr__(self, nome: str):
        atributo = getattr(self._cliente, nome)
        if not callable(atributo):
            return atributo

        def _cronometrada(*args, **kwargs):
            inicio = time.monotonic()
            try:
                return atributo(*args, **kwargs)
            finally:
                duracao_ms = int((time.monotonic() - inicio) * 1000)
                self._chamadas.append({"metodo": f"{self._prefixo}.{nome}", "duracao_ms": duracao_ms})

        return _cronometrada


def _agregar_breakdown(chamadas: list[dict]) -> dict[str, dict]:
    breakdown: dict[str, dict] = {}
    for chamada in chamadas:
        entrada = breakdown.setdefault(chamada["metodo"], {"duracao_ms": 0, "chamadas": 0})
        entrada["duracao_ms"] += chamada["duracao_ms"]
        entrada["chamadas"] += 1
    return breakdown


def obter_estado(conn: sqlite3.Connection) -> dict:
    linha = conn.execute("SELECT * FROM cargas_fusion_auto_envio_estado WHERE id = 1").fetchone()
    return {
        "pausado": bool(linha["pausado"]),
        "ultima_execucao_em": linha["ultima_execucao_em"],
        "ultima_execucao_duracao_ms": linha["ultima_execucao_duracao_ms"],
        "ultima_execucao_candidatas": linha["ultima_execucao_candidatas"],
        "ultima_execucao_enviadas": linha["ultima_execucao_enviadas"],
        "ultima_execucao_falhas": linha["ultima_execucao_falhas"],
    }


def pausar(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            "UPDATE cargas_fusion_auto_envio_estado SET pausado = 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = 1"
        )


def retomar(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute(
            "UPDATE cargas_fusion_auto_envio_estado SET pausado = 0, updated_at = CURRENT_TIMESTAMP "
            "WHERE id = 1"
        )


def _esta_pausado(conn: sqlite3.Connection) -> bool:
    linha = conn.execute("SELECT pausado FROM cargas_fusion_auto_envio_estado WHERE id = 1").fetchone()
    return bool(linha["pausado"])


def _registrar_execucao(
    conn: sqlite3.Connection, *, duracao_ms: int, candidatas: int, enviadas: int, falhas: int
) -> None:
    with conn:
        conn.execute(
            "UPDATE cargas_fusion_auto_envio_estado SET "
            "ultima_execucao_em = CURRENT_TIMESTAMP, ultima_execucao_duracao_ms = ?, "
            "ultima_execucao_candidatas = ?, ultima_execucao_enviadas = ?, "
            "ultima_execucao_falhas = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1",
            (duracao_ms, candidatas, enviadas, falhas),
        )


def _podar_execucoes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "DELETE FROM cargas_fusion_auto_envio_execucoes WHERE id NOT IN ("
        "SELECT id FROM cargas_fusion_auto_envio_execucoes ORDER BY id DESC LIMIT ?)",
        (_RETENCAO_EXECUCOES,),
    )


def _registrar_execucao_detalhada(
    conn: sqlite3.Connection,
    *,
    duracao_ms: int,
    candidatas: int,
    enviadas: int,
    falhas: int,
    breakdown: dict[str, dict],
    itens_lentos: list[dict],
) -> None:
    """Histórico pra diagnóstico — tabela separada de
    `cargas_fusion_auto_envio_estado` (que só guarda o último ciclo, sem
    detalhe de etapa) porque este serviço não tem ORM/migration pra
    adicionar colunas de histórico numa tabela de linha única; `with
    conn` cobre o INSERT e a poda na mesma transação."""
    with conn:
        conn.execute(
            "INSERT INTO cargas_fusion_auto_envio_execucoes "
            "(duracao_ms, candidatas, enviadas, falhas, breakdown, itens_lentos) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                duracao_ms,
                candidatas,
                enviadas,
                falhas,
                json.dumps(breakdown, ensure_ascii=False),
                json.dumps(itens_lentos, ensure_ascii=False),
            ),
        )
        _podar_execucoes(conn)


def listar_execucoes(conn: sqlite3.Connection, *, limite: int = 50) -> list[dict]:
    linhas = conn.execute(
        "SELECT * FROM cargas_fusion_auto_envio_execucoes ORDER BY id DESC LIMIT ?",
        (limite,),
    ).fetchall()
    return [
        {
            "executado_em": service._sqlite_utc_iso(linha["executado_em"]),
            "duracao_ms": linha["duracao_ms"],
            "candidatas": linha["candidatas"],
            "enviadas": linha["enviadas"],
            "falhas": linha["falhas"],
            "breakdown": json.loads(linha["breakdown"]),
            "itens_lentos": json.loads(linha["itens_lentos"]),
        }
        for linha in reversed(linhas)
    ]


def resumo_multi_pedido(conn: sqlite3.Connection, *, top_n: int = 5) -> dict:
    """`total` de pré-cargas multi-pedido já vistas pelo ciclo (nunca
    enviadas por ele) e as `top_n` empresas que mais cometem isso — pra
    responder "quantas" e "qual empresa mais" (pedido explícito, não é
    só um skip silencioso)."""
    total = conn.execute("SELECT COUNT(*) AS n FROM cargas_fusion_precarga_multi_pedido").fetchone()["n"]
    por_empresa = conn.execute(
        "SELECT id_empresa, COUNT(*) AS qtd FROM cargas_fusion_precarga_multi_pedido "
        "GROUP BY id_empresa ORDER BY qtd DESC LIMIT ?",
        (top_n,),
    ).fetchall()
    return {
        "total": total,
        "por_empresa": [{"id_empresa": linha["id_empresa"], "qtd": linha["qtd"]} for linha in por_empresa],
    }


def resumo_multi_pedido_por_dia(conn: sqlite3.Connection, *, dias: int = 14) -> list[dict]:
    """Pré-cargas multi-pedido vistas *pela primeira vez* por dia (fuso
    de Brasília, mesmo motivo de `_metricas_fila` em `service.py`) — não
    é um bug com tendência a corrigir (ver nota em CLAUDE.md: é exclusão
    permanente do escopo de pedido solto), é só volume pra acompanhar
    quanto o negócio bate nesse limite ao longo do tempo."""
    linhas = conn.execute(
        "SELECT date(primeira_vez_em, '-3 hours') AS dia, COUNT(*) AS qtd "
        "FROM cargas_fusion_precarga_multi_pedido "
        "WHERE primeira_vez_em >= datetime('now', ? || ' days') "
        "GROUP BY dia ORDER BY dia",
        (f"-{dias}",),
    ).fetchall()
    return [{"dia": linha["dia"], "qtd": linha["qtd"]} for linha in linhas]


def _registrar_multi_pedido(conn: sqlite3.Connection, id_empresa: int, id_precarga: int, qtd_pedidos: int) -> bool:
    """Upsert em `cargas_fusion_precarga_multi_pedido`. Devolve `True` só
    na primeira vez que esta pré-carga é vista (pra `executar_ciclo`
    decidir se notifica o Discord — nunca a cada tick, senão vira ruído)."""
    with conn:
        ja_existia = conn.execute(
            "SELECT 1 FROM cargas_fusion_precarga_multi_pedido WHERE id_empresa = ? AND id_precarga = ?",
            (id_empresa, id_precarga),
        ).fetchone()
        conn.execute(
            "INSERT INTO cargas_fusion_precarga_multi_pedido "
            "(id_empresa, id_precarga, qtd_pedidos) VALUES (?, ?, ?) "
            "ON CONFLICT (id_empresa, id_precarga) DO UPDATE SET "
            "qtd_pedidos = excluded.qtd_pedidos, ultima_vez_em = CURRENT_TIMESTAMP",
            (id_empresa, id_precarga, qtd_pedidos),
        )
    return ja_existia is None


def _adquirir_lock(redis_client, timeout_segundos: int):
    """`None` se outro worker já está com o lock neste tick — quem chama
    pula o ciclo inteiro, nunca espera."""
    lock = redis_client.lock(NOME_LOCK, timeout=timeout_segundos)
    return lock if lock.acquire(blocking=False) else None


def _liberar_lock(lock) -> None:
    try:
        lock.release()
    except Exception:
        # TTL já expirou sozinho (ciclo demorou mais que `timeout_segundos`)
        # — esperado, não é falha real; nunca propaga pra não mascarar o
        # resultado de verdade do ciclo.
        logger.warning("Lock de auto-envio de pré-carga já havia expirado ao tentar liberar.")


def executar_ciclo(
    conn: sqlite3.Connection,
    redis_client,
    ciss_client: CissClient,
    fusion_client: FusionClient | None,
    fusion_portal_client: FusionPortalClient | None,
    *,
    janela_horas: int,
    corte: datetime,
    discord_webhook_url: str | None,
    lock_timeout_segundos: int,
) -> dict:
    """`corte`: pré-carga com `DTMOVIMENTO` anterior a isto nunca é
    tocada — vem de `Config.auto_envio_pre_carga_corte`
    (`AUTO_ENVIO_PRE_CARGA_CORTE`, default fixo no código pra produção).
    Passado explicitamente em vez de uma constante do módulo justamente
    pra permitir um teste local/live deliberado contra uma janela mais
    antiga, sem precisar mudar o default de produção pra isso."""
    if _esta_pausado(conn):
        return {"status": "pausado"}

    if ciss_client is None:
        # Nunca deveria acontecer em produção (CISS DB2 é obrigatório pra
        # qualquer coisa deste serviço), mas um ambiente de teste local
        # pode legitimamente ligar AUTO_ENVIO_PRE_CARGA_ATIVO sem CISS
        # configurado só pra exercitar o lock/scheduler — sem este guard,
        # isso crasharia em get_pre_cargas.
        return {"status": "ciss_nao_configurado"}

    agora = datetime.now()
    if agora < corte:
        # `max(agora - janela_horas, corte)` sozinho viraria `corte` aqui
        # — maior que `agora`, uma janela invertida que `get_pre_cargas`
        # só devolveria vazia (sem erro, `BETWEEN` com início > fim não
        # bate em nenhuma linha). Sem este guard, cada tick bateria no
        # CISS DB2 de produção à toa até o corte passar, pra sempre
        # devolver nada.
        return {"status": "antes_do_corte"}

    lock = _adquirir_lock(redis_client, lock_timeout_segundos)
    if lock is None:
        return {"status": "pulado", "motivo": "ciclo já em andamento em outro worker"}

    # Proxies cronometrados em volta dos três clients — toda chamada de
    # método vira um registro em `chamadas` (método + duração), sem
    # precisar passar nenhum cronômetro pelas funções de `service.py`.
    # Mesma instância em todo o ciclo, não uma por pré-carga: o que
    # distingue um item do outro é a posição na lista (`idx_inicio_item`
    # abaixo), não o proxy em si.
    chamadas: list[dict] = []
    ciss = _ClienteCronometrado(ciss_client, chamadas, "ciss")
    fusion = _ClienteCronometrado(fusion_client, chamadas, "fusion") if fusion_client is not None else None
    portal = (
        _ClienteCronometrado(fusion_portal_client, chamadas, "portal")
        if fusion_portal_client is not None
        else None
    )

    inicio = time.monotonic()
    candidatas = 0
    enviadas = 0
    falhas = 0
    itens_lentos: list[dict] = []
    try:
        inicio_janela = max(agora - timedelta(hours=janela_horas), corte)

        pre_cargas = ciss.get_pre_cargas(inicio_janela, agora, limite=_LIMITE_PRE_CARGAS_POR_CICLO)
        ja_enviadas = service.pre_cargas_ja_enviadas(conn)

        for pre_carga in pre_cargas:
            id_empresa = pre_carga["IDEMPRESA"]
            id_precarga = pre_carga["IDPRECARGA"]
            if (id_empresa, id_precarga) in ja_enviadas:
                continue

            inicio_item = time.monotonic()
            idx_inicio_item = len(chamadas)
            try:
                itens_todos = ciss.get_itens_pre_carga(id_precarga)
                qtd_pedidos = service.contar_pedidos(itens_todos)

                if qtd_pedidos != 1:
                    primeira_vez = _registrar_multi_pedido(conn, id_empresa, id_precarga, qtd_pedidos)
                    if primeira_vez:
                        discord.notificar(
                            discord_webhook_url,
                            f"Pré-carga {service.codigo_pre_carga(id_empresa, id_precarga)} "
                            f"(empresa {id_empresa}) tem {qtd_pedidos} pedidos — fora do escopo "
                            "do envio automático (só pedido único).",
                        )
                    continue

                candidatas += 1
                resultado_preparo = service.preparar_envio_pre_carga_solta(
                    ciss, id_empresa, id_precarga
                )
                if resultado_preparo is None:
                    continue
                payload_entregas_servico, avisos, avisos_bloqueantes, _itens, clientes = resultado_preparo

                saida = service.processar_envio_pre_carga_solta(
                    conn,
                    fusion,
                    id_empresa,
                    id_precarga,
                    service.codigo_pre_carga(id_empresa, id_precarga),
                    payload_entregas_servico,
                    avisos,
                    avisos_bloqueantes,
                    clientes_por_idclifor=clientes,
                    fusion_portal_client=portal,
                )

                if saida["status"] == "pendente":
                    falhas += 1
                    fila = saida.get("fila") or {}
                    if fila.get("tentativas") == 1:
                        discord.notificar(
                            discord_webhook_url,
                            f"Falha ao enviar pré-carga {service.codigo_pre_carga(id_empresa, id_precarga)} "
                            f"(empresa {id_empresa}) automaticamente: {saida.get('motivo')}",
                        )
                elif saida["status"] == "enviada":
                    enviadas += 1
            except Exception:
                falhas += 1
                logger.exception(
                    "Erro inesperado processando pré-carga %s/%s no ciclo automático",
                    id_empresa,
                    id_precarga,
                )
                discord.notificar(
                    discord_webhook_url,
                    f"Erro inesperado processando pré-carga {id_empresa}/{id_precarga} "
                    "no ciclo automático — ver logs.",
                )
            finally:
                duracao_item_ms = int((time.monotonic() - inicio_item) * 1000)
                chamadas_item = chamadas[idx_inicio_item:]
                dominante = max(chamadas_item, key=lambda c: c["duracao_ms"], default=None)
                itens_lentos.append(
                    {
                        "id_empresa": id_empresa,
                        "id_precarga": id_precarga,
                        "duracao_ms": duracao_item_ms,
                        "metodo_dominante": dominante["metodo"] if dominante else None,
                        "duracao_dominante_ms": dominante["duracao_ms"] if dominante else 0,
                    }
                )

        duracao_ms = int((time.monotonic() - inicio) * 1000)
        _registrar_execucao(conn, duracao_ms=duracao_ms, candidatas=candidatas, enviadas=enviadas, falhas=falhas)
        itens_lentos.sort(key=lambda i: i["duracao_ms"], reverse=True)
        _registrar_execucao_detalhada(
            conn,
            duracao_ms=duracao_ms,
            candidatas=candidatas,
            enviadas=enviadas,
            falhas=falhas,
            breakdown=_agregar_breakdown(chamadas),
            itens_lentos=itens_lentos[:_TOP_N_ITENS_LENTOS],
        )
        return {
            "status": "executado",
            "candidatas": candidatas,
            "enviadas": enviadas,
            "falhas": falhas,
            "duracao_ms": duracao_ms,
        }
    finally:
        _liberar_lock(lock)
