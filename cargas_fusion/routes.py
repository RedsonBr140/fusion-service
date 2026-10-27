"""As rotas HTTP da tool Cargas Fusion, agora como serviço independente.

Porte de meta-natal/backend/tools/cargas_fusion/routes.py: mesma lógica de
negócio, auth trocada de `login_required`/`tool_required("cargas_fusion")`
(sessão Flask same-origin) para o `login_required` deste serviço (Bearer
token, `auth/decorators.py`) — a autorização "tem a tool cargas_fusion" já
foi checada pelo meta-natal antes de emitir o token de troca, ou pela
allow-list local no login direto.
"""

from __future__ import annotations

from functools import wraps

from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from auth.decorators import login_required
from cargas_fusion import auto_envio, service
from database import get_db
from integrations.ciss import CissDbError
from integrations.fusion_portal import FusionPortalError

bp = Blueprint("cargas_fusion", __name__, url_prefix="/api")

MSG_CISS_NAO_CONFIGURADO = (
    "Consulta ao CISS DB2 indisponível: variáveis de ambiente não configuradas."
)
MSG_CISS_INDISPONIVEL = "Consulta ao CISS DB2 indisponível. Tente novamente."
MSG_PERIODO_INVALIDO = "Informe data_inicial e data_final em ISO (AAAA-MM-DDTHH:MM:SS)."
MSG_CARGA_NAO_ENCONTRADA = "Carga não encontrada."
MSG_DT_SAIDA_INVALIDA = "Data de saída inválida."
MSG_CARGAS_CISS_DESLIGADAS = (
    "Carga real (CISS) desligada — MOSTRAR_CARGAS_CISS=false. Use pré-carga."
)
MSG_SOMENTE_PEDIDOS_SOLTOS = (
    "Envio empacotado em carga desligado — SOMENTE_PEDIDOS_SOLTOS=true. "
    "Use /pre-cargas/<id_empresa>/<id_precarga>/enviar-solta."
)


def requer_cargas_ciss(view):
    """Bloqueia as rotas de carga real (CISS) quando `MOSTRAR_CARGAS_CISS`
    está desligada (padrão) — a formação de carga passou a acontecer
    dentro do próprio Fusion, a partir de pedidos enviados soltos (ver
    `montar_payload_pre_carga_solta`). Nunca afeta pré-carga: aplicado só
    nas rotas de `/cargas*` e na fila de carga real (`/fila`)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if not current_app.config["APP_CONFIG"].mostrar_cargas_ciss:
            return jsonify({"error": MSG_CARGAS_CISS_DESLIGADAS}), 403
        return view(*args, **kwargs)

    return wrapper


def requer_envio_em_carga(view):
    """Bloqueia o envio empacotado num `saveCarga` (carga real e
    pré-carga no modo padrão, `PC_...`) quando `SOMENTE_PEDIDOS_SOLTOS`
    está ligada (padrão) — só `saveEntregaServico` solto
    (`/enviar-solta`) fica disponível, pra forçar a formação de carga a
    acontecer dentro do Fusion. Independente de `requer_cargas_ciss`:
    cobre o caso de `mostrar_cargas_ciss` ser religada sem pensar nesta
    flag."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        if current_app.config["APP_CONFIG"].somente_pedidos_soltos:
            return jsonify({"error": MSG_SOMENTE_PEDIDOS_SOLTOS}), 403
        return view(*args, **kwargs)

    return wrapper


def _ciss_client():
    return current_app.config["CISS_DB_CLIENT"]


def _fusion_client():
    return current_app.config["FUSION_CLIENT"]


def _fusion_portal_client():
    return current_app.config["FUSION_PORTAL_CLIENT"]


def _corpo() -> dict:
    return request.get_json(silent=True) or {}


def _periodo_da_query() -> tuple[datetime, datetime] | None:
    try:
        inicio = datetime.fromisoformat(request.args["data_inicial"])
        fim = datetime.fromisoformat(request.args["data_final"])
    except (KeyError, ValueError):
        return None
    return inicio, fim


def _inteiro_ou_none(valor) -> int | None:
    try:
        return int(valor) if valor is not None and valor != "" else None
    except (TypeError, ValueError):
        return None


