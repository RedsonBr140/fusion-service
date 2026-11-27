"""Eventos do app do motorista: `obtemEventosPorID` →
`DBA.EVENTOS_FUSION_TRACK` (+ `DBA.ITENS_DEVOLVIDOS_FUSION_TRACK`).

Etapa final do ciclo do rastreio (`rastreio.executar_ciclo`), depois de
romaneios e entregas. Confirmado em produção (2026-11-26):

- **`obtemEventosPorID` é destrutivo**: cursor do lado do Fusion, a mesma
  chamada devolve os 50 seguintes; `idInicial` só é piso, nunca
  rebobina, e `eventosLidosOuNao('N')` não traz de volta. Por isso a
  mesma ordem do `getIntErp`: grava cru em `cargas_fusion_evento` antes
  de qualquer outra coisa. Não existe confirmação (`setIntErp`) aqui.
- **Escopo** (decisão do negócio): só evento de romaneio que já está em
  `CARGAS_FUSION_TRACK` — e o DB2 exige isso mesmo
  (`FK_EVENTOSFUSION_CARGAFUSION`). A fila do Fusion trazia eventos desde
  abril (quando o integrador nativo parou de ler); esses e qualquer outro
  romaneio fora do nosso fluxo ficam só crus, `FORA_DO_ESCOPO`. Evento
  recente de romaneio ainda desconhecido espera (`AGUARDANDO_ROMANEIO`)
  até `HORAS_ESPERA_ROMANEIO` — o `getIntErp` do romaneio pode chegar
  depois.
- **`IDENTREGAFUSION`** = sequência da entrega no romaneio (mesma regra
  de `ENTREGAS_FUSION_TRACK`): `t32_seq_pedido_erp` do evento →
  `t12_ordemEntrega` no payload mais recente do romaneio. Nulo em evento
  sem entrega (início/fim de romaneio, jornada). O `t15_entrega_id` do
  evento é o id interno do Fusion, não a sequência — não é usado.
- **Itens devolvidos**: `obtemEventosItensDevolvidos(idEvento)` (leitura
  pura, fora da doc) pra todo evento com motivo de devolução/reentrega.
  Item com `qtd_devolvido` nulo/zero não foi devolvido (devolução parcial
  lista o pedido inteiro) e não entra. `IDSUBPRODUTO` = último segmento
  de `codigo_erp_produto` (nós mandamos `IDSUBPRODUTO`; o integrador
  nativo mandava `IDPRODUTO@IDSUBPRODUTO`). Evento e itens vão numa
  transação só.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from integrations.ciss import CissDbError
from integrations.fusion import FusionError

logger = logging.getLogger(__name__)

_LIMITE_POR_CHAMADA = 50
# 40 × 50 = até 2000 eventos consumidos por ciclo — o backlog fora do
# escopo custa só sqlite, então drenar rápido é barato.
MAX_CHAMADAS_POR_CICLO = 40
# Escritas no DB2 por ciclo (cabe no TTL do lock); o resto fica pro próximo.
LIMITE_GRAVACOES_POR_CICLO = 200
HORAS_ESPERA_ROMANEIO = 48
_BRASILIA = timezone(timedelta(hours=-3))  # sem DST desde 2019

_SITUACOES_A_PROCESSAR = ("RECEBIDO", "AGUARDANDO_ROMANEIO", "ERRO")


def _r():
    from . import rastreio

    return rastreio


def _maior_id(conn: sqlite3.Connection) -> int:
    return conn.execute("SELECT COALESCE(MAX(t15_id), 0) AS m FROM cargas_fusion_evento").fetchone()["m"]


def registrar_recebidos(conn: sqlite3.Connection, eventos: list[dict]) -> int:
    """Cru, numa transação, antes de qualquer processamento. `t15_id` já
    visto não é sobrescrito (evento não muda depois de registrado)."""
    r = _r()
    gravados = 0
    with conn:
        for evento in eventos:
            t15_id = r._inteiro(evento.get("t15_id"))
            if t15_id is None:
                logger.warning("Evento sem t15_id descartado: %r", evento)
                continue
            cursor = conn.execute(
                "INSERT OR IGNORE INTO cargas_fusion_evento (t15_id, t10_id, tipo, payload) VALUES (?, ?, ?, ?)",
                (
                    t15_id,
                    r._inteiro(evento.get("t15_romaneio_id")),
                    r._inteiro(evento.get("t15_tipo")),
                    json.dumps(evento, ensure_ascii=False),
                ),
            )
            gravados += cursor.rowcount
    return gravados


def _ordem_da_entrega(payload_romaneio: dict, pedido: str | None) -> int | None:
    pedido = (pedido or "").strip()
    if not pedido:
        return None
    for entrega in payload_romaneio.get("ENTREGAS") or []:
        if (entrega.get("t32_pedido_original") or "").strip() == pedido:
            return _r()._inteiro(entrega.get("t12_ordemEntrega"))
    return None


def _ordens_historicas(conn: sqlite3.Connection, t10_id: int) -> dict[str, int]:
    """`pedido → t12_ordemEntrega` de **todos** os payloads já gravados do
    romaneio, o mais novo vencendo. Pra evento de entrega que saiu do
    romaneio depois (adiada, remanejada — visto em produção: 111 eventos
    no primeiro dreno), que o payload mais recente já não lista."""
    ordens: dict[str, int] = {}
    for (payload,) in conn.execute(
        "SELECT payload FROM cargas_fusion_int_erp WHERE t10_id = ? AND status IN ('INTERNALIZADO', 'SUPERADO') "
        "ORDER BY codigo_int",
        (t10_id,),
    ):
        for entrega in json.loads(payload).get("ENTREGAS") or []:
            pedido = (entrega.get("t32_pedido_original") or "").strip()
            ordem = _r()._inteiro(entrega.get("t12_ordemEntrega"))
            if pedido and ordem is not None:
                ordens[pedido] = ordem
    return ordens


def _motivo(evento: dict) -> int | None:
    motivo = _r()._inteiro(evento.get("t15_motivo_devol_reent_id"))
    return motivo or None  # "0" = sem motivo


def mapear_evento(evento: dict, payload_romaneio: dict, ordens_historicas: dict[str, int] | None = None) -> dict:
    """Sequência da entrega: payload mais recente do romaneio; se o pedido
    não está mais nele, a última sequência que ele teve (`_ordens_historicas`)."""
    r = _r()
    pedido = (evento.get("t32_seq_pedido_erp") or "").strip()
    ordem = _ordem_da_entrega(payload_romaneio, pedido)
    if ordem is None and pedido and ordens_historicas:
        ordem = ordens_historicas.get(pedido)
    return {
        "IDEVENTOFUSION": r._inteiro(evento.get("t15_id")),
        "IDCARGAFUSION": r._inteiro(evento.get("t15_romaneio_id")),
        "IDENTREGAFUSION": ordem,
        "DESCRICAO": r._texto(evento.get("t15_descricao"), 45),
        "KMATUAL": r._decimal(evento.get("t15_kmAtual"), 7),
        "LATITUDE": r._decimal(evento.get("t15_latitude"), 7),
        "LONGITUDE": r._decimal(evento.get("t15_longitude"), 7),
        "DATAHORA": r._data(evento.get("t15_dataHora")),
        "OBSMOTORISTA": r._texto(evento.get("t15_obsMotorista"), 2000),
        "DATAHORAREGISTRO": r._data(evento.get("t15_data_registro")),
        "IDMOTIVODEVOLUCAO": _motivo(evento),
        "DESCRDEVOLUCAO": r._texto(evento.get("descricao_motivo_devol_reent"), 45),
        "STATUS": None,  # coluna nova, fonte ainda não decidida pelo negócio
        "LINKFOTO": r._texto(evento.get("t15_link_foto"), 500),
    }


def _subproduto(codigo: object) -> int | None:
    texto = str(codigo or "").strip()
    return _r()._inteiro(texto.rsplit("@", 1)[-1]) if texto else None


def mapear_itens(linha_evento: dict, itens: list[dict]) -> tuple[list[dict], list[str]]:
    """Só item de fato devolvido, agregado por `IDSUBPRODUTO` (a PK não
    aceita o mesmo produto duas vezes no evento). Devolve (linhas, problemas)."""
    r = _r()
    problemas: list[str] = []
    por_sub: dict[int, dict] = {}
    for item in itens:
        devolvido = r._decimal(item.get("qtd_devolvido"), 7)
        if not devolvido:
            continue
        sub = _subproduto(item.get("codigo_erp_produto"))
        if sub is None:
            problemas.append(f"produto devolvido sem IDSUBPRODUTO reconhecível: {item.get('codigo_erp_produto')!r}")
            continue
        original = r._decimal(item.get("qtd_original"), 7)
        atual = por_sub.get(sub)
        if atual is None:
            por_sub[sub] = {
                "IDEVENTOFUSION": linha_evento["IDEVENTOFUSION"],
                "IDENTREGAFUSION": linha_evento["IDENTREGAFUSION"],
                "IDSUBPRODUTO": sub,
                "QTDORIGINAL": original,
                "QTDDEVOLVIDO": devolvido,
                "IDMOTIVO": r._inteiro(item.get("id_motivo_devolucao")) or linha_evento["IDMOTIVODEVOLUCAO"],
                "MOTIVODEVOLUCAO": r._texto(item.get("motivo_devolucao"), 200) or linha_evento["DESCRDEVOLUCAO"],
            }
        else:
            atual["QTDDEVOLVIDO"] += devolvido
            if original is not None:
                atual["QTDORIGINAL"] = (atual["QTDORIGINAL"] or Decimal(0)) + original
    linhas = list(por_sub.values())
    if linhas and linha_evento["IDENTREGAFUSION"] is None:
        # PK exige IDENTREGAFUSION: sem a sequência da entrega, os itens não entram.
        problemas.append(f"{len(linhas)} item(ns) devolvido(s) sem sequência da entrega — não gravados")
        linhas = []
    return linhas, problemas


def _atualizar(conn: sqlite3.Connection, t15_id: int, **campos) -> None:
    sets = ", ".join(f"{nome} = ?" for nome in campos)
    with conn:
        conn.execute(
            f"UPDATE cargas_fusion_evento SET {sets}, atualizado_em = CURRENT_TIMESTAMP WHERE t15_id = ?",
            (*campos.values(), t15_id),
        )


def _processar_evento(conn, fusion, escrita, linha, payloads, agora, avisos) -> str:
    evento = json.loads(linha["payload"])
    dado = payloads.get(linha["t10_id"]) if linha["t10_id"] is not None else None
    if dado is None:
        quando = _r()._data(evento.get("t15_dataHora"))
        if linha["t10_id"] is not None and quando and agora - quando < timedelta(hours=HORAS_ESPERA_ROMANEIO):
            _atualizar(conn, linha["t15_id"], status="AGUARDANDO_ROMANEIO")
            return "AGUARDANDO_ROMANEIO"
        _atualizar(conn, linha["t15_id"], status="FORA_DO_ESCOPO")
        return "FORA_DO_ESCOPO"

    linha_evento = mapear_evento(evento, dado["payload"])
    if linha_evento["IDENTREGAFUSION"] is None and (evento.get("t32_seq_pedido_erp") or "").strip():
        linha_evento = mapear_evento(evento, dado["payload"], _ordens_historicas(conn, linha["t10_id"]))
    itens_brutos = json.loads(linha["itens_devolvidos"]) if linha["itens_devolvidos"] else None
    try:
        if _motivo(evento) and itens_brutos is None:
            itens_brutos = fusion.obtem_itens_devolvidos(str(linha["t15_id"]))
            _atualizar(conn, linha["t15_id"], itens_devolvidos=json.dumps(itens_brutos, ensure_ascii=False))
        itens, problemas = mapear_itens(linha_evento, itens_brutos or [])
        escrita.gravar_evento_fusion_track(linha_evento, itens)
    except (FusionError, CissDbError) as exc:
        _atualizar(
            conn, linha["t15_id"], status="ERRO", erro=f"{type(exc).__name__}: {exc}"[:500],
            tentativas=linha["tentativas"] + 1,
        )
        return "ERRO"
    for problema in problemas:
        avisos.append(f"evento {linha['t15_id']} (romaneio {linha['t10_id']}): {problema}")
    _atualizar(
        conn, linha["t15_id"], status="GRAVADO", erro="; ".join(problemas) or None,
        itens_gravados=len(itens), gravado_em=agora.strftime("%Y-%m-%d %H:%M:%S"),
    )
    return "GRAVADO"


def processar_eventos(conn, fusion, escrita, alertas, avisos, *, agora: datetime | None = None) -> dict:
    """Drena a fila do Fusion (grava cru) e depois grava no CISS o que é
    do escopo, em ordem de `t15_id`."""
    # Datas do Fusion são hora de Brasília sem fuso; o pod roda em UTC.
    agora = agora or datetime.now(_BRASILIA).replace(tzinfo=None)
    contagem: dict[str, int] = {}
    recebidos = 0
    for _ in range(MAX_CHAMADAS_POR_CICLO):
        try:
            eventos = fusion.obtem_eventos(_maior_id(conn))
        except FusionError as exc:
            alertas.append(f"Fusion indisponível no obtemEventosPorID ({type(exc).__name__})")
            break
        if not eventos:
            break
        recebidos += registrar_recebidos(conn, eventos)
        if len(eventos) < _LIMITE_POR_CHAMADA:
            break
    contagem["recebidos"] = recebidos

    payloads = _r().rastreio_entregas._ultimos_payloads_por_romaneio(conn)
    # Escopo = romaneio em CARGAS_FUSION_TRACK *agora*: um romaneio que
    # entrou depois (ex. resequenciado no Fusion) traz de volta os eventos
    # que já tinham ficado FORA_DO_ESCOPO.
    if payloads:
        with conn:
            ids = list(payloads)
            for i in range(0, len(ids), 500):
                lote = ids[i : i + 500]
                conn.execute(
                    f"UPDATE cargas_fusion_evento SET status = 'RECEBIDO', atualizado_em = CURRENT_TIMESTAMP "
                    f"WHERE status = 'FORA_DO_ESCOPO' AND t10_id IN ({','.join('?' * len(lote))})",
                    lote,
                )
    marcas = ",".join("?" * len(_SITUACOES_A_PROCESSAR))
    fila = conn.execute(
        f"SELECT * FROM cargas_fusion_evento WHERE status IN ({marcas}) ORDER BY t15_id",
        _SITUACOES_A_PROCESSAR,
    ).fetchall()
    gravacoes = 0
    for linha in fila:
        dentro = linha["t10_id"] in payloads
        if dentro and gravacoes >= LIMITE_GRAVACOES_POR_CICLO:
            contagem["restantes"] = contagem.get("restantes", 0) + 1
            continue
        resultado = _processar_evento(conn, fusion, escrita, linha, payloads, agora, avisos)
        gravacoes += dentro
        contagem[resultado] = contagem.get(resultado, 0) + 1
        if resultado == "ERRO" and linha["tentativas"] == 0:
            erro = conn.execute("SELECT erro FROM cargas_fusion_evento WHERE t15_id = ?", (linha["t15_id"],)).fetchone()
            alertas.append(f"evento {linha['t15_id']} (romaneio {linha['t10_id']}): {erro['erro']}")
    return contagem


def resumo(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        linha["status"]: linha["n"]
        for linha in conn.execute("SELECT status, COUNT(*) AS n FROM cargas_fusion_evento GROUP BY status")
    }


def listar_do_romaneio(conn: sqlite3.Connection, t10_id: int) -> list[dict]:
    saida = []
    for linha in conn.execute("SELECT * FROM cargas_fusion_evento WHERE t10_id = ? ORDER BY t15_id", (t10_id,)):
        evento = json.loads(linha["payload"])
        saida.append(
            {
                "t15_id": linha["t15_id"],
                "tipo": linha["tipo"],
                "descricao": evento.get("t15_descricao"),
                "data_hora": _r()._serializavel(_r()._data(evento.get("t15_dataHora"))),
                "pedido_erp": evento.get("t32_seq_pedido_erp") or None,
                "motivo": evento.get("descricao_motivo_devol_reent") or None,
                "status": linha["status"],
                "erro": linha["erro"],
                "itens_gravados": linha["itens_gravados"],
            }
        )
    return saida
