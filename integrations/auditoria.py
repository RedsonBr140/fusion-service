"""Toda ação da integração → canal de atividade do Discord.

`ClienteAuditado` envolve o `FusionClient` e o `CissEscritaClient` em
`create_app` (mesmo objeto que rotas, ciclo automático e rastreio usam),
então cada escrita no Fusion e no CISS vira um embed sem tocar em nenhum
call site — e sem acoplar Discord em `integrations/fusion.py`/
`ciss_escrita.py`, que continuam sendo só transporte.

Só **ações** (escritas e consumo da fila) são auditadas. Leitura
(`detalhe_entrega`, `consultar_veiculo`, ...) e o heartbeat
(`avisar_vivo`, um por ciclo, não muda nada) passam direto. `get_int_erp`
vazio também — não consumiu nada.

Falha da chamada (exceção) sai em vermelho aqui também, além de propagar
normal; o canal de **erros** continua sendo decisão da camada de negócio
(ex. pendência não gravada, pré-carga que falhou), não desta.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from integrations import discord

logger = logging.getLogger(__name__)

Campos = list[tuple[str, object, bool]]


def quem() -> str:
    """Usuário logado quando a ação veio de uma rota; senão, o ciclo
    automático/rastreio (thread do scheduler, sem request)."""
    try:
        from flask import g, has_request_context

        if has_request_context() and getattr(g, "user", None):
            usuario = g.user
            return f"{usuario.get('name')} ({usuario.get('ciss_code')})"
    except Exception:
        pass
    return "automático (scheduler)"


def _lista(valores, limite: int = 15) -> str:
    valores = [str(v) for v in valores if v not in (None, "")]
    if not valores:
        return "—"
    texto = ", ".join(f"`{v}`" for v in valores[:limite])
    return texto + (f" … +{len(valores) - limite}" if len(valores) > limite else "")


def _fusion_recusou(resultado: object) -> bool:
    """Fusion responde erro sem exceção (HTTP 200): texto/valores com
    "erro", `errors` não vazio, `success` falso/vazio, `{"msg": ...}`.
    Mesmo catálogo instável de CLAUDE.md — heurística só pra cor do embed,
    nunca pra decisão de negócio."""
    if isinstance(resultado, str):
        return "erro" in resultado.lower() or "nao " in resultado.lower()
    if isinstance(resultado, dict):
        if resultado.get("errors") or resultado.get("erro_detalhes") or "msg" in resultado:
            return True
        if "success" in resultado:
            return resultado["success"] in (False, [], "", None)
        return any(isinstance(v, str) and "erro" in v.lower() for v in resultado.values())
    return False


def _resultado(resultado: object) -> str:
    texto = resultado if isinstance(resultado, str) else json.dumps(resultado, ensure_ascii=False, default=str)
    return f"```{texto[:900]}```"


# --- Fusion --------------------------------------------------------------


def _save_carga(args, kwargs, resultado) -> tuple[str, Campos]:
    cargas = args[0] if args else kwargs.get("cargas") or []
    return "🚚 Fusion · saveCarga", [
        ("Cargas", _lista(c.get("codigo_erp") for c in cargas), False),
        ("Entregas", sum(len(c.get("entrega") or []) for c in cargas), True),
    ]


def _save_entrega_servico(args, kwargs, resultado) -> tuple[str, Campos]:
    entregas = args[0] if args else kwargs.get("entregas") or []
    return "📦 Fusion · saveEntregaServico", [
        ("Pedidos", _lista(e.get("pedido_erp") for e in entregas), False),
        ("Filial", _lista({e.get("empresa_fat") for e in entregas}), True),
        ("Itens", sum(len(e.get("itens") or []) for e in entregas), True),
    ]


def _send_clientes(args, kwargs, resultado) -> tuple[str, Campos]:
    clientes = args[0] if args else kwargs.get("clientes") or []
    return "👤 Fusion · sendClientes", [
        ("Clientes", _lista(c.get("codigo_cliente") for c in clientes), False),
    ]


def _send_motoristas(args, kwargs, resultado) -> tuple[str, Campos]:
    pessoas = args[0] if args else kwargs.get("motoristas") or []
    return "🧑‍✈️ Fusion · sendMotoristas", [
        ("Códigos", _lista(p.get("codigo_erp") for p in pessoas), False),
        ("Tipo", _lista({p.get("tipo") for p in pessoas}), True),
    ]


def _send_veiculos(args, kwargs, resultado) -> tuple[str, Campos]:
    veiculos = args[0] if args else kwargs.get("veiculos") or []
    return "🚛 Fusion · sendVeiculos", [
        ("Veículos", _lista(v.get("codigo_erp") or v.get("placa") for v in veiculos), False),
    ]


def _get_int_erp(args, kwargs, resultado) -> tuple[str, Campos] | None:
    if not resultado:
        return None  # fila vazia: nada consumido, não é ação
    tipos: dict[str, int] = {}
    for item in resultado:
        tipo = str(item.get("TIPO_INT") or "?").replace("T10_ROMANEIO_", "")
        tipos[tipo] = tipos.get(tipo, 0) + 1
    return "📥 Fusion · getIntErp (fila consumida)", [
        ("Pendências", len(resultado), True),
        ("Tipos", " · ".join(f"{t}: {n}" for t, n in sorted(tipos.items())), True),
        ("CODIGO_INT", _lista((i.get("CODIGO_INT") for i in resultado), 20), False),
        ("Romaneios", _lista(sorted({i.get("t10_id") for i in resultado if i.get("t10_id")}), 20), False),
    ]


def _obtem_eventos(args, kwargs, resultado) -> tuple[str, Campos] | None:
    if not resultado:
        return None  # fila vazia: nada consumido, não é ação
    tipos: dict[str, int] = {}
    for evento in resultado:
        tipo = f"{evento.get('t15_tipo')} {evento.get('t15_descricao') or ''}".strip()
        tipos[tipo] = tipos.get(tipo, 0) + 1
    ids = [e.get("t15_id") for e in resultado]
    return "📥 Fusion · obtemEventosPorID (fila consumida)", [
        ("Eventos", len(resultado), True),
        ("t15_id", f"`{ids[0]}` … `{ids[-1]}`", True),
        ("Tipos", " · ".join(f"{t}: {n}" for t, n in sorted(tipos.items()))[:1000], False),
        ("Romaneios", _lista(sorted({e.get("t15_romaneio_id") for e in resultado if e.get("t15_romaneio_id")}), 20), False),
    ]


def _set_int_erp(args, kwargs, resultado) -> tuple[str, Campos]:
    int_id = args[0] if args else kwargs.get("int_id")
    carga = kwargs.get("carga") if "carga" in kwargs else (args[2] if len(args) > 2 else None)
    return "✅ Fusion · setIntErp (confirmação)", [
        ("CODIGO_INT", f"`{int_id}`", True),
        ("carga", f"`{carga}`" if carga else "—", True),
    ]


# --- CISS ------------------------------------------------------------------


def _upsert_carga_track(args, kwargs, resultado) -> tuple[str, Campos]:
    linha = args[0] if args else kwargs.get("linha") or {}
    return "🗄️ CISS · CARGAS_FUSION_TRACK", [
        ("Romaneio", f"`{linha.get('IDCARGAFUSION')}`", True),
        ("Empresa", linha.get("IDEMPRESA"), True),
        ("Motorista", linha.get("IDMOTORISTA"), True),
        ("Veículo", linha.get("IDVEICULO"), True),
        ("Entregas", linha.get("NUMEROENTREGAS"), True),
        ("Destino", linha.get("DESTINO"), False),
    ]


def _upsert_entrega_track(args, kwargs, resultado) -> tuple[str, Campos]:
    linha = args[0] if args else kwargs.get("linha") or {}
    return "📍 CISS · ENTREGAS_FUSION_TRACK", [
        ("Romaneio", f"`{linha.get('IDCARGAFUSION')}`", True),
        ("NF", f"`{linha.get('NUMNOTA')}` (planilha {linha.get('IDPLANILHA')})", True),
        ("Empresa", linha.get("IDEMPRESA"), True),
        ("Status", linha.get("STATUSENTREGA"), True),
    ]


def _gravar_evento_track(args, kwargs, resultado) -> tuple[str, Campos]:
    evento = args[0] if args else kwargs.get("evento") or {}
    itens = (args[1] if len(args) > 1 else kwargs.get("itens")) or []
    campos: Campos = [
        ("Evento", f"`{evento.get('IDEVENTOFUSION')}` {evento.get('DESCRICAO') or ''}", False),
        ("Romaneio", f"`{evento.get('IDCARGAFUSION')}`", True),
        ("Entrega (sequência)", evento.get("IDENTREGAFUSION"), True),
        ("Quando", evento.get("DATAHORA"), True),
    ]
    if evento.get("DESCRDEVOLUCAO"):
        campos.append(("Motivo", evento.get("DESCRDEVOLUCAO"), True))
    if itens:
        campos.append(("Itens devolvidos", _lista(f"{i.get('IDSUBPRODUTO')}×{i.get('QTDDEVOLVIDO')}" for i in itens), False))
    return "🛰️ CISS · EVENTOS_FUSION_TRACK", campos


def _sincronizar_cargas_ciss(args, kwargs, resultado) -> tuple[str, Campos]:
    romaneio = args[0] if args else kwargs.get("id_carga_fusion")
    inserir = (args[1] if len(args) > 1 else kwargs.get("inserir")) or []
    remover = (args[2] if len(args) > 2 else kwargs.get("remover")) or []
    campos: Campos = [("Romaneio", f"`{romaneio}`", True)]
    if inserir:
        campos.append(("Vinculadas", _lista(f"{e}_{c}" for e, c in inserir), False))
    if remover:
        campos.append(("Removidas", _lista(f"{e}_{c}" for e, c in remover), False))
    return "🔗 CISS · CARGAS_CISS_FUSION_TRACK", campos


FORMATADORES: dict[str, Callable] = {
    "save_carga": _save_carga,
    "save_entrega_servico": _save_entrega_servico,
    "send_clientes": _send_clientes,
    "send_motoristas": _send_motoristas,
    "send_veiculos": _send_veiculos,
    "get_int_erp": _get_int_erp,
    "set_int_erp": _set_int_erp,
    "obtem_eventos": _obtem_eventos,
    "upsert_carga_fusion_track": _upsert_carga_track,
    "upsert_entrega_fusion_track": _upsert_entrega_track,
    "gravar_evento_fusion_track": _gravar_evento_track,
    "sincronizar_cargas_ciss": _sincronizar_cargas_ciss,
}


class ClienteAuditado:
    """Proxy transparente: métodos fora de `FORMATADORES` passam direto."""

    def __init__(self, cliente) -> None:
        self._cliente = cliente

    def __getattr__(self, nome: str):
        atributo = getattr(self._cliente, nome)
        formatador = FORMATADORES.get(nome)
        if formatador is None or not callable(atributo):
            return atributo

        def _auditado(*args, **kwargs):
            try:
                resultado = atributo(*args, **kwargs)
            except Exception as exc:
                _registrar(formatador, args, kwargs, None, exc)
                raise
            _registrar(formatador, args, kwargs, resultado, None)
            return resultado

        return _auditado


def _registrar(formatador, args, kwargs, resultado, exc) -> None:
    # Auditoria nunca derruba a ação auditada.
    try:
        montado = formatador(args, kwargs, resultado)
        if montado is None:
            return
        titulo, campos = montado
        campos = [*campos, ("Por", quem(), True)]
        if exc is not None:
            campos.append(("Falhou", f"`{type(exc).__name__}`", True))
            discord.atividade(titulo, cor=discord.COR_ERRO, campos=campos)
            return
        cor = discord.COR_SUCESSO
        if resultado is not None and titulo.startswith(("🚚", "📦", "👤", "🧑", "🚛", "✅")):
            campos.append(("Resposta do Fusion", _resultado(resultado), False))
            if _fusion_recusou(resultado):
                cor = discord.COR_AVISO
                titulo += " — recusado pelo Fusion"
        discord.atividade(titulo, cor=cor, campos=campos)
    except Exception:
        logger.warning("Falha montando auditoria de %s", getattr(formatador, "__name__", "?"), exc_info=True)