def _selecao_de_itens(valor) -> set[int] | None:
    if valor is None:
        return None
    if isinstance(valor, str):
        bruto = [v for v in valor.split(",") if v.strip()]
    else:
        bruto = valor
    numeros = {_inteiro_ou_none(v) for v in bruto}
    numeros.discard(None)
    return numeros


def _dt_saida_da_requisicao(valor) -> str | None:
    """`None`/vazio deixa `_dt_saida_padrao` decidir; levanta
    `service.DtSaidaInvalida` pra quem chamar devolver 400."""
    if not valor:
        return None
    return service.normalizar_dt_saida(valor)


def _sugestoes(detalhe: dict) -> tuple[dict | None, dict | None]:
    motorista = service.motorista_sugerido(_fusion_portal_client(), detalhe.get("IDMOTORISTA"))
    veiculo = service.veiculo_sugerido(_fusion_portal_client(), detalhe.get("PLACATRANSP"))
    return motorista, veiculo


def _montar_preview(
    client,
    id_empresa: int,
    id_planilha: int,
    *,
    motorista_codigo_erp: str | None = None,
    veiculo_codigo_erp: str | None = None,
    itens_selecionados: set[int] | None = None,
    dt_saida: str | None = None,
):
    detalhe = client.get_carga_detalhe(id_empresa, id_planilha)
    if detalhe is None:
        return None

    itens_todos = client.get_itens_carga(id_empresa, id_planilha)
    itens = service.filtrar_itens_selecionados(itens_todos, itens_selecionados)

    ids_clifor = {item.get("IDCLIFORENTREGA") or item.get("IDCLIFOR") for item in itens}
    ids_clifor.discard(None)
    clientes = {idc: client.get_cliente_fusion(idc) for idc in ids_clifor}

    conn = get_db()
    sugestao_motorista, sugestao_veiculo = _sugestoes(detalhe)

    motorista = (
        service.motorista_por_codigo_erp(_fusion_portal_client(), motorista_codigo_erp)
        if motorista_codigo_erp is not None
        else sugestao_motorista
    )
    veiculo = (
        service.veiculo_por_codigo_erp(_fusion_portal_client(), veiculo_codigo_erp)
        if veiculo_codigo_erp is not None
        else sugestao_veiculo
    )

    origem_por_pedido = client.get_origem_pre_cargas_da_carga(id_empresa, id_planilha)
    pedidos_excluidos = service.pedidos_cobertos_por_pre_carga(conn, origem_por_pedido)

    payload_carga, payload_entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_carga(
        detalhe, itens, clientes, motorista, veiculo, pedidos_excluidos=pedidos_excluidos, dt_saida=dt_saida
    )
    return (
        payload_carga,
        payload_entregas_servico,
        avisos,
        avisos_bloqueantes,
        itens_todos,
        sugestao_motorista,
        sugestao_veiculo,
        clientes,
    )


def _montar_preview_pre_carga(
    client,
    id_empresa: int,
    id_precarga: int,
    *,
    motorista_codigo_erp: str | None = None,
    veiculo_codigo_erp: str | None = None,
    itens_selecionados: set[int] | None = None,
    dt_saida: str | None = None,
):
    detalhe = client.get_pre_carga_detalhe(id_empresa, id_precarga)
    if detalhe is None:
        return None

    itens_todos = client.get_itens_pre_carga(id_precarga)
    itens = service.filtrar_itens_selecionados(itens_todos, itens_selecionados)

    ids_clifor = {item.get("IDCLIFORENTREGA") or item.get("IDCLIFOR") for item in itens}
    ids_clifor.discard(None)
    clientes = {idc: client.get_cliente_fusion(idc) for idc in ids_clifor}

    motorista = (
        service.motorista_por_codigo_erp(_fusion_portal_client(), motorista_codigo_erp)
        if motorista_codigo_erp is not None
        else None
    )
    veiculo = (
        service.veiculo_por_codigo_erp(_fusion_portal_client(), veiculo_codigo_erp)
        if veiculo_codigo_erp is not None
        else None
    )

    payload_carga, payload_entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_pre_carga(
        detalhe, itens, clientes, motorista, veiculo, dt_saida=dt_saida
    )
    return (
        payload_carga,
        payload_entregas_servico,
        avisos,
        avisos_bloqueantes,
        itens_todos,
        clientes,
        motorista,
        veiculo,
    )


