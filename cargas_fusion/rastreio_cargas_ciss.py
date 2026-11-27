"""Vínculo romaneio do Fusion ↔ carga do CISS → `DBA.CARGAS_CISS_FUSION_TRACK`.

Etapa do mesmo ciclo do rastreio, depois das entregas (FK: o romaneio
precisa estar em `CARGAS_FUSION_TRACK`). Muitos pra muitos: um romaneio
leva pedidos de várias cargas do CISS, e uma carga pode se espalhar por
romaneios. Colunas: `IDEMPRESA`, `IDCARGACISS` (= `RETIRADA_PRODUTOS.
IDPLANILHA`), `IDCARGAFUSION` (= `t10_id`).

**De onde vem o par** (decisão do negócio, 2026-11-27), unindo as duas:

1. O que o próprio Fusion diz: `t10_cargas_erp` (lista separada por
   vírgula) e `ENTREGAS[].t32_carga_erp`, no formato `{IDEMPRESA}_{IDPLANILHA}`
   — é o `codigo_erp` que o caminho legado mandava no `saveCarga`. Fora
   desse formato é descartado: `PC_{empresa}_{pré-carga}` (a carga empacotada
   de pré-carga, não uma carga do CISS — os pedidos dela entram pelo item 2),
   e o próprio `t10_id`, que volta nesses campos por causa do contorno do
   `setIntErp` (`rastreio._carga_para_setinterp`).
2. O que o CISS diz, pedido a pedido: o código de cada entrega
   (`identificar_pedido`) leva à carga que carrega o item
   (`CissClient.get_cargas_ciss_do_pedido`). É o único caminho pro fluxo
   corrente (pedido solto), em que os campos do item 1 vêm vazios.
   Resultado guardado em `cargas_fusion_entrega_track.cargas_ciss`; vazio
   (pré-carga ainda não virou carga) é reconsultado nos ciclos seguintes.

**Espelho exato** (decisão do negócio): a tabela fica igual ao conteúdo
atual do romaneio — carga que sai dele sai da tabela (DELETE, só nesta
tabela, ver `ciss_escrita.TABELAS_DELETE_PERMITIDO`). Remoção só quando o
conteúdo do romaneio é conhecido por inteiro: se alguma entrega ainda não
foi consultada (ou a consulta falhou), o romaneio só ganha vínculos nesse
ciclo, nunca perde — uma falha de leitura nunca apaga vínculo válido.

Só romaneio dentro da janela de reatualização (`romaneio_expirado`); depois
disso o vínculo fica como estava.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime

from integrations import ciss_escrita
from integrations.ciss import CissDbError

from . import rastreio_entregas

logger = logging.getLogger(__name__)

TABELA = "DBA.CARGAS_CISS_FUSION_TRACK"

# Consultas ao CISS por ciclo pra descobrir a carga de entregas (uma por
# entrega). Na primeira vez há todo o histórico pra resolver; depois, só
# entregas novas e pré-cargas ainda não formadas.
LIMITE_CONSULTAS_POR_CICLO = 200

_CODIGO_CARGA = re.compile(r"(\d+)_(\d+)")


def cargas_informadas_pelo_fusion(payload: dict) -> set[tuple[int, int]]:
    valores = [payload.get("t10_cargas_erp") or ""]
    valores += [e.get("t32_carga_erp") or "" for e in payload.get("ENTREGAS") or []]
    cargas = set()
    for valor in valores:
        for token in str(valor).split(","):
            if m := _CODIGO_CARGA.fullmatch(token.strip()):
                cargas.add((int(m[1]), int(m[2])))
    return cargas


def _pedidos_do_payload(payload: dict) -> dict[str, dict]:
    return {
        (e.get("t32_pedido_original") or "").strip(): e
        for e in payload.get("ENTREGAS") or []
        if (e.get("t32_pedido_original") or "").strip()
    }


def _resolver_pendentes(
    conn: sqlite3.Connection, ciss, ativos: dict[int, dict], agora: datetime, limite: int
) -> dict[str, int]:
    """Consulta a carga das entregas ainda sem resposta (`NULL`) e reconsulta
    as que voltaram vazias — `NULL` primeiro, depois a menos recentemente
    consultada."""
    if not ativos:
        return {}
    candidatas = []
    for t10_id, dado in ativos.items():
        pedidos = _pedidos_do_payload(dado["payload"])
        for linha in conn.execute(
            "SELECT pedido_erp, cargas_ciss, cargas_ciss_em FROM cargas_fusion_entrega_track "
            "WHERE t10_id = ? AND (cargas_ciss IS NULL OR cargas_ciss = '[]')",
            (t10_id,),
        ):
            if linha["pedido_erp"] in pedidos:
                candidatas.append(
                    (linha["cargas_ciss"] is not None, linha["cargas_ciss_em"] or "", t10_id,
                     linha["pedido_erp"], pedidos[linha["pedido_erp"]])
                )
    candidatas.sort(key=lambda c: (c[0], c[1]))

    contagem = {"consultadas": 0, "sem_carga": 0, "erro_consulta": 0}
    momento = agora.strftime("%Y-%m-%d %H:%M:%S")
    for _, _, t10_id, pedido, entrega in candidatas[:limite]:
        identificado = rastreio_entregas.identificar_pedido(pedido, entrega.get("t43_codigo_filial_erp"))
        if identificado is None:
            cargas: list[tuple[int, int]] = []  # formato desconhecido: nada a consultar
        else:
            try:
                cargas = ciss.get_cargas_ciss_do_pedido(
                    identificado.formato,
                    id_orcamento=identificado.id_orcamento,
                    id_pre_nota=identificado.id_pre_nota,
                    id_empresa=identificado.id_empresa,
                    id_planilha_carga=identificado.id_planilha_carga,
                    id_precarga=identificado.id_precarga,
                )
            except CissDbError:
                contagem["erro_consulta"] += 1
                continue  # fica como estava (NULL = desconhecido, bloqueia remoção)
            contagem["consultadas"] += 1
        if not cargas:
            contagem["sem_carga"] += 1
        with conn:
            conn.execute(
                "UPDATE cargas_fusion_entrega_track SET cargas_ciss = ?, cargas_ciss_em = ? "
                "WHERE t10_id = ? AND pedido_erp = ?",
                (json.dumps(sorted(cargas)), momento, t10_id, pedido),
            )
    contagem["restantes"] = max(0, len(candidatas) - limite)
    return contagem


def vinculos_desejados(conn: sqlite3.Connection, t10_id: int, payload: dict) -> tuple[set[tuple[int, int]], bool]:
    """`(cargas, completo)`. `completo=False` quando alguma entrega do
    romaneio ainda não tem resposta do CISS — aí não se remove nada."""
    cargas = cargas_informadas_pelo_fusion(payload)
    pedidos = _pedidos_do_payload(payload)
    completo = True
    vistos = set()
    for linha in conn.execute(
        "SELECT pedido_erp, cargas_ciss FROM cargas_fusion_entrega_track WHERE t10_id = ?", (t10_id,)
    ):
        if linha["pedido_erp"] not in pedidos:
            continue  # entrega que saiu do romaneio: não conta mais
        vistos.add(linha["pedido_erp"])
        if linha["cargas_ciss"] is None:
            completo = False
            continue
        cargas |= {(int(e), int(p)) for e, p in json.loads(linha["cargas_ciss"])}
    if vistos != set(pedidos):
        completo = False
    return cargas, completo


def processar(
    conn: sqlite3.Connection, ciss, escrita, alertas: list[str], *,
    agora: datetime | None = None,
    limite_consultas: int = LIMITE_CONSULTAS_POR_CICLO,
) -> dict:
    from . import rastreio

    agora = agora or datetime.now()
    payloads = rastreio_entregas._ultimos_payloads_por_romaneio(conn)
    rastreio_entregas._sincronizar_lista(conn, payloads)
    ativos = {t10: dado for t10, dado in payloads.items() if not rastreio_entregas.romaneio_expirado(dado, agora)}

    existentes = rastreio.ler_existentes(
        ciss, TABELA, ciss_escrita.COLUNAS_CARGAS_CISS_FUSION_TRACK, ciss_escrita.CHAVE_CARGAS_CISS,
        "IDCARGAFUSION", list(ativos),
    )
    if existentes is None:
        # Sem saber o que já está lá não dá pra espelhar — e o caso comum é a
        # tabela ainda não ter sido criada (DDL em docs/ddl/).
        logger.warning("Vínculo carga CISS: leitura de %s falhou — etapa pulada neste ciclo", TABELA)
        return {"status": "indisponivel"}

    resultado: dict = {"status": "executado", **_resolver_pendentes(conn, ciss, ativos, agora, limite_consultas)}
    atuais: dict[int, set[tuple[int, int]]] = {}
    for empresa, carga, t10_id in existentes:
        atuais.setdefault(t10_id, set()).add((empresa, carga))

    vinculadas = removidas = erros = 0
    for t10_id, dado in ativos.items():
        desejado, completo = vinculos_desejados(conn, t10_id, dado["payload"])
        atual = atuais.get(t10_id, set())
        inserir = sorted(desejado - atual)
        remover = sorted(atual - desejado) if completo else []
        if not inserir and not remover:
            continue
        try:
            escrita.sincronizar_cargas_ciss(t10_id, inserir, remover)
        except CissDbError as exc:
            erros += 1
            logger.warning("Vínculo carga CISS do romaneio %s falhou: %s", t10_id, exc)
            continue
        vinculadas += len(inserir)
        removidas += len(remover)
    # Falha de gravação fica no resumo e no log, sem alerta: é retentada todo
    # ciclo, e um alerta (@everyone) a cada 2 min seria só barulho.
    resultado.update({"VINCULADAS": vinculadas, "REMOVIDAS": removidas, "ERRO": erros})
    return resultado
