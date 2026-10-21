"""As rotas HTTP da tool Cargas Fusion, agora como serviço independente.

Porte de meta-natal/backend/tools/cargas_fusion/routes.py: mesma lógica de
negócio, auth trocada de `login_required`/`tool_required("cargas_fusion")`
(sessão Flask same-origin) para o `login_required` deste serviço (Bearer
token, `auth/decorators.py`) — a autorização "tem a tool cargas_fusion" já
foi checada pelo meta-natal antes de emitir o token de troca, ou pela
allow-list local no login direto.
"""

from __future__ import annotations

from datetime import datetime

from flask import Blueprint, current_app, jsonify, request

from auth.decorators import login_required
from cargas_fusion import service
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

    payload_carga, payload_entregas_servico, avisos = service.montar_payload_carga(
        detalhe, itens, clientes, motorista, veiculo, pedidos_excluidos=pedidos_excluidos
    )
    return (
        payload_carga,
        payload_entregas_servico,
        avisos,
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

    payload_carga, payload_entregas_servico, avisos = service.montar_payload_pre_carga(
        detalhe, itens, clientes, motorista, veiculo
    )
    return payload_carga, payload_entregas_servico, avisos, itens_todos, clientes, motorista, veiculo


# --- Cargas --------------------------------------------------------------


@bp.get("/cargas")
@login_required
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

    if ocultar_enviadas:
        ja_enviadas = service.cargas_ja_enviadas(get_db())
        cargas = [carga for carga in cargas if (carga["IDEMPRESA"], carga["IDPLANILHA"]) not in ja_enviadas]

    return jsonify({"cargas": cargas}), 200


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
def ver_carga(id_empresa: int, id_planilha: int):
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    try:
        resultado = _montar_preview(
            client,
            id_empresa,
            id_planilha,
            motorista_codigo_erp=request.args.get("motorista_codigo_erp") or None,
            veiculo_codigo_erp=request.args.get("veiculo_codigo_erp") or None,
            itens_selecionados=_selecao_de_itens(request.args.get("itens")),
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    (
        payload_carga,
        payload_entregas_servico,
        avisos,
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
def enviar_carga(id_empresa: int, id_planilha: int):
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    corpo = _corpo()
    try:
        resultado = _montar_preview(
            client,
            id_empresa,
            id_planilha,
            motorista_codigo_erp=corpo.get("motorista_codigo_erp") or None,
            veiculo_codigo_erp=corpo.get("veiculo_codigo_erp") or None,
            itens_selecionados=_selecao_de_itens(corpo.get("itens")),
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    (
        payload_carga,
        payload_entregas_servico,
        avisos,
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
        clientes_por_idclifor=clientes,
        fusion_portal_client=_fusion_portal_client(),
        forcar=bool(corpo.get("forcar")),
    )
    return jsonify(saida), 200


@bp.post("/cargas/<int:id_empresa>/<int:id_planilha>/marcar-integrada")
@login_required
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
        resultado = _montar_preview_pre_carga(
            client,
            id_empresa,
            id_precarga,
            motorista_codigo_erp=request.args.get("motorista_codigo_erp") or None,
            veiculo_codigo_erp=request.args.get("veiculo_codigo_erp") or None,
            itens_selecionados=_selecao_de_itens(request.args.get("itens")),
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    payload_carga, payload_entregas_servico, avisos, itens_todos, _clientes, motorista, veiculo = resultado
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
            }
        ),
        200,
    )


@bp.post("/pre-cargas/<int:id_empresa>/<int:id_precarga>/enviar")
@login_required
def enviar_pre_carga(id_empresa: int, id_precarga: int):
    client = _ciss_client()
    if client is None:
        return jsonify({"error": MSG_CISS_NAO_CONFIGURADO}), 503

    corpo = _corpo()
    motorista_codigo_erp = corpo.get("motorista_codigo_erp") or None
    veiculo_codigo_erp = corpo.get("veiculo_codigo_erp") or None
    try:
        resultado = _montar_preview_pre_carga(
            client,
            id_empresa,
            id_precarga,
            motorista_codigo_erp=motorista_codigo_erp,
            veiculo_codigo_erp=veiculo_codigo_erp,
            itens_selecionados=_selecao_de_itens(corpo.get("itens")),
        )
    except CissDbError:
        return jsonify({"error": MSG_CISS_INDISPONIVEL}), 503

    if resultado is None:
        return jsonify({"error": MSG_CARGA_NAO_ENCONTRADA}), 404

    payload_carga, payload_entregas_servico, avisos, _itens_todos, clientes, motorista, veiculo = resultado
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


# --- Fila de envio -----------------------------------------------------


@bp.get("/fila")
@login_required
def ver_fila():
    apenas_pendentes = request.args.get("todas") != "1"
    return jsonify({"fila": service.listar_fila(get_db(), apenas_pendentes=apenas_pendentes)}), 200


@bp.delete("/fila/<int:id_>")
@login_required
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