# --- Cargas --------------------------------------------------------------


@bp.get("/cargas")
@login_required
@requer_cargas_ciss
def buscar_cargas():
    periodo = _periodo_da_query()
    if periodo is None:
        return jsonify({"error": MSG_PERIODO_INVALIDO}), 400
    inicio, fim = periodo

    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    ocultar_enviadas = request.args.get("ocultar_enviadas", "1") != "0"
    apenas_fusion = request.args.get("apenas_fusion", "1") != "0"

    try:
        limite = int(request.args.get("limite") or 100)
        id_empresa = _inteiro_ou_none(request.args.get("id_empresa"))
        cargas = client.get_cargas(
            inicio, fim, limite=limite, id_empresa=id_empresa, apenas_fusion=apenas_fusion
        )
        cargas_com_pedido = client.get_cargas_com_pedido_resolvido(
            [(carga["IDEMPRESA"], carga["IDPLANILHA"]) for carga in cargas]
        )
        cargas = [
            carga for carga in cargas if (carga["IDEMPRESA"], carga["IDPLANILHA"]) in cargas_com_pedido
        ]
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    ja_enviadas = service.cargas_ja_enviadas(get_db())
    pendentes = [carga for carga in cargas if (carga["IDEMPRESA"], carga["IDPLANILHA"]) not in ja_enviadas]

    # Uma carga cujos pedidos já foram todos enviados ao Fusion como
    # pré-carga não tem nada de novo pra decidir — marca ENVIADA aqui
    # mesmo, sem esperar alguém abrir a carga e clicar "enviar" (ver
    # CLAUDE.md, "Aviso bloqueante vs. informativo"). `marcar_carga_
    # coberta_automaticamente` só marca quando não há nada bloqueante e
    # nenhuma entrega genuína sobrou — nunca dispara envio real ao Fusion
    # a partir da listagem.
    recem_marcadas: set[tuple[int, int]] = set()
    try:
        for carga in pendentes:
            id_empresa_carga = carga["IDEMPRESA"]
            id_planilha_carga = carga["IDPLANILHA"]
            itens = client.get_itens_carga(id_empresa_carga, id_planilha_carga)
            origem_por_pedido = client.get_origem_pre_cargas_da_carga(id_empresa_carga, id_planilha_carga)
            resultado = service.marcar_carga_coberta_automaticamente(
                get_db(), id_empresa_carga, id_planilha_carga, carga, itens, origem_por_pedido
            )
            if resultado is not None:
                recem_marcadas.add((id_empresa_carga, id_planilha_carga))
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if ocultar_enviadas:
        cargas = [
            carga
            for carga in cargas
            if (carga["IDEMPRESA"], carga["IDPLANILHA"]) not in ja_enviadas
            and (carga["IDEMPRESA"], carga["IDPLANILHA"]) not in recem_marcadas
        ]

    return jsonify({"cargas": cargas}), 200


@bp.get("/cargas/busca-documento")
@login_required
@requer_cargas_ciss
def buscar_carga_por_documento():
    termo = (request.args.get("termo") or "").strip()
    if not termo:
        return jsonify({"error": "Informe o código da carga, o pedido ou a NF."}), 400

    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    try:
        resultado = service.localizar_carga_por_documento(client, termo)
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": "Nenhuma carga encontrada para esse código, pedido ou NF."}), 404

    id_empresa, id_planilha = resultado
    return jsonify({"id_empresa": id_empresa, "id_planilha": id_planilha}), 200


@bp.get("/empresas")
@login_required
def listar_empresas():
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    try:
        empresas = client.get_empresas()
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    return jsonify({"empresas": empresas}), 200


