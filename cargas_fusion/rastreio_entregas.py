"""Entregas do rastreio Fusion → CISS: `ENTREGAS[]` das pendências do
`getIntErp` → `DBA.ENTREGAS_FUSION_TRACK`.

De onde vem cada coisa (confirmado em produção, 2026-11-26):

- **A lista de entregas** vem do `ENTREGAS[]` que o `getIntErp` já
  devolveu (guardado cru em `cargas_fusion_int_erp`). `detalheCarga` não
  é usado: devolve as mesmas entregas com menos campos (sem datas).
- **`IDPLANILHA`/`NUMNOTA`/`IDEMPRESA` da NF** vêm sempre do CISS, nunca
  do `t32_nf` do Fusion — `t32_nf` é `"0"` em toda pré-carga (o pedido foi
  pro Fusion antes de faturar) e, no formato do integrador nativo, repete
  o orçamento em vez da NF. Como achar a NF depende do formato do código
  do pedido (`t32_pedido_original`) — ver `identificar_pedido`.
- **`STATUSENTREGA`** é o `status` inteiro do `detalheEntrega(pedido_erp)`,
  direto do Fusion, sem mapeamento (decisão do negócio). O mesmo
  `detalheEntrega` dá a observação e a previsão atualizada.

- **`IDENTREGAFUSION`** é a **sequência da entrega dentro do romaneio**
  (`t12_ordemEntrega` do payload mais recente do romaneio — muda quando o
  Fusion resequencia, `T10_ROMANEIO_ROTEIRIZADO`), não o `id` do
  `detalheEntrega` (decisão do negócio, 2026-11-26). Não é único: dois
  pedidos na mesma parada repetem a ordem (visto em produção, romaneios
  816733 e 831957), e um pedido com mais de uma NF repete em cada linha.

Fora do escopo por enquanto (decisão do negócio): `IDDEVOLUCAO`/
`DESCRDEVOLUCAO` (eventos de entrega ficam pra depois).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta

from integrations import ciss_escrita
from integrations.ciss import CissDbError
from integrations.fusion import FusionError

logger = logging.getLogger(__name__)

# Formatos de `t32_pedido_original`, um por origem de envio.
_NATIVO = re.compile(r"(\d+)@(\d+)@\d+@\d+")  # integrador nativo: pré-nota@empresa@x@y
_PRE_NOTA = re.compile(r"PN(\d+)_\S+")  # sem orçamento: PN{pré-nota}_{sufixo}
_PRE_CARGA = re.compile(r"(\d+)_PC(\d+)")  # {IDORCAMENTO}_PC{id_precarga}
_CARGA = re.compile(r"(\d+)_(\d+)(?:R\d+)?")  # {IDORCAMENTO}_{id_planilha}[R{reenvio}]


@dataclass(frozen=True)
class PedidoIdentificado:
    formato: str
    id_orcamento: int | None = None
    id_pre_nota: int | None = None
    id_empresa: int | None = None
    id_planilha_carga: int | None = None
    id_precarga: int | None = None


def identificar_pedido(pedido_original: str | None, filial: str | None) -> PedidoIdentificado | None:
    """`None` = formato desconhecido (ex. carga de teste `TESTE_CLAUDE_...`).

    | formato | exemplo | NF no CISS |
    |---|---|---|
    | `nativo` | `21956497@1@0@1` | pré-nota/empresa do código → retirada → NF |
    | `carga` | `613768_22105494`, `625240_22276368R1` | orçamento → pré-nota → retirada daquela carga → NF |
    | `pre_carga` | `616205_PC796273` | orçamento → pré-nota → retirada da carga formada pela pré-carga → NF |
    | `pre_nota` | `PN22171369_PC796450` | `NOTAS.IDPLANILHA = pré-nota`, empresa = filial da entrega |
    """
    codigo = (pedido_original or "").strip()
    if m := _NATIVO.fullmatch(codigo):
        return PedidoIdentificado("nativo", id_pre_nota=int(m[1]), id_empresa=int(m[2]))
    if m := _PRE_NOTA.fullmatch(codigo):
        empresa = int(filial) if filial and str(filial).strip().isdigit() else None
        if empresa is None:
            return None
        return PedidoIdentificado("pre_nota", id_pre_nota=int(m[1]), id_empresa=empresa)
    if m := _PRE_CARGA.fullmatch(codigo):
        return PedidoIdentificado("pre_carga", id_orcamento=int(m[1]), id_precarga=int(m[2]))
    if m := _CARGA.fullmatch(codigo):
        return PedidoIdentificado("carga", id_orcamento=int(m[1]), id_planilha_carga=int(m[2]))
    return None


def resolver_nfs(ciss, pedido: PedidoIdentificado) -> list[dict[str, object]]:
    """NFs reais (`IDEMPRESA`, `IDPLANILHA`, `NUMNOTA`) do pedido. Lista
    vazia = ainda sem NF no CISS (pré-carga não faturada/não consolidada)."""
    if pedido.formato == "nativo":
        return ciss.get_nfs_por_pre_nota_retirada(pedido.id_empresa, pedido.id_pre_nota)
    if pedido.formato == "pre_nota":
        return ciss.get_nfs_por_pre_nota_direta(pedido.id_empresa, pedido.id_pre_nota)
    if pedido.formato == "pre_carga":
        return ciss.get_nfs_por_orcamento(pedido.id_orcamento, id_precarga=pedido.id_precarga)
    return ciss.get_nfs_por_orcamento(pedido.id_orcamento, id_planilha_carga=pedido.id_planilha_carga)


# --- Ciclo de entregas ---------------------------------------------------

# Teto por ciclo — cada entrega é 1 detalheEntrega (~1s) + até 2 idas ao
# DB2; 100 cabe folgado no TTL do lock em produção (intervalo 120s × 3).
LIMITE_ENTREGAS_POR_CICLO = 100

# PROVISÓRIO (não confirmado com o negócio): "romaneio terminado" pra
# parar de reatualizar. getIntErp não reenvia o romaneio quando a viagem
# acaba, então sem as duas últimas regras um romaneio novo seria
# reatualizado pra sempre. `7` = único status de entrega concluída visto
# em produção até agora (detalheEntrega).
STATUS_ENTREGA_FINAL = frozenset({7})
DIAS_MAXIMOS_DE_ATUALIZACAO = 30

_SITUACOES_RETENTAVEIS = ("SEM_NF", "ERRO")


def _r():
    # Import tardio: rastreio importa este módulo, e este usa os
    # conversores de campo de lá.
    from . import rastreio

    return rastreio


def _ultimos_payloads_por_romaneio(conn: sqlite3.Connection) -> dict[int, dict]:
    """Payload mais novo (maior CODIGO_INT) já gravado no CISS de cada
    romaneio — só romaneio com linha em CARGAS_FUSION_TRACK pode ter
    entrega (FK_ENTREGASFUSION_CARGAFUSION)."""
    linhas = conn.execute(
        "SELECT e.t10_id, e.payload, e.recebido_em FROM cargas_fusion_int_erp e "
        "JOIN (SELECT t10_id, MAX(codigo_int) AS m FROM cargas_fusion_int_erp "
        "      WHERE status = 'INTERNALIZADO' AND t10_id IS NOT NULL GROUP BY t10_id) u "
        "  ON u.t10_id = e.t10_id AND u.m = e.codigo_int"
    ).fetchall()
    return {
        linha["t10_id"]: {"payload": json.loads(linha["payload"]), "recebido_em": linha["recebido_em"]}
        for linha in linhas
    }


def _sincronizar_lista(conn: sqlite3.Connection, payloads: dict[int, dict]) -> None:
    """Toda entrega do ENTREGAS[] vira uma linha local `PENDENTE` (se
    ainda não existir) — é a fila de trabalho do ciclo."""
    with conn:
        for t10_id, dado in payloads.items():
            for entrega in dado["payload"].get("ENTREGAS") or []:
                pedido = (entrega.get("t32_pedido_original") or "").strip()
                if pedido:
                    conn.execute(
                        "INSERT OR IGNORE INTO cargas_fusion_entrega_track (t10_id, pedido_erp) VALUES (?, ?)",
                        (t10_id, pedido),
                    )


def romaneio_expirado(dado: dict, agora: datetime) -> bool:
    """Passou do teto de dias desde a saída prevista (ou recebimento) —
    nem reatualiza, nem retenta NF que nunca apareceu."""
    r = _r()
    referencia = r._data(r._campo(dado["payload"], "t10_data_saida")) or r._data(dado["recebido_em"])
    return referencia is not None and agora - referencia > timedelta(days=DIAS_MAXIMOS_DE_ATUALIZACAO)


def romaneio_terminado(dado: dict, entregas: list[sqlite3.Row], agora: datetime) -> bool:
    """PROVISÓRIO — ver `STATUS_ENTREGA_FINAL`. Terminado = data de
    término/cancelamento no payload, ou toda entrega concluída, ou
    expirado."""
    payload = dado["payload"]
    r = _r()
    if r._data(r._campo(payload, "t10_data_termino")) or r._data(r._campo(payload, "t10_data_cancelamento")):
        return True
    if entregas and all(
        e["situacao"] in ("GRAVADA", "OUTRO_ROMANEIO", "IGNORADA")
        and (e["situacao"] != "GRAVADA" or e["status_entrega"] in STATUS_ENTREGA_FINAL)
        for e in entregas
    ):
        return True
    return romaneio_expirado(dado, agora)


def _entrega_do_payload(payload: dict, pedido: str) -> dict:
    return next(
        (e for e in payload.get("ENTREGAS") or [] if (e.get("t32_pedido_original") or "").strip() == pedido),
        {},
    )


def montar_linhas(t10_id: int, nfs: list[dict], entrega: dict, detalhe: dict) -> list[dict]:
    """Uma linha de DBA.ENTREGAS_FUSION_TRACK por NF. Previsão atualizada
    e observação preferem o detalheEntrega (mais fresco); previsão
    original e entrega real só existem no payload do getIntErp."""
    r = _r()
    status = r._inteiro(detalhe.get("status"))
    observacao = r._texto(detalhe.get("observacao")) or r._texto(entrega.get("t12_observacao_da_entrega"))
    prevista_atualizada = r._data(detalhe.get("data_prevista_entrega_atualizada")) or r._data(
        entrega.get("t12_data_prevista_entrega_atualizada")
    )
    return [
        {
            "IDEMPRESA": int(nf["IDEMPRESA"]),
            "IDPLANILHA": int(nf["IDPLANILHA"]),
            "IDCARGAFUSION": t10_id,
            "NUMNOTA": int(nf["NUMNOTA"]),
            "STATUSENTREGA": status,
            "DATAPREVISTAENTREGA": r._data(entrega.get("t12_data_prevista_entrega")),
            "DATAPREVISTAENTREGAATUALIZADA": prevista_atualizada,
            "DATAREALENTREGA": r._data(entrega.get("t12_data_real_entrega")),
            "OBERSERVACAOENTREGA": observacao,
            # Sequência da entrega no romaneio, não id do Fusion (ver topo).
            "IDENTREGAFUSION": r._inteiro(entrega.get("t12_ordemEntrega")),
            # Fora do escopo por enquanto (decisão do negócio, 2026-11-26).
            "IDDEVOLUCAO": None,
            "DESCRDEVOLUCAO": None,
        }
        for nf in nfs
    ]


def _atualizar(conn: sqlite3.Connection, t10_id: int, pedido: str, **campos) -> None:
    sets = ", ".join(f"{nome} = ?" for nome in campos)
    with conn:
        conn.execute(
            f"UPDATE cargas_fusion_entrega_track SET {sets}, atualizado_em = CURRENT_TIMESTAMP "
            "WHERE t10_id = ? AND pedido_erp = ?",
            (*campos.values(), t10_id, pedido),
        )


def processar_entrega(
    conn: sqlite3.Connection, ciss, fusion, escrita, t10_id: int, linha: sqlite3.Row, entrega: dict,
    existentes: dict[tuple, dict] | None = None,
) -> str:
    """`existentes`: linhas de `ENTREGAS_FUSION_TRACK` já no DB2 (ver
    `rastreio.ler_existentes`). Se toda NF do pedido já está igual, nada é
    gravado: `SEM_MUDANCA`, a linha local continua `GRAVADA`. É o caso
    comum da reatualização de romaneio em viagem, que antes regravava
    tudo a cada ciclo."""
    pedido = linha["pedido_erp"]
    tentativas = linha["tentativas"] + 1
    identificado = identificar_pedido(pedido, entrega.get("t43_codigo_filial_erp"))
    if identificado is None:
        _atualizar(conn, t10_id, pedido, situacao="IGNORADA", erro="Formato de pedido desconhecido")
        return "IGNORADA"

    nfs = json.loads(linha["nfs"]) if linha["nfs"] else []
    if not nfs:
        try:
            nfs = resolver_nfs(ciss, identificado)
        except CissDbError:
            _atualizar(conn, t10_id, pedido, formato=identificado.formato, situacao="ERRO",
                       erro="CISS indisponível resolvendo a NF", tentativas=tentativas)
            return "ERRO"
        if not nfs:
            _atualizar(conn, t10_id, pedido, formato=identificado.formato, situacao="SEM_NF",
                       erro="Ainda sem NF no CISS", tentativas=tentativas)
            return "SEM_NF"
        _atualizar(conn, t10_id, pedido, formato=identificado.formato, nfs=json.dumps(nfs))

    try:
        detalhe = fusion.detalhe_entrega(pedido)
    except FusionError:
        detalhe, erro = None, "Fusion indisponível no detalheEntrega"
    else:
        erro = None if detalhe is not None else "Pedido não encontrado no detalheEntrega"
    if erro is None and _r()._inteiro(detalhe.get("fusion_romaneio_id")) not in (None, t10_id):
        _atualizar(conn, t10_id, pedido, situacao="OUTRO_ROMANEIO",
                   erro=f"Entrega está no romaneio {detalhe.get('fusion_romaneio_id')}")
        return "OUTRO_ROMANEIO"
    if erro is None and _r()._inteiro(detalhe.get("status")) is None:
        erro = f"detalheEntrega sem status inteiro: {detalhe.get('status')!r}"
    if erro is not None:
        _atualizar(conn, t10_id, pedido, situacao="ERRO", erro=erro, tentativas=tentativas)
        return "ERRO"

    linhas_track = montar_linhas(t10_id, nfs, entrega, detalhe)
    gravadas = 0
    try:
        for registro in linhas_track:
            chave = ciss_escrita.chave_de(registro, ciss_escrita.CHAVE_ENTREGAS)
            existente = existentes.get(chave) if existentes is not None else None
            if ciss_escrita.linha_identica(registro, existente, ciss_escrita.COLUNAS_ENTREGAS_FUSION_TRACK):
                continue
            escrita.upsert_entrega_fusion_track(registro)
            gravadas += 1
            if existentes is not None:
                existentes[chave] = registro
    except CissDbError as exc:
        _atualizar(conn, t10_id, pedido, situacao="ERRO", tentativas=tentativas,
                   erro=f"Falha gravando no CISS: {exc}" if str(exc) else "Falha gravando no CISS")
        return "ERRO"

    # `gravada_em` = última escrita de verdade no DB2; `atualizado_em` =
    # última conferência (é o que ordena a fila de reatualização).
    with conn:
        conn.execute(
            "UPDATE cargas_fusion_entrega_track SET situacao = 'GRAVADA', erro = NULL, status_entrega = ?, "
            "tentativas = 0, atualizado_em = CURRENT_TIMESTAMP"
            + (", gravada_em = CURRENT_TIMESTAMP" if gravadas or linha["gravada_em"] is None else "")
            + " WHERE t10_id = ? AND pedido_erp = ?",
            (linhas_track[0]["STATUSENTREGA"], t10_id, pedido),
        )
    return "GRAVADA" if gravadas else "SEM_MUDANCA"


def processar_entregas(
    conn: sqlite3.Connection, ciss, fusion, escrita, alertas: list[str], avisos: list[str] | None = None, *,
    agora: datetime | None = None,
    limite: int = LIMITE_ENTREGAS_POR_CICLO,
) -> dict[str, int]:
    """Uma rodada, até `limite` entregas, nesta prioridade: (0) nunca
    processadas; (1) reatualização de romaneio não terminado; (2)
    retentativa de SEM_NF/ERRO de romaneio não expirado — por último pra
    um acúmulo de pré-carga ainda não faturada nunca matar de fome a
    atualização de quem está em viagem. Dentro de cada grupo, a menos
    recentemente tocada primeiro."""
    agora = agora or datetime.now()
    avisos = avisos if avisos is not None else []
    payloads = _ultimos_payloads_por_romaneio(conn)
    _sincronizar_lista(conn, payloads)

    fila: list[tuple[int, str, sqlite3.Row]] = []
    for t10_id, dado in payloads.items():
        entregas = conn.execute(
            "SELECT * FROM cargas_fusion_entrega_track WHERE t10_id = ?", (t10_id,)
        ).fetchall()
        terminado = romaneio_terminado(dado, entregas, agora)
        expirado = romaneio_expirado(dado, agora)
        for linha in entregas:
            if linha["situacao"] == "PENDENTE":
                fila.append((0, linha["atualizado_em"], linha))
            elif linha["situacao"] == "GRAVADA" and not terminado:
                fila.append((1, linha["atualizado_em"], linha))
            elif linha["situacao"] in _SITUACOES_RETENTAVEIS and not expirado:
                fila.append((2, linha["atualizado_em"], linha))
    fila.sort(key=lambda item: (item[0], item[1]))

    contagem: dict[str, int] = {}
    lote = fila[:limite]
    # Uma leitura só pro lote inteiro (por romaneio, não por linha).
    existentes = _r().ler_existentes(
        ciss,
        "DBA.ENTREGAS_FUSION_TRACK",
        ciss_escrita.COLUNAS_ENTREGAS_FUSION_TRACK,
        ciss_escrita.CHAVE_ENTREGAS,
        "IDCARGAFUSION",
        [linha["t10_id"] for _, _, linha in lote],
    )
    for _, _, linha in lote:
        t10_id = linha["t10_id"]
        entrega = _entrega_do_payload(payloads[t10_id]["payload"], linha["pedido_erp"])
        resultado = processar_entrega(conn, ciss, fusion, escrita, t10_id, linha, entrega, existentes)
        contagem[resultado] = contagem.get(resultado, 0) + 1
        # IGNORADA/OUTRO_ROMANEIO são terminais (nunca reprocessados) —
        # aviso sai uma vez só, sem precisar de controle extra.
        if resultado in ("IGNORADA", "OUTRO_ROMANEIO"):
            motivo = conn.execute(
                "SELECT erro FROM cargas_fusion_entrega_track WHERE t10_id = ? AND pedido_erp = ?",
                (t10_id, linha["pedido_erp"]),
            ).fetchone()["erro"]
            avisos.append(f"entrega {linha['pedido_erp']} (romaneio {t10_id}): {motivo}")
        if resultado == "ERRO" and linha["tentativas"] == 0:
            erro = conn.execute(
                "SELECT erro FROM cargas_fusion_entrega_track WHERE t10_id = ? AND pedido_erp = ?",
                (t10_id, linha["pedido_erp"]),
            ).fetchone()["erro"]
            alertas.append(f"entrega {linha['pedido_erp']} (romaneio {t10_id}): {erro}")
    contagem["restantes"] = max(0, len(fila) - limite)
    return contagem


def resumo(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        linha["situacao"]: linha["n"]
        for linha in conn.execute(
            "SELECT situacao, COUNT(*) AS n FROM cargas_fusion_entrega_track GROUP BY situacao"
        )
    }


def listar_do_romaneio(conn: sqlite3.Connection, t10_id: int) -> list[dict]:
    return [
        {
            "pedido_erp": linha["pedido_erp"],
            "formato": linha["formato"],
            "nfs": json.loads(linha["nfs"]) if linha["nfs"] else [],
            "status_entrega": linha["status_entrega"],
            "situacao": linha["situacao"],
            "erro": linha["erro"],
            "tentativas": linha["tentativas"],
            "gravada_em": _r().service._sqlite_utc_iso(linha["gravada_em"]),
            "atualizado_em": _r().service._sqlite_utc_iso(linha["atualizado_em"]),
        }
        for linha in conn.execute(
            "SELECT * FROM cargas_fusion_entrega_track WHERE t10_id = ? ORDER BY pedido_erp", (t10_id,)
        )
    ]
