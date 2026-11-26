"""Rastreio Fusion → CISS: consome a fila `getIntErp` e popula
`DBA.CARGAS_FUSION_TRACK`, depois confirma cada pendência com `setIntErp`.

`getIntErp` é **destrutivo**: toda pendência devolvida sai da fila do
Fusion na hora. Por isso a ordem aqui é fixa e não se negocia:

1. tudo o que o `getIntErp` devolve vai cru pra `cargas_fusion_int_erp`
   (sqlite) antes de qualquer outra coisa — se o DB2 estiver fora, nada
   se perde, só fica `ERRO` e é reprocessado no próximo ciclo;
2. só então o upsert no CISS (`integrations/ciss_escrita.py`);
3. só depois de gravado no CISS, `setIntErp` (confirmação de "recebido e
   internalizado"). Falha no `setIntErp` não desfaz o upsert — fica
   `confirmado_em` nulo e é retentado no próximo ciclo.

Só `T10_ROMANEIO_MONTAGEM_CARGA` e `T10_ROMANEIO_ROTEIRIZADO` são
internalizados (os dois tipos que a doc do getIntErp diz que o ERP
precisa processar); o resto fica `IGNORADO`, guardado cru, sem
`setIntErp`.

`STATUSROMANEIO`: o Fusion manda `t10_situacao` como texto e nenhum lado
define o inteiro (nem a doc do Fusion, nem o catálogo do CISS) — a
tabela `STATUS_ROMANEIO` é nossa, decisão do negócio (2026-11-26).
Situação fora dela vai `NULL` com aviso no Discord.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime
from decimal import Decimal, InvalidOperation

from integrations import discord
from integrations.ciss import CissDbError
from integrations.ciss_escrita import CissEscritaClient
from integrations.fusion import FusionClient, FusionError

from . import rastreio_entregas, rastreio_eventos, service

logger = logging.getLogger(__name__)

NOME_LOCK = "fusion-service:rastreio-int-erp"

TIPOS_INTERNALIZADOS = frozenset({"T10_ROMANEIO_MONTAGEM_CARGA", "T10_ROMANEIO_ROTEIRIZADO"})

_LIMITE_POR_CHAMADA = 50
# Teto de chamadas ao getIntErp por ciclo (50 × 20 = 1000 pendências) —
# finito pra um ciclo nunca virar um loop sem fim se o Fusion insistir em
# devolver o mesmo lote; o que sobrar vem no próximo ciclo.
_MAX_CHAMADAS_POR_CICLO = 20

_DATAS_NULAS = ("0000-00-00", "0000-00-00 00:00:00")


# --- Conversão de campo --------------------------------------------------


def _campo(item: dict, *chaves: str) -> object:
    """Primeiro valor não vazio entre `chaves`, sem diferenciar maiúscula
    — a doc do Fusion alterna `t10_kmTotal`/`t10_kmtotal` entre métodos."""
    por_nome = {str(k).lower(): v for k, v in item.items()}
    for chave in chaves:
        valor = por_nome.get(chave.lower())
        if valor is not None and not (isinstance(valor, str) and not valor.strip()):
            return valor
    return None


def _inteiro(valor: object) -> int | None:
    if valor is None:
        return None
    try:
        numero = Decimal(str(valor).strip())
    except InvalidOperation:
        return None
    if numero != numero.to_integral_value():
        return None
    return int(numero)


def _decimal(valor: object, casas: int) -> Decimal | None:
    if valor is None:
        return None
    try:
        numero = Decimal(str(valor).strip())
    except InvalidOperation:
        return None
    if not numero.is_finite():
        return None
    return numero.quantize(Decimal(1).scaleb(-casas))


def _data(valor: object) -> datetime | None:
    """Datas do Fusion chegam em hora de Brasília, sem fuso — mesmo fuso
    em que o CISS grava TIMESTAMP, então vão ingênuas pro DB2."""
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto or texto in _DATAS_NULAS or texto.startswith("0000-00-00"):
        return None
    # `dd/mm/aaaa hh:mm`: formato do detalheEntrega (`data_prevista_entrega_atualizada`).
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(texto).replace(tzinfo=None)
    except ValueError:
        return None


def _texto(valor: object, tamanho: int | None = None) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    if not texto:
        return None
    return texto[:tamanho] if tamanho else texto


# Ordem da doc do `listaCargas` ('Ativo', 'Em Curso', 'Finalizado',
# 'Unido', 'Cancelado'); "Bloqueado" aparece em produção mas não na doc.
# Decisão do negócio (2026-11-26) — nenhum dos dois sistemas define isso.
STATUS_ROMANEIO = {
    "ATIVO": 1,
    "EM CURSO": 2,
    "FINALIZADO": 3,
    "UNIDO": 4,
    "CANCELADO": 5,
    "BLOQUEADO": 6,
}


def status_romaneio(situacao: object) -> int | None:
    texto = _texto(situacao)
    return STATUS_ROMANEIO.get(texto.upper()) if texto else None


def mapear_carga_track(item: dict) -> tuple[dict | None, str | None]:
    """Pendência do getIntErp → linha de `DBA.CARGAS_FUSION_TRACK`.
    Devolve `(linha, None)` ou `(None, motivo)` quando falta algo que a
    tabela exige (`IDCARGAFUSION`/`IDEMPRESA`/`IDMOTORISTA` são NOT NULL).

    `t05_codigo_erp`/`t06_codigo_erp` são os códigos do CISS que esta
    própria integração manda como `codigo_erp` (IDCLIFOR do motorista,
    id do veículo) — confirmado com o negócio."""
    id_carga = _inteiro(_campo(item, "t10_id"))
    id_empresa = _inteiro(_campo(item, "t43_codigo_filial_erp"))
    id_motorista = _inteiro(_campo(item, "t05_codigo_erp"))

    faltando = []
    if id_carga is None:
        faltando.append("t10_id")
    if id_empresa is None:
        faltando.append(f"t43_codigo_filial_erp ({_campo(item, 't43_codigo_filial_erp')!r})")
    if id_motorista is None:
        faltando.append(f"t05_codigo_erp ({_campo(item, 't05_codigo_erp')!r})")
    if faltando:
        return None, "Campo obrigatório ausente ou não numérico: " + ", ".join(faltando)

    entregas = item.get("ENTREGAS")
    linha = {
        "IDCARGAFUSION": id_carga,
        "IDEMPRESA": id_empresa,
        "DESTINO": _texto(_campo(item, "t10_destino"), 45),
        "STATUSROMANEIO": status_romaneio(_campo(item, "t10_situacao")),
        "OBSERVACAO": _texto(_campo(item, "t10_obs")),
        "DATASAIDAPREVISTA": _data(_campo(item, "t10_data_saida")),
        "IDMOTORISTA": id_motorista,
        "PESO": _decimal(_campo(item, "peso"), 3),
        "CUSTO": _decimal(_campo(item, "custo"), 3),
        "CUSTOESTIMADO": _decimal(_campo(item, "t10_custo_estimado"), 3),
        "VOLUME": _decimal(_campo(item, "volume"), 3),
        "VALORTOTAL": _decimal(_campo(item, "valortotal"), 3),
        "NUMEROENTREGAS": len(entregas) if isinstance(entregas, list) else None,
        "NUMEROCLIENTES": _inteiro(_campo(item, "numclientes")),
        "NUMEROCIDADES": _inteiro(_campo(item, "numcidades")),
        "CODIGOPRACAPRINCIPAL": _texto(_campo(item, "codpracaprinc", "t10_codpracaprinc"), 45),
        "IDVEICULO": _inteiro(_campo(item, "t06_codigo_erp")),
        "CUSTOPEDAGIO": _decimal(_campo(item, "t10_custo_pedagio"), 3),
        "DATACANCELAMENTO": _data(_campo(item, "t10_data_cancelamento")),
        "DATAGRAVA": _data(_campo(item, "t10_data_grava")),
        "DATAINICIO": _data(_campo(item, "t10_data_inicio")),
        "DATAPREVISTATERMINO": _data(_campo(item, "t10_data_prevista_termino")),
        "DATATERMINO": _data(_campo(item, "t10_data_termino")),
        "KMREALIZADO": _decimal(_campo(item, "t10_kmRealizado", "t10_km_realizado", "km_realizado"), 7),
        "KMPREVISTO": _decimal(_campo(item, "t10_kmPrevisto", "t10_km_previsto", "km_previsto"), 7),
        "KMTOTAL": _decimal(_campo(item, "t10_kmTotal", "t10_km_total", "km_total"), 7),
    }
    return linha, None


# --- Caixa de entrada (sqlite) -------------------------------------------


def _registrar_recebidos(conn: sqlite3.Connection, itens: list[dict]) -> int:
    """Grava cru, numa transação, antes de qualquer processamento. Um
    `CODIGO_INT` já visto (reenvio manual pelo Fusion) volta pra
    `RECEBIDO` com o payload novo."""
    gravados = 0
    with conn:
        for item in itens:
            codigo_int = _inteiro(_campo(item, "CODIGO_INT"))
            payload = json.dumps(item, ensure_ascii=False)
            tipo = _texto(_campo(item, "TIPO_INT"))
            t10_id = _inteiro(_campo(item, "t10_id"))
            if codigo_int is None:
                # Sem chave não dá pra confirmar nem deduplicar — guarda
                # mesmo assim (é o único registro do que foi consumido).
                conn.execute(
                    "INSERT INTO cargas_fusion_int_erp (codigo_int, tipo_int, t10_id, payload, status, erro) "
                    "VALUES (NULL, ?, ?, ?, 'ERRO', 'Pendência sem CODIGO_INT')",
                    (tipo, t10_id, payload),
                )
            else:
                conn.execute(
                    "INSERT INTO cargas_fusion_int_erp (codigo_int, tipo_int, t10_id, payload) "
                    "VALUES (?, ?, ?, ?) "
                    "ON CONFLICT (codigo_int) DO UPDATE SET "
                    "tipo_int = excluded.tipo_int, t10_id = excluded.t10_id, "
                    "payload = excluded.payload, status = 'RECEBIDO', erro = NULL, "
                    "confirmado_em = NULL, confirmacao_erro = NULL, "
                    "recebido_em = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP",
                    (codigo_int, tipo, t10_id, payload),
                )
            gravados += 1
    return gravados


def _atualizar(conn: sqlite3.Connection, id_: int, **campos) -> None:
    sets = ", ".join(f"{nome} = ?" for nome in campos)
    with conn:
        conn.execute(
            f"UPDATE cargas_fusion_int_erp SET {sets}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (*campos.values(), id_),
        )


def _superado(conn: sqlite3.Connection, linha: sqlite3.Row) -> int | None:
    """Uma pendência mais nova do mesmo romaneio já foi gravada no CISS?
    Reprocessar a antiga (ex. `ERRO` retentado depois) sobrescreveria
    dado novo com velho."""
    if linha["t10_id"] is None or linha["codigo_int"] is None:
        return None
    mais_nova = conn.execute(
        "SELECT codigo_int FROM cargas_fusion_int_erp WHERE t10_id = ? AND codigo_int > ? "
        "AND status = 'INTERNALIZADO' ORDER BY codigo_int DESC LIMIT 1",
        (linha["t10_id"], linha["codigo_int"]),
    ).fetchone()
    return mais_nova["codigo_int"] if mais_nova else None


def _texto_ok(valor: object) -> bool:
    return isinstance(valor, str) and valor.strip().strip('"').upper().startswith("OK")


def _confirmacao_ok(resultado: object) -> bool:
    """`"OK"` (doc), ou um objeto por id — o formato que o Fusion
    devolveu de verdade (`{"<int_id>": "Erro: ..."}` na falha). Sucesso
    só quando todo valor do objeto é OK; qualquer `"Erro..."` é falha."""
    if _texto_ok(resultado):
        return True
    if isinstance(resultado, dict):
        if "success" in resultado:
            return resultado.get("success") not in (None, False, "", [], 0, "0") and not resultado.get("errors")
        return bool(resultado) and all(_texto_ok(v) for v in resultado.values())
    return False


def _carga_para_setinterp(linha: sqlite3.Row) -> str | None:
    """CONTORNO, NÃO O RESULTADO FINAL DESEJADO (decisão do Redson,
    2026-11-26): o Fusion só aceita o setIntErp como sucesso com `carga`
    preenchido (sem ele, grava a chamada como erro), mas este serviço não
    cria carga no CISS — não existe número de carga do ERP pra mandar.
    Até existir um número de verdade, manda o próprio `t10_id` (romaneio
    do Fusion). Consequência: o Fusion pode passar a mostrar o próprio
    romaneio como "carga formada no ERP" (`t10_carga_formada_erp`).
    Trocar isto quando houver uma carga real do CISS pra vincular."""
    return str(linha["t10_id"]) if linha["t10_id"] is not None else None


def _internalizar(
    conn: sqlite3.Connection,
    escrita: CissEscritaClient,
    linha: sqlite3.Row,
    alertas: list[str],
    avisos: list[str],
) -> str:
    tipo = linha["tipo_int"]
    if tipo not in TIPOS_INTERNALIZADOS:
        _atualizar(conn, linha["id"], status="IGNORADO", erro=None)
        # Uma vez só: IGNORADO nunca é reprocessado.
        avisos.append(
            f"{linha['codigo_int']} (romaneio {linha['t10_id']}): tipo `{tipo}` não é processado — "
            "guardado cru, sem gravar no CISS nem confirmar"
        )
        return "IGNORADO"

    mais_nova = _superado(conn, linha)
    if mais_nova is not None:
        _atualizar(conn, linha["id"], status="SUPERADO", erro=f"Superado pelo CODIGO_INT {mais_nova}")
        return "SUPERADO"

    tentativas = linha["tentativas"] + 1
    registro, motivo = mapear_carga_track(json.loads(linha["payload"]))
    if registro is not None:
        try:
            escrita.upsert_carga_fusion_track(registro)
        except CissDbError as exc:
            motivo = f"Falha gravando no CISS: {exc}" if str(exc) else "Falha gravando no CISS (DB2 indisponível)"

    if motivo is not None:
        _atualizar(conn, linha["id"], status="ERRO", erro=motivo, tentativas=tentativas)
        if tentativas == 1:
            alertas.append(
                f"{linha['codigo_int']} (romaneio {linha['t10_id']}, {tipo}) não gravada: {motivo}"
            )
        return "ERRO"

    situacao = _texto(_campo(json.loads(linha["payload"]), "t10_situacao"))
    if situacao and registro["STATUSROMANEIO"] is None and tentativas == 1:
        avisos.append(
            f"{linha['codigo_int']} (romaneio {linha['t10_id']}): situação `{situacao}` fora da tabela "
            "STATUS_ROMANEIO — gravado com STATUSROMANEIO nulo"
        )
    _atualizar(conn, linha["id"], status="INTERNALIZADO", erro=None, tentativas=tentativas)
    return "INTERNALIZADO"


def _confirmar(
    conn: sqlite3.Connection, fusion: FusionClient, linha: sqlite3.Row, alertas: list[str]
) -> bool:
    if linha["status"] == "SUPERADO":
        retorno = f"{linha['erro']} - romaneio {linha['t10_id']} ja internalizado"
    else:
        retorno = f"Internalizado em CARGAS_FUSION_TRACK (romaneio {linha['t10_id']})"
    try:
        resultado = fusion.set_int_erp(str(linha["codigo_int"]), retorno, carga=_carga_para_setinterp(linha))
    except FusionError:
        resultado = None
        erro = "Fusion indisponível no setIntErp"
    else:
        erro = None if _confirmacao_ok(resultado) else f"setIntErp respondeu: {resultado!r}"[:500]

    if erro is None:
        with conn:
            conn.execute(
                "UPDATE cargas_fusion_int_erp SET confirmado_em = CURRENT_TIMESTAMP, "
                "confirmacao_erro = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (linha["id"],),
            )
        return True

    primeira_falha = linha["confirmacao_erro"] is None
    _atualizar(conn, linha["id"], confirmacao_erro=erro)
    if primeira_falha:
        alertas.append(f"{linha['codigo_int']} gravada no CISS mas não confirmada (setIntErp): {erro}")
    return False


# Discord corta mensagem em 2000 caracteres; e uma mensagem por pendência
# estourou o rate limit (429) no primeiro ciclo real, com 290 pendências.
_MAX_ALERTAS_DETALHADOS = 10


def _notificar_alertas(discord_webhook_url: str | None, alertas: list[str]) -> None:
    """Canal de erros: um embed por ciclo, nunca um por pendência."""
    if not alertas:
        return
    linhas = [f"• {a[:180]}" for a in alertas[:_MAX_ALERTAS_DETALHADOS]]
    if len(alertas) > _MAX_ALERTAS_DETALHADOS:
        linhas.append(f"… e mais {len(alertas) - _MAX_ALERTAS_DETALHADOS} — ver aba Rastreio.")
    discord.erro(
        discord_webhook_url,
        f"Rastreio getIntErp — {len(alertas)} problema(s) novo(s)",
        "\n".join(linhas),
        campos=[("Onde ver", "Aba **Rastreio (getIntErp)** do fusion-service", False)],
    )


def _notificar_avisos(discord_webhook_url: str | None, avisos: list[str]) -> None:
    """Canal de erros, amarelo, sem menção — um embed por ciclo."""
    if not avisos:
        return
    linhas = [f"• {a[:180]}" for a in avisos[:_MAX_ALERTAS_DETALHADOS]]
    if len(avisos) > _MAX_ALERTAS_DETALHADOS:
        linhas.append(f"… e mais {len(avisos) - _MAX_ALERTAS_DETALHADOS} — ver aba Rastreio.")
    discord.aviso(discord_webhook_url, f"Rastreio getIntErp — {len(avisos)} aviso(s)", "\n".join(linhas))


def _resumir_ciclo(resultado: dict) -> None:
    """Canal de atividade: resumo do ciclo, só quando algo aconteceu."""
    processadas = resultado.get("processadas") or {}
    entregas = {k: v for k, v in (resultado.get("entregas") or {}).items() if k != "restantes" and v}
    eventos = {
        k: v for k, v in (resultado.get("eventos") or {}).items() if k not in ("recebidos", "restantes") and v
    }
    recebidos_eventos = (resultado.get("eventos") or {}).get("recebidos", 0)
    if not (resultado.get("recebidas") or processadas or resultado.get("confirmadas") or entregas or eventos
            or recebidos_eventos):
        return
    descricao = lambda d: " · ".join(f"{k.lower()}: **{v}**" for k, v in sorted(d.items())) or "—"  # noqa: E731
    discord.atividade(
        "🔁 Ciclo de rastreio (getIntErp + eventos)",
        cor=discord.COR_INFO,
        campos=[
            ("Recebidas do getIntErp", resultado.get("recebidas", 0), True),
            ("Confirmadas (setIntErp)", resultado.get("confirmadas", 0), True),
            ("Falhas de confirmação", resultado.get("falhas_confirmacao", 0), True),
            ("Romaneios", descricao(processadas), False),
            ("Entregas", descricao(entregas), False),
            ("Entregas pra próximos ciclos", (resultado.get("entregas") or {}).get("restantes", 0), True),
            ("Eventos consumidos (obtemEventos)", recebidos_eventos, True),
            ("Eventos", descricao(eventos), False),
            ("Eventos pra próximos ciclos", (resultado.get("eventos") or {}).get("restantes", 0), True),
            ("Duração", f"{resultado.get('duracao_ms', 0) / 1000:.1f}s", True),
        ],
    )


# --- Ciclo ---------------------------------------------------------------


def _adquirir_lock(redis_client, timeout_segundos: int):
    lock = redis_client.lock(NOME_LOCK, timeout=timeout_segundos)
    return lock if lock.acquire(blocking=False) else None


def _liberar_lock(lock) -> None:
    try:
        lock.release()
    except Exception:
        logger.warning("Lock do rastreio getIntErp já havia expirado ao tentar liberar.")


def executar_ciclo(
    conn: sqlite3.Connection,
    redis_client,
    fusion_client: FusionClient | None,
    escrita_client: CissEscritaClient | None,
    *,
    discord_webhook_url: str | None,
    lock_timeout_segundos: int,
    ciss_client=None,
) -> dict:
    """`ciss_client` (leitura) é só pra resolver a NF das entregas
    (`rastreio_entregas`) — sem ele, o ciclo grava/confirma romaneios
    normalmente e só pula a etapa de entregas."""
    if fusion_client is None or escrita_client is None:
        return {"status": "nao_configurado"}

    lock = _adquirir_lock(redis_client, lock_timeout_segundos)
    if lock is None:
        return {"status": "pulado", "motivo": "ciclo já em andamento em outro worker"}

    inicio = time.monotonic()
    recebidas = 0
    contagem: dict[str, int] = {}
    confirmadas = 0
    falhas_confirmacao = 0
    erro_coleta = None
    alertas: list[str] = []
    avisos: list[str] = []
    try:
        for _ in range(_MAX_CHAMADAS_POR_CICLO):
            try:
                itens = fusion_client.get_int_erp(_LIMITE_POR_CHAMADA)
            except FusionError:
                erro_coleta = "Fusion indisponível no getIntErp"
                break
            if not itens:
                break
            recebidas += _registrar_recebidos(conn, itens)
            if len(itens) < _LIMITE_POR_CHAMADA:
                break

        # Ordem crescente de CODIGO_INT = ordem da fila do Fusion: a
        # pendência mais nova de um romaneio sempre sobrescreve a velha.
        pendentes = conn.execute(
            "SELECT * FROM cargas_fusion_int_erp WHERE status IN ('RECEBIDO', 'ERRO') "
            "AND codigo_int IS NOT NULL ORDER BY codigo_int"
        ).fetchall()
        for linha in pendentes:
            resultado = _internalizar(conn, escrita_client, linha, alertas, avisos)
            contagem[resultado] = contagem.get(resultado, 0) + 1

        a_confirmar = conn.execute(
            "SELECT * FROM cargas_fusion_int_erp WHERE status IN ('INTERNALIZADO', 'SUPERADO') "
            "AND confirmado_em IS NULL ORDER BY codigo_int"
        ).fetchall()
        for linha in a_confirmar:
            if _confirmar(conn, fusion_client, linha, alertas):
                confirmadas += 1
            else:
                falhas_confirmacao += 1

        # Entregas por último: só romaneio já em CARGAS_FUSION_TRACK pode
        # ter linha em ENTREGAS_FUSION_TRACK (FK no DB2).
        entregas = None
        if ciss_client is not None:
            entregas = rastreio_entregas.processar_entregas(
                conn, ciss_client, fusion_client, escrita_client, alertas, avisos
            )

        # Eventos depois de tudo: mesma FK (romaneio em CARGAS_FUSION_TRACK),
        # e a sequência da entrega vem do payload do romaneio.
        eventos = rastreio_eventos.processar_eventos(conn, fusion_client, escrita_client, alertas, avisos)

        if erro_coleta:
            logger.warning("Rastreio getIntErp: %s", erro_coleta)
            alertas.insert(0, erro_coleta)
        _notificar_alertas(discord_webhook_url, alertas)
        _notificar_avisos(discord_webhook_url, avisos)
        resultado = {
            "status": "executado",
            "recebidas": recebidas,
            "processadas": contagem,
            "confirmadas": confirmadas,
            "falhas_confirmacao": falhas_confirmacao,
            "entregas": entregas,
            "eventos": eventos,
            "erro_coleta": erro_coleta,
            "duracao_ms": int((time.monotonic() - inicio) * 1000),
        }
        _resumir_ciclo(resultado)
        return resultado
    finally:
        _liberar_lock(lock)


def _serializavel(valor: object) -> object:
    """Decimal → string (sem perder casa decimal), datetime ingênuo
    (hora de Brasília) → ISO com `-03:00` explícito — mesma regra de fuso
    da API inteira (CLAUDE.md, "Fuso horário nas respostas da API")."""
    if isinstance(valor, Decimal):
        return str(valor)
    if isinstance(valor, datetime):
        return valor.isoformat() + "-03:00"
    return valor


def detalhe(conn: sqlite3.Connection, id_: int) -> dict | None:
    """Uma pendência inteira pra conferência na tela: payload cru do
    Fusion e a linha que `mapear_carga_track` monta a partir dele (ou o
    motivo de não montar)."""
    linha = conn.execute("SELECT * FROM cargas_fusion_int_erp WHERE id = ?", (id_,)).fetchone()
    if linha is None:
        return None
    payload = json.loads(linha["payload"])
    mapeada, motivo = (
        mapear_carga_track(payload) if linha["tipo_int"] in TIPOS_INTERNALIZADOS else (None, None)
    )
    return {
        "id": linha["id"],
        "codigo_int": linha["codigo_int"],
        "tipo_int": linha["tipo_int"],
        "t10_id": linha["t10_id"],
        "status": linha["status"],
        "erro": linha["erro"],
        "confirmado_em": service._sqlite_utc_iso(linha["confirmado_em"]),
        "confirmacao_erro": linha["confirmacao_erro"],
        "payload": payload,
        "linha_mapeada": (
            {chave: _serializavel(valor) for chave, valor in mapeada.items()} if mapeada else None
        ),
        "motivo_mapeamento": motivo,
        "entregas": (
            rastreio_entregas.listar_do_romaneio(conn, linha["t10_id"]) if linha["t10_id"] is not None else []
        ),
        "eventos": (
            rastreio_eventos.listar_do_romaneio(conn, linha["t10_id"]) if linha["t10_id"] is not None else []
        ),
    }


def listar(conn: sqlite3.Connection, *, status: str | None = None, limite: int = 100) -> dict:
    """Caixa de entrada pra tela/diagnóstico — sem o payload cru (pode ser
    grande); `contagem` cobre a tabela inteira, não só a página."""
    if status:
        linhas = conn.execute(
            "SELECT * FROM cargas_fusion_int_erp WHERE status = ? ORDER BY id DESC LIMIT ?",
            (status, limite),
        ).fetchall()
    else:
        linhas = conn.execute(
            "SELECT * FROM cargas_fusion_int_erp ORDER BY id DESC LIMIT ?", (limite,)
        ).fetchall()
    contagem = {
        linha["status"]: linha["n"]
        for linha in conn.execute("SELECT status, COUNT(*) AS n FROM cargas_fusion_int_erp GROUP BY status")
    }
    nao_confirmadas = conn.execute(
        "SELECT COUNT(*) AS n FROM cargas_fusion_int_erp WHERE status IN ('INTERNALIZADO', 'SUPERADO') "
        "AND confirmado_em IS NULL"
    ).fetchone()["n"]
    return {
        "contagem": contagem,
        "nao_confirmadas": nao_confirmadas,
        "entregas_contagem": rastreio_entregas.resumo(conn),
        "eventos_contagem": rastreio_eventos.resumo(conn),
        "itens": [
            {
                "id": linha["id"],
                "codigo_int": linha["codigo_int"],
                "tipo_int": linha["tipo_int"],
                "t10_id": linha["t10_id"],
                "status": linha["status"],
                "erro": linha["erro"],
                "tentativas": linha["tentativas"],
                "confirmado_em": service._sqlite_utc_iso(linha["confirmado_em"]),
                "confirmacao_erro": linha["confirmacao_erro"],
                "recebido_em": service._sqlite_utc_iso(linha["recebido_em"]),
                "updated_at": service._sqlite_utc_iso(linha["updated_at"]),
            }
            for linha in linhas
        ],
    }