@bp.get("/cargas/<int:id_empresa>/<int:id_planilha>")
@login_required
@requer_cargas_ciss
def ver_carga(id_empresa: int, id_planilha: int):
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    try:
        dt_saida = _dt_saida_da_requisicao(request.args.get("dt_saida"))
    except service.DtSaidaInvalida:
        return jsonify({"error": MSG_DT_SAIDA_INVALIDA}), 400

    try:
        resultado = _montar_preview(
            client,
            id_empresa,
            id_planilha,
            motorista_codigo_erp=request.args.get("motorista_codigo_erp") or None,
            veiculo_codigo_erp=request.args.get("veiculo_codigo_erp") or None,
            itens_selecionados=_selecao_de_itens(request.args.get("itens")),
            dt_saida=dt_saida,
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    (
        payload_carga,
        payload_entregas_servico,
        avisos,
        _avisos_bloqueantes,
        itens_todos,
        sugestao_motorista,
        sugestao_veiculo,
        _clientes,
    ) = resultado
    item_fila = service.buscar_item_fila(get_db(), id_empresa, id_planilha)
    return (
        jsonify(
            {
                "payload": payload_carga,
                "payload_entregas_servico": payload_entregas_servico,
                "avisos": avisos,
                "itens": itens_todos,
                "sugestao_motorista": sugestao_motorista,
                "sugestao_veiculo": sugestao_veiculo,
                "item_fila": item_fila,
            }
        ),
        200,
    )


@bp.post("/cargas/<int:id_empresa>/<int:id_planilha>/enviar")
@login_required
@requer_cargas_ciss
@requer_envio_em_carga
def enviar_carga(id_empresa: int, id_planilha: int):
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    corpo = _corpo()
    try:
        dt_saida = _dt_saida_da_requisicao(corpo.get("dt_saida"))
    except service.DtSaidaInvalida:
        return jsonify({"error": MSG_DT_SAIDA_INVALIDA}), 400

    try:
        resultado = _montar_preview(
            client,
            id_empresa,
            id_planilha,
            motorista_codigo_erp=corpo.get("motorista_codigo_erp") or None,
            veiculo_codigo_erp=corpo.get("veiculo_codigo_erp") or None,
            itens_selecionados=_selecao_de_itens(corpo.get("itens")),
            dt_saida=dt_saida,
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    (
        payload_carga,
        payload_entregas_servico,
        avisos,
        avisos_bloqueantes,
        _itens_todos,
        _sugestao_motorista,
        _sugestao_veiculo,
        clientes,
    ) = resultado
    saida = service.processar_envio(
        get_db(),
        _fusion_client(),
        id_empresa,
        id_planilha,
        payload_carga,
        payload_entregas_servico,
        avisos,
        avisos_bloqueantes,
        clientes_por_idclifor=clientes,
        fusion_portal_client=_fusion_portal_client(),
        forcar=bool(corpo.get("forcar")),
    )
    return jsonify(saida), 200


@bp.post("/cargas/<int:id_empresa>/<int:id_planilha>/marcar-integrada")
@login_required
@requer_cargas_ciss
def marcar_carga_integrada(id_empresa: int, id_planilha: int):
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    try:
        detalhe = client.get_carga_detalhe(id_empresa, id_planilha)
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if detalhe is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    item = service.marcar_integrada_manualmente(get_db(), id_empresa, id_planilha)
    return jsonify({"fila": item}), 200


# --- Pré-cargas ----------------------------------------------------------


@bp.get("/pre-cargas")
@login_required
def buscar_pre_cargas():
    periodo = _periodo_da_query()
    if periodo is None:
        return jsonify({"error": MSG_PERIODO_INVALIDO}), 400
    inicio, fim = periodo

    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    ocultar_enviadas = request.args.get("ocultar_enviadas", "1") != "0"
    apenas_fusion = request.args.get("apenas_fusion", "1") != "0"

    try:
        limite = int(request.args.get("limite") or 100)
        id_empresa = _inteiro_ou_none(request.args.get("id_empresa"))
        pre_cargas = client.get_pre_cargas(
            inicio, fim, limite=limite, id_empresa=id_empresa, apenas_fusion=apenas_fusion
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if ocultar_enviadas:
        ja_enviadas = service.pre_cargas_ja_enviadas(get_db())
        pre_cargas = [
            pc for pc in pre_cargas if (pc["IDEMPRESA"], pc["IDPRECARGA"]) not in ja_enviadas
        ]

    return jsonify({"pre_cargas": pre_cargas}), 200


@bp.get("/pre-cargas/<int:id_empresa>/<int:id_precarga>")
@login_required
def ver_pre_carga(id_empresa: int, id_precarga: int):
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    try:
        dt_saida = _dt_saida_da_requisicao(request.args.get("dt_saida"))
    except service.DtSaidaInvalida:
        return jsonify({"error": MSG_DT_SAIDA_INVALIDA}), 400

    try:
        resultado = _montar_preview_pre_carga(
            client,
            id_empresa,
            id_precarga,
            motorista_codigo_erp=request.args.get("motorista_codigo_erp") or None,
            veiculo_codigo_erp=request.args.get("veiculo_codigo_erp") or None,
            itens_selecionados=_selecao_de_itens(request.args.get("itens")),
            dt_saida=dt_saida,
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    (
        payload_carga,
        payload_entregas_servico,
        avisos,
        _avisos_bloqueantes,
        itens_todos,
        _clientes,
        motorista,
        veiculo,
    ) = resultado
    item_fila = service.buscar_item_fila_pre_carga(get_db(), id_empresa, id_precarga)
    return (
        jsonify(
            {
                "payload": payload_carga,
                "payload_entregas_servico": payload_entregas_servico,
                "avisos": avisos,
                "itens": itens_todos,
                "motorista": motorista,
                "veiculo": veiculo,
                "item_fila": item_fila,
                # Só permite o botão de "enviar solta" no frontend quando a
                # pré-carga resolve pra um único pedido — ver
                # service.montar_payload_pre_carga_solta.
                "permite_envio_solto": service.contar_pedidos(itens_todos) == 1,
            }
        ),
        200,
    )


@bp.post("/pre-cargas/<int:id_empresa>/<int:id_precarga>/enviar")
@login_required
@requer_envio_em_carga
def enviar_pre_carga(id_empresa: int, id_precarga: int):
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    corpo = _corpo()
    motorista_codigo_erp = corpo.get("motorista_codigo_erp") or None
    veiculo_codigo_erp = corpo.get("veiculo_codigo_erp") or None
    try:
        dt_saida = _dt_saida_da_requisicao(corpo.get("dt_saida"))
    except service.DtSaidaInvalida:
        return jsonify({"error": MSG_DT_SAIDA_INVALIDA}), 400

    try:
        resultado = _montar_preview_pre_carga(
            client,
            id_empresa,
            id_precarga,
            motorista_codigo_erp=motorista_codigo_erp,
            veiculo_codigo_erp=veiculo_codigo_erp,
            itens_selecionados=_selecao_de_itens(corpo.get("itens")),
            dt_saida=dt_saida,
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    (
        payload_carga,
        payload_entregas_servico,
        avisos,
        avisos_bloqueantes,
        _itens_todos,
        clientes,
        motorista,
        veiculo,
    ) = resultado
    saida = service.processar_envio_pre_carga(
        get_db(),
        _fusion_client(),
        id_empresa,
        id_precarga,
        service.codigo_pre_carga(id_empresa, id_precarga),
        motorista_codigo_erp,
        (motorista or {}).get("nome"),
        veiculo_codigo_erp,
        (veiculo or {}).get("descricao") or (veiculo or {}).get("placa"),
        payload_carga,
        payload_entregas_servico,
        avisos,
        avisos_bloqueantes,
        clientes_por_idclifor=clientes,
        fusion_portal_client=_fusion_portal_client(),
        forcar=bool(corpo.get("forcar")),
    )
    return jsonify(saida), 200


@bp.post("/pre-cargas/<int:id_empresa>/<int:id_precarga>/enviar-solta")
@login_required
def enviar_pre_carga_solta(id_empresa: int, id_precarga: int):
    """Modo alternativo: manda só `saveEntregaServico`, sem `saveCarga` —
    pedido fica disponível pra agrupamento manual em "Formação de carga"
    no próprio Fusion, em vez de já chegar empacotado num `cargas[]`. Só
    vale pra pré-carga de pedido único; mais de um pedido devolve 400
    sem chamar o Fusion (`service.montar_payload_pre_carga_solta` que
    decide isso, via aviso bloqueante)."""
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    corpo = _corpo()

    try:
        resultado = service.preparar_envio_pre_carga_solta(
            client,
            id_empresa,
            id_precarga,
            itens_selecionados=_selecao_de_itens(corpo.get("itens")),
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    payload_entregas_servico, avisos, avisos_bloqueantes, _itens_todos, clientes = resultado

    saida = service.processar_envio_pre_carga_solta(
        get_db(),
        _fusion_client(),
        id_empresa,
        id_precarga,
        service.codigo_pre_carga(id_empresa, id_precarga),
        payload_entregas_servico,
        avisos,
        avisos_bloqueantes,
        clientes_por_idclifor=clientes,
        fusion_portal_client=_fusion_portal_client(),
        forcar=bool(corpo.get("forcar")),
    )
    return jsonify(saida), 200


@bp.get("/pre-cargas/fila")
@login_required
def ver_fila_pre_cargas():
    apenas_pendentes = request.args.get("todas") != "1"
    return (
        jsonify({"fila": service.listar_fila_pre_cargas(get_db(), apenas_pendentes=apenas_pendentes)}),
        200,
    )


@bp.delete("/pre-cargas/fila/<int:id_>")
@login_required
def remover_da_fila_pre_carga(id_: int):
    if not service.remover_da_fila_pre_carga(get_db(), id_):
        return jsonify({"error": "Item da fila não encontrado."}), 404
    return "", 204


# --- Envio automático de pré-carga solta --------------------------------


@bp.get("/pre-cargas/envio-automatico")
@login_required
def ver_estado_envio_automatico():
    conn = get_db()
    estado = auto_envio.obter_estado(conn)
    estado["multi_pedido_ignorado"] = auto_envio.resumo_multi_pedido(conn)
    return jsonify(estado), 200


@bp.post("/pre-cargas/envio-automatico/pausar")
@login_required
def pausar_envio_automatico():
    auto_envio.pausar(get_db())
    return jsonify(auto_envio.obter_estado(get_db())), 200


@bp.post("/pre-cargas/envio-automatico/retomar")
@login_required
def retomar_envio_automatico():
    auto_envio.retomar(get_db())
    return jsonify(auto_envio.obter_estado(get_db())), 200


@bp.post("/pre-cargas/envio-automatico/executar-agora")
@login_required
def executar_ciclo_agora():
    """Roda um tick do ciclo automático na hora, fora do intervalo do
    `BackgroundScheduler` (`AUTO_ENVIO_PRE_CARGA_INTERVALO_SEGUNDOS`) —
    pra testar/observar sem esperar, ou reagir a um achado do
    Diagnóstico sem precisar de redeploy. Usa o mesmo `redis_client`
    (`current_app.config["AUTO_ENVIO_REDIS_CLIENT"]`) e os mesmos
    clients do `_tick` agendado em `app.py` — o lock é compartilhado de
    verdade, então clicar durante um tick automático em andamento
    devolve `{"status": "pulado", ...}`, nunca dispara em dobro."""
    redis_client = current_app.config.get("AUTO_ENVIO_REDIS_CLIENT")
    if redis_client is None:
        return (
            jsonify({"error": "Ciclo automático desligado neste ambiente (AUTO_ENVIO_PRE_CARGA_ATIVO=false)."}),
            409,
        )

    config = current_app.config["APP_CONFIG"]
    resultado = auto_envio.executar_ciclo(
        get_db(),
        redis_client,
        _ciss_client(),
        _fusion_client(),
        _fusion_portal_client(),
        janela_horas=config.auto_envio_pre_carga_janela_horas,
        corte=config.auto_envio_pre_carga_corte,
        discord_webhook_url=config.discord_webhook_url,
        lock_timeout_segundos=config.auto_envio_pre_carga_intervalo_segundos * 3,
    )
    return jsonify(resultado), 200


# --- Métricas ------------------------------------------------------------


@bp.get("/metricas")
@login_required
def ver_metricas():
    conn = get_db()
    try:
        dias = int(request.args.get("dias", 14))
    except ValueError:
        dias = 14
    dias = max(1, min(dias, 90))

    metricas = service.metricas_envio(conn, dias=dias)
    metricas["multi_pedido"] = auto_envio.resumo_multi_pedido(conn)
    metricas["multi_pedido"]["por_dia"] = auto_envio.resumo_multi_pedido_por_dia(conn, dias=dias)
    return jsonify(metricas), 200


@bp.get("/pre-cargas/envio-automatico/execucoes")
@login_required
def ver_execucoes_envio_automatico():
    try:
        limite = int(request.args.get("limite", 50))
    except ValueError:
        limite = 50
    limite = max(1, min(limite, 200))
    return jsonify({"execucoes": auto_envio.listar_execucoes(get_db(), limite=limite)}), 200


# --- Fila de envio -----------------------------------------------------


@bp.get("/fila")
@login_required
@requer_cargas_ciss
def ver_fila():
    apenas_pendentes = request.args.get("todas") != "1"
    return jsonify({"fila": service.listar_fila(get_db(), apenas_pendentes=apenas_pendentes)}), 200


@bp.delete("/fila/<int:id_>")
@login_required
@requer_cargas_ciss
def remover_da_fila(id_: int):
    if not service.remover_da_fila(get_db(), id_):
        return jsonify({"error": "Item da fila não encontrado."}), 404
    return "", 204


# --- Veículo: lido ao vivo do Fusion ---------------------------------------


@bp.get("/veiculos")
@login_required
def buscar_veiculos():
    termo = request.args.get("termo") or ""
    try:
        veiculos = service.buscar_veiculos_fusion(_fusion_portal_client(), termo)
    except FusionPortalError:
        return jsonify({"error": "Portal do Fusion indisponível. Tente novamente."}), 503
    return jsonify({"veiculos": veiculos}), 200


# --- Motorista: lido ao vivo do Fusion -------------------------------------


@bp.get("/motoristas")
@login_required
def buscar_motoristas():
    termo = request.args.get("termo") or ""
    try:
        motoristas = service.buscar_motoristas_fusion(_fusion_portal_client(), termo)
    except FusionPortalError:
        return jsonify({"error": "Portal do Fusion indisponível. Tente novamente."}), 503
    return jsonify({"motoristas": motoristas}), 200


# --- Cadastro: ajudante -----------------------------------------------------


@bp.get("/ajudantes")
@login_required
def listar_ajudantes():
    apenas_ativos = request.args.get("todos") != "1"
    return jsonify({"ajudantes": service.listar_ajudantes(get_db(), apenas_ativos=apenas_ativos)}), 200


@bp.get("/ajudantes/candidatos")
@login_required
def candidatos_ajudante():
    termo = (request.args.get("termo") or "").strip()
    if not termo:
        return jsonify({"candidatos": []}), 200

    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    try:
        candidatos = client.get_motoristas_candidatos(termo)
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    return jsonify({"candidatos": candidatos}), 200


@bp.post("/ajudantes")
@login_required
def criar_ajudante():
    try:
        linha = service.salvar_ajudante(get_db(), _corpo())
    except ValueError as erro:
        return jsonify({"error": str(erro)}), 400
    return jsonify({"ajudante": linha}), 201


@bp.put("/ajudantes/<int:id_>")
@login_required
def atualizar_ajudante(id_: int):
    linha = service.atualizar_ajudante(get_db(), id_, _corpo())
    if linha is None:
        return jsonify({"error": "Ajudante não encontrado."}), 404
    return jsonify({"ajudante": linha}), 200


@bp.post("/ajudantes/<int:id_>/sincronizar")
@login_required
def sincronizar_ajudante(id_: int):
    try:
        linha = service.sincronizar_ajudante(get_db(), _fusion_client(), id_)
    except service.SincronizacaoFusionError as erro:
        status = 404 if "não encontrado" in str(erro) else 503
        return jsonify({"error": str(erro)}), status
    return jsonify({"ajudante": linha}), 200
