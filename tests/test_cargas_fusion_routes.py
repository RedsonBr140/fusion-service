"""As rotas HTTP da tool Cargas Fusion, no fusion-service — porte de
meta-natal/backend/tests/test_cargas_fusion_routes.py. Auth trocada de
sessão Flask (cookie) para Bearer token: `autenticar()` grava o token em
`client.environ_base`, que o test client do Werkzeug aplica a toda
requisição seguinte — efeito equivalente ao cookie de sessão do teste
original, sem precisar tocar nos ~150 call sites de `client.get/post/...`.
CISS e Fusion são sempre dublês.
"""

import dataclasses

import pytest

from auth import tokens
from database import connect
from integrations.ciss import CissDbUnavailable
from integrations.fusion import FusionUnavailable
from integrations.fusion_portal import FusionPortalError

PREFIXO = "/api"


def criar_usuario_local(config, ciss_code="123", name="Usuário Teste", active=1):
    conn = connect(config.database_path)
    with conn:
        conn.execute(
            "INSERT INTO usuarios_locais (username, ciss_code, name, active) VALUES (?, ?, ?, ?)",
            (ciss_code, ciss_code, name, active),
        )
    conn.close()
    return ciss_code


def autenticar(client, config, ciss_code="123", name="Usuário Teste"):
    token = tokens.criar_token_sessao(ciss_code=ciss_code, name=name, secret=config.secret_key)
    client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"


def usuario_com_a_tool(client, config, ciss_code="com-tool"):
    """Nome mantido do teste original: aqui, "ter a tool" é só estar na
    allow-list local e autenticado — não há tool_required separado, a
    allow-list já é o controle de acesso deste serviço."""
    criar_usuario_local(config, ciss_code=ciss_code)
    autenticar(client, config, ciss_code=ciss_code)
    return ciss_code


class CissClientFalso:
    def __init__(
        self,
        *,
        cargas=(),
        detalhe=None,
        itens=(),
        clientes=None,
        candidatos_motorista=(),
        empresas=(),
        erro=None,
        planilhas_resolvidas=None,
        pre_cargas=(),
        detalhe_pre_carga=None,
        itens_pre_carga=(),
        origem_pre_cargas=None,
        por_pedido=None,
        por_nota=None,
    ):
        self._cargas = list(cargas)
        self._detalhe = detalhe
        self._itens = list(itens)
        self._clientes = clientes or {}
        self._candidatos_motorista = list(candidatos_motorista)
        self._empresas = list(empresas)
        self._erro = erro
        # None = comportamento antigo: toda carga listada tem pedido
        # resolvido — só precisa de um valor explícito pra testar o filtro.
        # Senão, um set de pares (IDEMPRESA, IDPLANILHA).
        self._planilhas_resolvidas = planilhas_resolvidas
        self._pre_cargas = list(pre_cargas)
        self._detalhe_pre_carga = detalhe_pre_carga
        self._itens_pre_carga = list(itens_pre_carga)
        # {} = nenhum pedido desta carga veio de pré-carga — comportamento
        # padrão (não exclui nada do envio).
        self._origem_pre_cargas = origem_pre_cargas or {}
        self._por_pedido = por_pedido
        self._por_nota = por_nota
        self.id_empresa_recebido = "não chamado"

    def get_carga_por_pedido(self, numero_pedido):
        if self._erro is not None:
            raise self._erro
        return self._por_pedido

    def get_carga_por_nota(self, numnota, serienota):
        if self._erro is not None:
            raise self._erro
        return self._por_nota

    def get_cargas(self, data_inicial, data_final, limite=100, id_empresa=None, apenas_fusion=True):
        if self._erro is not None:
            raise self._erro
        self.id_empresa_recebido = id_empresa
        return self._cargas

    def get_cargas_com_pedido_resolvido(self, cargas):
        if self._erro is not None:
            raise self._erro
        if self._planilhas_resolvidas is None:
            return set(cargas)
        return set(cargas) & set(self._planilhas_resolvidas)

    def get_empresas(self):
        if self._erro is not None:
            raise self._erro
        return self._empresas

    def get_carga_detalhe(self, id_empresa, id_planilha):
        if self._erro is not None:
            raise self._erro
        return self._detalhe

    def get_itens_carga(self, id_empresa, id_planilha):
        if self._erro is not None:
            raise self._erro
        return self._itens

    def get_cliente_fusion(self, id_clifor):
        if self._erro is not None:
            raise self._erro
        return self._clientes.get(id_clifor)

    def get_motoristas_candidatos(self, termo, limite=50):
        if self._erro is not None:
            raise self._erro
        return self._candidatos_motorista

    def get_origem_pre_cargas_da_carga(self, id_empresa, id_planilha):
        if self._erro is not None:
            raise self._erro
        return self._origem_pre_cargas

    def get_pre_cargas(self, data_inicial, data_final, limite=100, id_empresa=None, apenas_fusion=True):
        if self._erro is not None:
            raise self._erro
        self.id_empresa_recebido = id_empresa
        return self._pre_cargas

    def get_pre_carga_detalhe(self, id_empresa, id_precarga):
        if self._erro is not None:
            raise self._erro
        return self._detalhe_pre_carga

    def get_itens_pre_carga(self, id_precarga):
        if self._erro is not None:
            raise self._erro
        return self._itens_pre_carga


class FusionClienteFalso:
    def __init__(
        self, *, resultado_save=None, resultado_entrega_servico=None, resultado_clientes=None, erro=None
    ):
        self._resultado_save = resultado_save or {"success": "1", "errors": []}
        self._resultado_entrega_servico = resultado_entrega_servico or {"success": ["ok"], "errors": []}
        self._resultado_clientes = resultado_clientes or {"success": ["ok"], "errors": []}
        self._erro = erro
        self.enviado = None
        self.enviado_entrega_servico = None
        self.enviado_clientes = None

    def save_carga(self, cargas):
        if self._erro is not None:
            raise self._erro
        self.enviado = cargas
        return self._resultado_save

    def save_entrega_servico(self, entregas):
        if self._erro is not None:
            raise self._erro
        self.enviado_entrega_servico = entregas
        return self._resultado_entrega_servico

    def send_clientes(self, clientes):
        if self._erro is not None:
            raise self._erro
        self.enviado_clientes = clientes
        return self._resultado_clientes

    def consultar_veiculo(self, placa_ou_id):
        if self._erro is not None:
            raise self._erro
        return None

    def send_veiculos(self, veiculos):
        if self._erro is not None:
            raise self._erro

    def send_motoristas(self, motoristas):
        if self._erro is not None:
            raise self._erro


class FusionPortalClienteFalso:
    """Dublê de integrations.fusion_portal.FusionPortalClient — motorista e
    veículo não têm mais tabela local, são lidos ao vivo do Fusion."""

    def __init__(
        self,
        motoristas: dict | None = None,
        erro=None,
        clientes_cadastrados: set | None = None,
        veiculos: dict | None = None,
    ):
        self._motoristas = motoristas or {}
        self._erro = erro
        self._clientes_cadastrados = clientes_cadastrados or set()
        self._veiculos = veiculos or {}

    def motorista_por_codigo_erp(self, codigo_erp):
        if self._erro is not None:
            raise self._erro
        return self._motoristas.get(codigo_erp)

    def buscar_motoristas(self, termo, *, rows=20):
        if self._erro is not None:
            raise self._erro
        termo_up = termo.upper()
        return [m for m in self._motoristas.values() if termo_up in m["nome"].upper()]

    def cliente_cadastrado(self, codigo_erp):
        if self._erro is not None:
            raise self._erro
        return codigo_erp in self._clientes_cadastrados

    def veiculo_por_placa(self, placa):
        if self._erro is not None:
            raise self._erro
        placa_norm = placa.upper().replace("-", "").replace(" ", "")
        for veiculo in self._veiculos.values():
            if veiculo["placa"].upper().replace("-", "").replace(" ", "") == placa_norm:
                return veiculo
        return None

    def veiculo_por_codigo_erp(self, codigo_erp):
        if self._erro is not None:
            raise self._erro
        return self._veiculos.get(codigo_erp)

    def buscar_veiculos(self, termo, *, rows=250):
        if self._erro is not None:
            raise self._erro
        termo_up = termo.upper()
        return [
            v for v in self._veiculos.values()
            if termo_up in v["placa"].upper() or termo_up in (v.get("descricao") or "").upper()
        ]


def _motorista_fusion(codigo_erp: str, nome: str = "Fulano") -> dict:
    return {
        "codigo_erp": codigo_erp, "nome": nome, "cpf": "", "telefone": "",
        "endereco": "", "uf": "", "cidade": "", "cep": "",
    }


def _veiculo_fusion(codigo_erp: str, placa: str, descricao: str = "WOLKS") -> dict:
    return {"codigo_erp": codigo_erp, "placa": placa, "descricao": descricao, "modelo": None, "ano_modelo": None, "ano_fabricacao": None}


DETALHE = {
    "IDEMPRESA": 3, "IDPLANILHA": 22099141, "UF": "PE", "DESCRCIDADE": "RECIFE",
    "OBSERVACAO": None, "DTHORASAIDA": None, "IDMOTORISTA": None, "PLACATRANSP": None,
}

ITEM = {
    "NUMSEQUENCIA": 1,
    "IDPLANILHADOCORI": 22083047, "IDORCAMENTO": 612442, "IDSUBPRODUTO": 141624, "NUMNOTA": 165858,
    "SERIENOTA": "2", "IDCLIFOR": 26623, "IDCLIFORENTREGA": 26623,
    "VALTOTLIQUIDO": 730.01, "PESOBRUTO": 0.5,
}

ITEM2 = {
    "NUMSEQUENCIA": 2,
    "IDPLANILHADOCORI": 22083048, "IDORCAMENTO": 612443, "IDSUBPRODUTO": 141625, "NUMNOTA": 165859,
    "SERIENOTA": "2", "IDCLIFOR": 26623, "IDCLIFORENTREGA": 26623,
    "VALTOTLIQUIDO": 50.0, "PESOBRUTO": 1.0,
}


# --- MOSTRAR_CARGAS_CISS desligada (padrão) ---------------------------------


def _desligar_cargas_ciss(app, config):
    app.config["APP_CONFIG"] = dataclasses.replace(config, mostrar_cargas_ciss=False)


def test_mostrar_cargas_ciss_desligada_bloqueia_buscar_cargas(client, app, config):
    usuario_com_a_tool(client, config)
    _desligar_cargas_ciss(app, config)

    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert resp.status_code == 403


def test_mostrar_cargas_ciss_desligada_bloqueia_ver_carga(client, app, config):
    usuario_com_a_tool(client, config)
    _desligar_cargas_ciss(app, config)

    resp = client.get(f"{PREFIXO}/cargas/3/22099141")

    assert resp.status_code == 403


def test_mostrar_cargas_ciss_desligada_bloqueia_enviar_carga(client, app, config):
    usuario_com_a_tool(client, config)
    _desligar_cargas_ciss(app, config)

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/enviar", json={})

    assert resp.status_code == 403


def test_mostrar_cargas_ciss_desligada_bloqueia_marcar_integrada(client, app, config):
    usuario_com_a_tool(client, config)
    _desligar_cargas_ciss(app, config)

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/marcar-integrada")

    assert resp.status_code == 403


def test_mostrar_cargas_ciss_desligada_bloqueia_busca_documento(client, app, config):
    usuario_com_a_tool(client, config)
    _desligar_cargas_ciss(app, config)

    resp = client.get(f"{PREFIXO}/cargas/busca-documento?termo=1_22144395")

    assert resp.status_code == 403


def test_mostrar_cargas_ciss_desligada_bloqueia_fila(client, app, config):
    usuario_com_a_tool(client, config)
    _desligar_cargas_ciss(app, config)

    resp = client.get(f"{PREFIXO}/fila")

    assert resp.status_code == 403


def test_mostrar_cargas_ciss_desligada_nao_afeta_pre_cargas(client, app, config):
    usuario_com_a_tool(client, config)
    _desligar_cargas_ciss(app, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(pre_cargas=[])

    resp = client.get(
        f"{PREFIXO}/pre-cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00"
    )

    assert resp.status_code == 200


# --- SOMENTE_PEDIDOS_SOLTOS ligada (padrão) ---------------------------------


def _ligar_somente_pedidos_soltos(app, config):
    app.config["APP_CONFIG"] = dataclasses.replace(config, somente_pedidos_soltos=True)


def test_somente_pedidos_soltos_bloqueia_enviar_carga(client, app, config):
    usuario_com_a_tool(client, config)
    _ligar_somente_pedidos_soltos(app, config)

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/enviar", json={})

    assert resp.status_code == 403


def test_somente_pedidos_soltos_bloqueia_enviar_pre_carga(client, app, config):
    usuario_com_a_tool(client, config)
    _ligar_somente_pedidos_soltos(app, config)

    resp = client.post(f"{PREFIXO}/pre-cargas/3/796109/enviar", json={})

    assert resp.status_code == 403


def test_somente_pedidos_soltos_nao_afeta_enviar_pre_carga_solta(client, app, config):
    usuario_com_a_tool(client, config)
    _ligar_somente_pedidos_soltos(app, config)
    # Sem CISS configurado: o que importa é não ser 403 (bloqueado pela
    # flag) — a rota segue até a checagem de CISS normalmente.
    resp = client.post(f"{PREFIXO}/pre-cargas/3/796109/enviar-solta", json={})

    assert resp.status_code != 403
    assert resp.status_code == 503


# --- /cargas ---------------------------------------------------------------


def test_buscar_cargas_sem_sessao_devolve_401(client):
    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")
    assert resp.status_code == 401


def test_buscar_cargas_sem_periodo_devolve_400(client, config):
    usuario_com_a_tool(client, config)

    resp = client.get(f"{PREFIXO}/cargas")

    assert resp.status_code == 400


def test_buscar_cargas_sem_ciss_configurado_devolve_503(client, config):
    usuario_com_a_tool(client, config)

    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert resp.status_code == 503


def test_buscar_cargas_devolve_a_lista(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(cargas=[{"IDEMPRESA": 1, "IDPLANILHA": 22099141}])

    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert resp.status_code == 200
    assert resp.get_json()["cargas"] == [{"IDEMPRESA": 1, "IDPLANILHA": 22099141}]


def test_buscar_cargas_omite_carga_sem_pedido_resolvido(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        cargas=[{"IDEMPRESA": 1, "IDPLANILHA": 22099141}, {"IDEMPRESA": 1, "IDPLANILHA": 22099999}],
        planilhas_resolvidas={(1, 22099141)},
    )

    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert resp.status_code == 200
    assert resp.get_json()["cargas"] == [{"IDEMPRESA": 1, "IDPLANILHA": 22099141}]


def test_buscar_cargas_oculta_carga_ja_enviada_por_padrao(client, app, config):
    from cargas_fusion import service

    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        cargas=[{"IDEMPRESA": 1, "IDPLANILHA": 22099141}, {"IDEMPRESA": 1, "IDPLANILHA": 22099999}]
    )
    conn = connect(config.database_path)
    service.marcar_integrada_manualmente(conn, 1, 22099141)
    conn.close()

    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert resp.status_code == 200
    assert resp.get_json()["cargas"] == [{"IDEMPRESA": 1, "IDPLANILHA": 22099999}]


def test_buscar_cargas_mostra_ja_enviadas_com_ocultar_enviadas_0(client, app, config):
    from cargas_fusion import service

    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        cargas=[{"IDEMPRESA": 1, "IDPLANILHA": 22099141}, {"IDEMPRESA": 1, "IDPLANILHA": 22099999}]
    )
    conn = connect(config.database_path)
    service.marcar_integrada_manualmente(conn, 1, 22099141)
    conn.close()

    resp = client.get(
        f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00&ocultar_enviadas=0"
    )

    assert resp.status_code == 200
    cargas = resp.get_json()["cargas"]
    assert {"IDEMPRESA": 1, "IDPLANILHA": 22099141} in cargas
    assert {"IDEMPRESA": 1, "IDPLANILHA": 22099999} in cargas


def test_buscar_cargas_marca_automaticamente_carga_totalmente_coberta_por_pre_carga(client, app, config):
    """Uma carga cujo único pedido já foi enviado ao Fusion como pré-carga
    não tem nada de novo pra decidir — a listagem marca ENVIADA direto
    (sem chamar o Fusion) e some da lista padrão, sem precisar que
    alguém abra a carga e clique "enviar" manualmente."""
    from cargas_fusion import service

    usuario_com_a_tool(client, config)
    item = {
        "IDPLANILHADOCORI": 22083047,
        "IDORCAMENTO": 612442,
        "IDSUBPRODUTO": 141624,
        "NUMNOTA": 165858,
        "SERIENOTA": "2",
        "IDCLIFOR": 26623,
        "IDCLIFORENTREGA": 26623,
        "VALTOTLIQUIDO": 730.01,
        "PESOBRUTO": 0.5,
        "DESCRCOMPRODUTO": "ITEM",
    }
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        cargas=[{"IDEMPRESA": 1, "IDPLANILHA": 22099141}],
        itens=[item],
        origem_pre_cargas={22083047: {99}},
    )
    conn = connect(config.database_path)
    service._marcar_enviada_na_fila_pre_carga(conn, 1, 99, "PC_1_99", None, None, None, None)
    conn.close()

    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert resp.status_code == 200
    assert resp.get_json()["cargas"] == []

    conn = connect(config.database_path)
    assert service.cargas_ja_enviadas(conn) == {(1, 22099141)}
    conn.close()


def test_buscar_cargas_nao_marca_carga_parcialmente_coberta(client, app, config):
    """Mesma carga, mas com um segundo pedido que não veio de pré-carga
    nenhuma — ainda há algo de novo pra decidir, então a listagem não
    marca nada automaticamente e a carga continua pendente."""
    from cargas_fusion import service

    usuario_com_a_tool(client, config)
    item_coberto = {
        "IDPLANILHADOCORI": 22083047,
        "IDORCAMENTO": 612442,
        "IDSUBPRODUTO": 141624,
        "NUMNOTA": 165858,
        "SERIENOTA": "2",
        "IDCLIFOR": 26623,
        "IDCLIFORENTREGA": 26623,
        "VALTOTLIQUIDO": 730.01,
        "PESOBRUTO": 0.5,
        "DESCRCOMPRODUTO": "ITEM COBERTO",
    }
    item_novo = {**item_coberto, "IDPLANILHADOCORI": 999997, "IDORCAMENTO": 888888}
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        cargas=[{"IDEMPRESA": 1, "IDPLANILHA": 22099141}],
        itens=[item_coberto, item_novo],
        origem_pre_cargas={22083047: {99}},
    )
    conn = connect(config.database_path)
    service._marcar_enviada_na_fila_pre_carga(conn, 1, 99, "PC_1_99", None, None, None, None)
    conn.close()

    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert resp.status_code == 200
    assert resp.get_json()["cargas"] == [{"IDEMPRESA": 1, "IDPLANILHA": 22099141}]

    conn = connect(config.database_path)
    assert service.cargas_ja_enviadas(conn) == set()
    conn.close()


def test_buscar_cargas_nao_confunde_mesmo_idplanilha_de_empresas_diferentes(client, app, config):
    """Achado ao vivo: 9_22104238 e 2_22104238 coexistem (mesmo IDPLANILHA,
    empresas diferentes). Resolver só pelo IDPLANILHA faria a carga vazia
    da empresa 9 aparecer "de carona" no pedido resolvido da empresa 2."""
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        cargas=[{"IDEMPRESA": 9, "IDPLANILHA": 22104238}, {"IDEMPRESA": 2, "IDPLANILHA": 22104238}],
        planilhas_resolvidas={(2, 22104238)},
    )

    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert resp.status_code == 200
    assert resp.get_json()["cargas"] == [{"IDEMPRESA": 2, "IDPLANILHA": 22104238}]


def test_buscar_cargas_sem_id_empresa_passa_none(client, app, config):
    usuario_com_a_tool(client, config)
    fusion_ciss = CissClientFalso(cargas=[])
    app.config["CISS_DB_CLIENT"] = fusion_ciss

    client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert fusion_ciss.id_empresa_recebido is None


def test_buscar_cargas_filtra_por_id_empresa(client, app, config):
    usuario_com_a_tool(client, config)
    fusion_ciss = CissClientFalso(cargas=[])
    app.config["CISS_DB_CLIENT"] = fusion_ciss

    client.get(
        f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00&id_empresa=3"
    )

    assert fusion_ciss.id_empresa_recebido == 3


def test_listar_empresas_sem_sessao_devolve_401(client):
    resp = client.get(f"{PREFIXO}/empresas")
    assert resp.status_code == 401


def test_listar_empresas_devolve_a_lista(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(empresas=[{"id_empresa": 1, "nome": "Matriz"}])

    resp = client.get(f"{PREFIXO}/empresas")

    assert resp.status_code == 200
    assert resp.get_json()["empresas"] == [{"id_empresa": 1, "nome": "Matriz"}]


def test_listar_empresas_sem_ciss_configurado_devolve_503(client, config):
    usuario_com_a_tool(client, config)

    resp = client.get(f"{PREFIXO}/empresas")

    assert resp.status_code == 503


def test_buscar_cargas_ciss_indisponivel_devolve_503(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(erro=CissDbUnavailable())

    resp = client.get(f"{PREFIXO}/cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-14T00:00:00")

    assert resp.status_code == 503


def test_ver_carga_inexistente_devolve_404(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=None)

    resp = client.get(f"{PREFIXO}/cargas/3/22099141")

    assert resp.status_code == 404


def test_ver_carga_monta_o_payload_com_avisos(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE, itens=[ITEM], clientes={26623: None})

    resp = client.get(f"{PREFIXO}/cargas/3/22099141")

    assert resp.status_code == 200
    corpo = resp.get_json()
    assert corpo["payload"]["cargas"][0]["codigo_erp"] == "3_22099141"
    assert any("motorista" in a for a in corpo["avisos"])


def test_ver_carga_devolve_os_itens_e_as_sugestoes(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe=DETALHE, itens=[ITEM, ITEM2], clientes={26623: None}
    )

    resp = client.get(f"{PREFIXO}/cargas/3/22099141")

    corpo = resp.get_json()
    assert len(corpo["itens"]) == 2
    assert corpo["sugestao_motorista"] is None
    assert corpo["sugestao_veiculo"] is None


def test_ver_carga_usa_motorista_e_veiculo_escolhidos(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(
        {"M-1": _motorista_fusion("M-1")}, veiculos={"V-1": _veiculo_fusion("V-1", "ABC1234")}
    )
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe=DETALHE, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623}}
    )

    resp = client.get(
        f"{PREFIXO}/cargas/3/22099141?motorista_codigo_erp=M-1&veiculo_codigo_erp=V-1"
    )

    corpo = resp.get_json()
    assert corpo["payload"]["cargas"][0]["codmotorista"] == "M-1"
    assert corpo["payload"]["cargas"][0]["codveiculo"] == "V-1"
    assert corpo["avisos"] == []


def test_ver_carga_restringe_aos_itens_selecionados(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe=DETALHE, itens=[ITEM, ITEM2], clientes={26623: None}
    )

    resp = client.get(f"{PREFIXO}/cargas/3/22099141?itens=2")

    corpo = resp.get_json()
    assert len(corpo["itens"]) == 2, "itens continua trazendo todos, pra tela desenhar as caixinhas"
    assert len(corpo["payload"]["entrega"]) == 1
    assert corpo["payload"]["entrega"][0]["pedido_erp"] == "612443_22099141"


def test_ver_carga_sem_nenhum_item_selecionado_avisa(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe=DETALHE, itens=[ITEM], clientes={26623: None}
    )

    resp = client.get(f"{PREFIXO}/cargas/3/22099141?itens=")

    corpo = resp.get_json()
    assert any("Nenhuma entrega selecionada" in a for a in corpo["avisos"])


def test_enviar_carga_usa_selecao_do_corpo(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(
        {"M-1": _motorista_fusion("M-1")}, veiculos={"V-1": _veiculo_fusion("V-1", "ABC1234")}
    )
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe=DETALHE, itens=[ITEM, ITEM2], clientes={26623: {"CODIGO_CLIENTE": 26623}}
    )
    fusion = FusionClienteFalso(resultado_save={"success": "1", "errors": []})
    app.config["FUSION_CLIENT"] = fusion

    resp = client.post(
        f"{PREFIXO}/cargas/3/22099141/enviar",
        json={"motorista_codigo_erp": "M-1", "veiculo_codigo_erp": "V-1", "itens": [1]},
    )

    assert resp.get_json()["status"] == "enviada"
    payload_enviado = fusion.enviado[0]
    assert len(payload_enviado["entrega"]) == 1
    assert payload_enviado["cargas"][0]["codmotorista"] == "M-1"


def _cadastrar_motorista_e_veiculo_da_carga(client, app):
    """DETALHE_COMPLETA tem IDMOTORISTA=63588 e PLACATRANSP='KGJ-6098'.
    Motorista e veículo são lidos ao vivo do Fusion — IDMOTORISTA é o
    próprio codigo_erp do motorista (mesma convenção do cliente), e o
    veículo casa pela placa. Sem isso, a sugestão automática não acha
    nenhum dos dois e `processar_envio` nunca chega a chamar o Fusion."""
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(
        {"63588": _motorista_fusion("63588")}, veiculos={"V-1": _veiculo_fusion("V-1", "KGJ-6098")}
    )


DETALHE_COMPLETA = {**DETALHE, "IDMOTORISTA": 63588, "PLACATRANSP": "KGJ-6098"}


def test_enviar_carga_com_aviso_bloqueante_fica_pendente_sem_chamar_o_fusion(client, app, config):
    """DETALHE/ITEM têm motorista/veículo/cliente ausentes — vira item da
    fila, e o Fusion nunca chega a ser chamado (nem precisa estar configurado)."""
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE, itens=[ITEM], clientes={26623: None})

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/enviar")

    assert resp.status_code == 200
    corpo = resp.get_json()
    assert corpo["status"] == "pendente"
    assert corpo["fila"]["id_empresa"] == 3
    assert corpo["fila"]["id_planilha"] == 22099141

    fila = client.get(f"{PREFIXO}/fila").get_json()["fila"]
    assert len(fila) == 1
    assert fila[0]["status"] == "PENDENTE"


def test_enviar_carga_feliz(client, app, config):
    usuario_com_a_tool(client, config)
    _cadastrar_motorista_e_veiculo_da_carga(client, app)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE_COMPLETA, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623, "RAZAO_CLIENTE": "X"}})
    fusion = FusionClienteFalso(resultado_save={"success": "8814621", "errors": []})
    app.config["FUSION_CLIENT"] = fusion

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/enviar")

    assert resp.status_code == 200
    corpo = resp.get_json()
    assert corpo["status"] == "enviada"
    assert corpo["resultado"] == {"success": "8814621", "errors": []}
    assert fusion.enviado[0]["cargas"][0]["codigo_erp"] == "3_22099141"
    assert client.get(f"{PREFIXO}/fila").get_json()["fila"] == []


def test_enviar_carga_ja_enviada_nao_chama_o_fusion_de_novo(client, app, config):
    usuario_com_a_tool(client, config)
    _cadastrar_motorista_e_veiculo_da_carga(client, app)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE_COMPLETA, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623, "RAZAO_CLIENTE": "X"}})
    fusion = FusionClienteFalso(resultado_save={"success": "8814621", "errors": []})
    app.config["FUSION_CLIENT"] = fusion

    primeiro = client.post(f"{PREFIXO}/cargas/3/22099141/enviar")
    assert primeiro.get_json()["status"] == "enviada"

    fusion.enviado = None
    segundo = client.post(f"{PREFIXO}/cargas/3/22099141/enviar")

    assert segundo.status_code == 200
    assert segundo.get_json()["status"] == "ja_enviada"
    assert fusion.enviado is None


def test_enviar_carga_ja_enviada_com_forcar_reenvia(client, app, config):
    usuario_com_a_tool(client, config)
    _cadastrar_motorista_e_veiculo_da_carga(client, app)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE_COMPLETA, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623, "RAZAO_CLIENTE": "X"}})
    fusion = FusionClienteFalso(resultado_save={"success": "8814621", "errors": []})
    app.config["FUSION_CLIENT"] = fusion

    client.post(f"{PREFIXO}/cargas/3/22099141/enviar")
    fusion.enviado = None
    resp = client.post(f"{PREFIXO}/cargas/3/22099141/enviar", json={"forcar": True})

    assert resp.get_json()["status"] == "enviada"
    assert fusion.enviado is not None


def test_ver_carga_mostra_item_fila_quando_ja_enviada(client, app, config):
    usuario_com_a_tool(client, config)
    _cadastrar_motorista_e_veiculo_da_carga(client, app)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE_COMPLETA, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623, "RAZAO_CLIENTE": "X"}})
    app.config["FUSION_CLIENT"] = FusionClienteFalso(resultado_save={"success": "8814621", "errors": []})
    client.post(f"{PREFIXO}/cargas/3/22099141/enviar")

    resp = client.get(f"{PREFIXO}/cargas/3/22099141")

    assert resp.get_json()["item_fila"]["status"] == "ENVIADA"


def test_ver_carga_sem_envio_previo_nao_tem_item_fila(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE, itens=[ITEM], clientes={26623: None})

    resp = client.get(f"{PREFIXO}/cargas/3/22099141")

    assert resp.get_json()["item_fila"] is None


def test_marcar_carga_integrada_marca_sem_chamar_o_fusion(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE)
    fusion = FusionClienteFalso()
    app.config["FUSION_CLIENT"] = fusion

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/marcar-integrada")

    assert resp.status_code == 200
    corpo = resp.get_json()
    assert corpo["fila"]["status"] == "ENVIADA"
    assert corpo["fila"]["integrada_manualmente"] is True
    assert fusion.enviado is None


def test_marcar_carga_integrada_carga_inexistente_devolve_404(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=None)

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/marcar-integrada")

    assert resp.status_code == 404


def test_marcar_carga_integrada_sem_sessao_devolve_401(client):
    resp = client.post(f"{PREFIXO}/cargas/3/22099141/marcar-integrada")
    assert resp.status_code == 401


def test_marcar_carga_integrada_sem_ciss_configurado_devolve_503(client, config):
    usuario_com_a_tool(client, config)

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/marcar-integrada")

    assert resp.status_code == 503


def test_enviar_carga_depois_de_marcada_integrada_recusa_sem_forcar(client, app, config):
    usuario_com_a_tool(client, config)
    _cadastrar_motorista_e_veiculo_da_carga(client, app)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE_COMPLETA, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623, "RAZAO_CLIENTE": "X"}})
    fusion = FusionClienteFalso(resultado_save={"success": "8814621", "errors": []})
    app.config["FUSION_CLIENT"] = fusion

    client.post(f"{PREFIXO}/cargas/3/22099141/marcar-integrada")
    resp = client.post(f"{PREFIXO}/cargas/3/22099141/enviar")

    assert resp.get_json()["status"] == "ja_enviada"
    assert fusion.enviado is None


def test_enviar_carga_fusion_indisponivel_fica_pendente(client, app, config):
    usuario_com_a_tool(client, config)
    _cadastrar_motorista_e_veiculo_da_carga(client, app)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE_COMPLETA, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623, "RAZAO_CLIENTE": "X"}})
    app.config["FUSION_CLIENT"] = FusionClienteFalso(erro=FusionUnavailable())

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/enviar")

    assert resp.status_code == 200
    corpo = resp.get_json()
    assert corpo["status"] == "pendente"
    assert "indisponível" in corpo["motivo"].lower()


def test_enviar_carga_sem_fusion_configurado_fica_pendente(client, app, config):
    usuario_com_a_tool(client, config)
    _cadastrar_motorista_e_veiculo_da_carga(client, app)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE_COMPLETA, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623, "RAZAO_CLIENTE": "X"}})

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/enviar")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "pendente"


def test_enviar_carga_rejeitada_pelo_fusion_fica_pendente_com_o_motivo(client, app, config):
    """Confirmado em produção: o Fusion pode rejeitar a carga inteira com
    `{'msg': '...'}`, formato diferente de `{'success', 'errors'}`."""
    usuario_com_a_tool(client, config)
    _cadastrar_motorista_e_veiculo_da_carga(client, app)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE_COMPLETA, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623, "RAZAO_CLIENTE": "X"}})
    app.config["FUSION_CLIENT"] = FusionClienteFalso(resultado_save={"msg": "codmotorista () ou cpf () nao encontrado"})

    resp = client.post(f"{PREFIXO}/cargas/3/22099141/enviar")

    corpo = resp.get_json()
    assert corpo["status"] == "pendente"
    assert "nao encontrado" in corpo["motivo"]


def test_remover_da_fila(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe=DETALHE, itens=[ITEM], clientes={26623: None})
    corpo = client.post(f"{PREFIXO}/cargas/3/22099141/enviar").get_json()
    id_fila = corpo["fila"]["id"]

    resp = client.delete(f"{PREFIXO}/fila/{id_fila}")

    assert resp.status_code == 204
    assert client.get(f"{PREFIXO}/fila").get_json()["fila"] == []


def test_remover_da_fila_inexistente_devolve_404(client, config):
    usuario_com_a_tool(client, config)

    resp = client.delete(f"{PREFIXO}/fila/999")

    assert resp.status_code == 404


# --- /cargas/busca-documento -------------------------------------------------


def test_buscar_carga_por_documento_sem_sessao_devolve_401(client):
    resp = client.get(f"{PREFIXO}/cargas/busca-documento?termo=1_22144395")
    assert resp.status_code == 401


def test_buscar_carga_por_documento_sem_termo_devolve_400(client, config):
    usuario_com_a_tool(client, config)

    resp = client.get(f"{PREFIXO}/cargas/busca-documento")

    assert resp.status_code == 400


def test_buscar_carga_por_documento_pelo_codigo(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe={"IDEMPRESA": 1, "IDPLANILHA": 22144395})

    resp = client.get(f"{PREFIXO}/cargas/busca-documento?termo=1_22144395")

    assert resp.status_code == 200
    assert resp.get_json() == {"id_empresa": 1, "id_planilha": 22144395}


def test_buscar_carga_por_documento_pelo_pedido(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(por_pedido=(1, 22144395))

    resp = client.get(f"{PREFIXO}/cargas/busca-documento?termo=616139")

    assert resp.status_code == 200
    assert resp.get_json() == {"id_empresa": 1, "id_planilha": 22144395}


def test_buscar_carga_por_documento_pela_nf(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(por_nota=(1, 22144395))

    resp = client.get(f"{PREFIXO}/cargas/busca-documento?termo=166105-2")

    assert resp.status_code == 200
    assert resp.get_json() == {"id_empresa": 1, "id_planilha": 22144395}


def test_buscar_carga_por_documento_nao_encontrada_devolve_404(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso()

    resp = client.get(f"{PREFIXO}/cargas/busca-documento?termo=999999")

    assert resp.status_code == 404


def test_buscar_carga_por_documento_sem_ciss_configurado_devolve_503(client, config):
    usuario_com_a_tool(client, config)

    resp = client.get(f"{PREFIXO}/cargas/busca-documento?termo=616139")

    assert resp.status_code == 503


# --- /veiculos ---------------------------------------------------------------


def test_buscar_veiculos_sem_termo_devolve_vazio(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(veiculos={"V-1": _veiculo_fusion("V-1", "KGJ6098")})

    resp = client.get(f"{PREFIXO}/veiculos")

    assert resp.status_code == 200
    assert resp.get_json()["veiculos"] == []


def test_buscar_veiculos_feliz(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(veiculos={"V-1": _veiculo_fusion("V-1", "KGJ6098")})

    resp = client.get(f"{PREFIXO}/veiculos?termo=kgj")

    assert resp.status_code == 200
    assert resp.get_json()["veiculos"] == [_veiculo_fusion("V-1", "KGJ6098")]


def test_buscar_veiculos_portal_indisponivel_devolve_503(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(erro=FusionPortalError("fora do ar"))

    resp = client.get(f"{PREFIXO}/veiculos?termo=kgj")

    assert resp.status_code == 503


# --- /motoristas ---------------------------------------------------------------


def test_buscar_motoristas_sem_termo_devolve_vazio(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso({"M-1": _motorista_fusion("M-1")})

    resp = client.get(f"{PREFIXO}/motoristas")

    assert resp.status_code == 200
    assert resp.get_json()["motoristas"] == []


def test_buscar_motoristas_feliz(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso({"M-1": _motorista_fusion("M-1", "Fulano")})

    resp = client.get(f"{PREFIXO}/motoristas?termo=fulano")

    assert resp.status_code == 200
    assert resp.get_json()["motoristas"] == [_motorista_fusion("M-1", "Fulano")]


def test_buscar_motoristas_portal_indisponivel_devolve_503(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(erro=FusionPortalError("fora do ar"))

    resp = client.get(f"{PREFIXO}/motoristas?termo=fulano")

    assert resp.status_code == 503


# --- /ajudantes ---------------------------------------------------------------


def test_criar_ajudante_feliz(client, config):
    usuario_com_a_tool(client, config)

    resp = client.post(f"{PREFIXO}/ajudantes", json={"codigo_erp": "A-1", "nome": "Ciclano"})

    assert resp.status_code == 201
    assert resp.get_json()["ajudante"]["nome"] == "Ciclano"


def test_atualizar_ajudante(client, config):
    usuario_com_a_tool(client, config)
    criado = client.post(f"{PREFIXO}/ajudantes", json={"codigo_erp": "A-1", "nome": "Ciclano"}).get_json()["ajudante"]

    resp = client.put(f"{PREFIXO}/ajudantes/{criado['id']}", json={"nome": "Outro"})

    assert resp.status_code == 200
    assert resp.get_json()["ajudante"]["nome"] == "Outro"


def test_candidatos_ajudante_reusa_a_busca_de_motorista(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(candidatos_motorista=[{"IDCLIFOR": 1}])

    resp = client.get(f"{PREFIXO}/ajudantes/candidatos?termo=silva")

    assert resp.get_json()["candidatos"] == [{"IDCLIFOR": 1}]


# --- pedidos cobertos por pré-carga (não reenviar como carga) --------------
# addendum à spec 2026-10-14-cargas-fusion-design.md


def test_ver_carga_pula_pedido_ja_enviado_como_pre_carga(client, app, config):
    conn = connect(config.database_path)
    with conn:
        conn.execute(
            "INSERT INTO cargas_fusion_precarga_fila (id_empresa, id_precarga, codigo_erp, status) "
            "VALUES (3, 796109, 'PC_3_796109', 'ENVIADA')"
        )
    conn.close()

    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe=DETALHE,
        itens=[ITEM],
        clientes={26623: {"CODIGO_CLIENTE": 26623}},
        origem_pre_cargas={ITEM["IDPLANILHADOCORI"]: {796109}},
    )

    resp = client.get(f"{PREFIXO}/cargas/3/22099141")

    corpo = resp.get_json()
    assert corpo["payload"]["entrega"] == []
    assert any("já enviado ao Fusion como pré-carga" in a for a in corpo["avisos"])


def test_ver_carga_nao_exclui_pedido_de_pre_carga_nao_enviada(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe=DETALHE,
        itens=[ITEM],
        clientes={26623: {"CODIGO_CLIENTE": 26623}},
        origem_pre_cargas={ITEM["IDPLANILHADOCORI"]: {796109}},
    )

    resp = client.get(f"{PREFIXO}/cargas/3/22099141")

    corpo = resp.get_json()
    assert len(corpo["payload"]["entrega"]) == 1


# --- /pre-cargas -------------------------------------------------------

DETALHE_PRE_CARGA = {"IDPRECARGA": 796109, "IDEMPRESA": 3, "DESCRPRECARGA": "RECIFE"}

ITEM_PRE_CARGA = {
    "NUMSEQUENCIA": 1,
    "IDPLANILHADOCORI": 22118405,
    "IDORCAMENTO": 614596,
    "IDSUBPRODUTO": 9397,
    "IDCLIFOR": 26623,
    "IDCLIFORENTREGA": 26623,
    "VALTOTLIQUIDO": 510.0,
    "PESOBRUTO": 0.0,
}


def test_buscar_pre_cargas_sem_sessao_devolve_401(client):
    resp = client.get(f"{PREFIXO}/pre-cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-20T00:00:00")

    assert resp.status_code == 401


def test_buscar_pre_cargas_devolve_a_lista(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(pre_cargas=[DETALHE_PRE_CARGA])

    resp = client.get(f"{PREFIXO}/pre-cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-20T00:00:00")

    assert resp.status_code == 200
    assert resp.get_json()["pre_cargas"] == [DETALHE_PRE_CARGA]


def test_buscar_pre_cargas_oculta_ja_enviada_por_padrao(client, app, config):
    conn = connect(config.database_path)
    with conn:
        conn.execute(
            "INSERT INTO cargas_fusion_precarga_fila (id_empresa, id_precarga, codigo_erp, status) "
            "VALUES (3, 796109, 'PC_3_796109', 'ENVIADA')"
        )
    conn.close()

    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(pre_cargas=[DETALHE_PRE_CARGA])

    resp = client.get(f"{PREFIXO}/pre-cargas?data_inicial=2026-10-01T00:00:00&data_final=2026-10-20T00:00:00")

    assert resp.get_json()["pre_cargas"] == []


def test_ver_pre_carga_inexistente_devolve_404(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(detalhe_pre_carga=None)

    resp = client.get(f"{PREFIXO}/pre-cargas/3/796109")

    assert resp.status_code == 404


def test_ver_pre_carga_sem_motorista_e_veiculo_avisa(client, app, config):
    """Sem sugestão automática — motorista_id/veiculo_id ausentes na query
    deixam o aviso bloqueante, igual a uma carga real sem cadastro."""
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe_pre_carga=DETALHE_PRE_CARGA,
        itens_pre_carga=[ITEM_PRE_CARGA],
        clientes={26623: {"CODIGO_CLIENTE": 26623}},
    )

    resp = client.get(f"{PREFIXO}/pre-cargas/3/796109")

    corpo = resp.get_json()
    assert corpo["payload"]["cargas"][0]["codigo_erp"] == "PC_3_796109"
    assert any("motorista" in a for a in corpo["avisos"])
    assert any("veículo" in a for a in corpo["avisos"])


def test_enviar_pre_carga_sem_motorista_e_veiculo_fica_pendente(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe_pre_carga=DETALHE_PRE_CARGA,
        itens_pre_carga=[ITEM_PRE_CARGA],
        clientes={26623: {"CODIGO_CLIENTE": 26623}},
    )

    resp = client.post(f"{PREFIXO}/pre-cargas/3/796109/enviar")

    assert resp.status_code == 200
    corpo = resp.get_json()
    assert corpo["status"] == "pendente"

    fila = client.get(f"{PREFIXO}/pre-cargas/fila").get_json()["fila"]
    assert len(fila) == 1
    assert fila[0]["id_precarga"] == 796109


def test_enviar_pre_carga_com_motorista_e_veiculo_feliz(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(
        {"M-1": _motorista_fusion("M-1")}, veiculos={"V-1": _veiculo_fusion("V-1", "ABC1234")}
    )
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe_pre_carga=DETALHE_PRE_CARGA,
        itens_pre_carga=[ITEM_PRE_CARGA],
        clientes={26623: {"CODIGO_CLIENTE": 26623}},
    )
    fusion = FusionClienteFalso(resultado_save={"success": "1", "errors": []})
    app.config["FUSION_CLIENT"] = fusion

    resp = client.post(
        f"{PREFIXO}/pre-cargas/3/796109/enviar",
        json={"motorista_codigo_erp": "M-1", "veiculo_codigo_erp": "V-1"},
    )

    assert resp.get_json()["status"] == "enviada"
    payload_enviado = fusion.enviado[0]
    assert payload_enviado["cargas"][0]["codigo_erp"] == "PC_3_796109"
    assert payload_enviado["cargas"][0]["codmotorista"] == "M-1"
    assert payload_enviado["cargas"][0]["codveiculo"] == "V-1"


def test_enviar_pre_carga_ja_enviada_nao_chama_o_fusion_de_novo(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(
        {"M-1": _motorista_fusion("M-1")}, veiculos={"V-1": _veiculo_fusion("V-1", "ABC1234")}
    )
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe_pre_carga=DETALHE_PRE_CARGA,
        itens_pre_carga=[ITEM_PRE_CARGA],
        clientes={26623: {"CODIGO_CLIENTE": 26623}},
    )
    app.config["FUSION_CLIENT"] = FusionClienteFalso(resultado_save={"success": "1", "errors": []})
    client.post(
        f"{PREFIXO}/pre-cargas/3/796109/enviar",
        json={"motorista_codigo_erp": "M-1", "veiculo_codigo_erp": "V-1"},
    )

    segunda = FusionClienteFalso(resultado_save={"success": "2", "errors": []})
    app.config["FUSION_CLIENT"] = segunda
    resp = client.post(
        f"{PREFIXO}/pre-cargas/3/796109/enviar",
        json={"motorista_codigo_erp": "M-1", "veiculo_codigo_erp": "V-1"},
    )

    assert resp.get_json()["status"] == "ja_enviada"
    assert segunda.enviado is None


def test_remover_da_fila_pre_carga(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe_pre_carga=DETALHE_PRE_CARGA, itens_pre_carga=[ITEM_PRE_CARGA], clientes={26623: None}
    )
    client.post(f"{PREFIXO}/pre-cargas/3/796109/enviar")
    id_ = client.get(f"{PREFIXO}/pre-cargas/fila").get_json()["fila"][0]["id"]

    resp = client.delete(f"{PREFIXO}/pre-cargas/fila/{id_}")

    assert resp.status_code == 204
    assert client.get(f"{PREFIXO}/pre-cargas/fila").get_json()["fila"] == []


def test_remover_da_fila_pre_carga_inexistente_devolve_404(client, config):
    usuario_com_a_tool(client, config)

    resp = client.delete(f"{PREFIXO}/pre-cargas/fila/999")

    assert resp.status_code == 404


# --- /pre-cargas/envio-automatico -------------------------------------------


def test_ver_estado_envio_automatico_sem_sessao_devolve_401(client):
    resp = client.get(f"{PREFIXO}/pre-cargas/envio-automatico")

    assert resp.status_code == 401


def test_ver_estado_envio_automatico_devolve_pausado_e_resumo_multi_pedido(client, config):
    usuario_com_a_tool(client, config)

    resp = client.get(f"{PREFIXO}/pre-cargas/envio-automatico")

    assert resp.status_code == 200
    corpo = resp.get_json()
    assert corpo["pausado"] is False
    assert corpo["multi_pedido_ignorado"] == {"total": 0, "por_empresa": []}


def test_pausar_e_retomar_envio_automatico(client, config):
    usuario_com_a_tool(client, config)

    resp = client.post(f"{PREFIXO}/pre-cargas/envio-automatico/pausar")
    assert resp.status_code == 200
    assert resp.get_json()["pausado"] is True

    resp = client.post(f"{PREFIXO}/pre-cargas/envio-automatico/retomar")
    assert resp.status_code == 200
    assert resp.get_json()["pausado"] is False


def test_pausar_envio_automatico_sem_sessao_devolve_401(client):
    resp = client.post(f"{PREFIXO}/pre-cargas/envio-automatico/pausar")

    assert resp.status_code == 401


def test_executar_ciclo_agora_sem_sessao_devolve_401(client):
    resp = client.post(f"{PREFIXO}/pre-cargas/envio-automatico/executar-agora")

    assert resp.status_code == 401


def test_executar_ciclo_agora_devolve_409_quando_ciclo_automatico_desligado(client, config):
    """`AUTO_ENVIO_PRE_CARGA_ATIVO=false` (default de `config` nos testes
    daqui) nunca cria `AUTO_ENVIO_REDIS_CLIENT` em `create_app` — sem
    lock, não tem como rodar o ciclo com segurança contra os workers do
    gunicorn, então a rota recusa em vez de tentar sem lock."""
    usuario_com_a_tool(client, config)

    resp = client.post(f"{PREFIXO}/pre-cargas/envio-automatico/executar-agora")

    assert resp.status_code == 409


def test_executar_ciclo_agora_roda_o_ciclo_de_verdade_quando_ligado(client, app, config):
    import fakeredis

    usuario_com_a_tool(client, config)
    app.config["AUTO_ENVIO_REDIS_CLIENT"] = fakeredis.FakeRedis()

    resp = client.post(f"{PREFIXO}/pre-cargas/envio-automatico/executar-agora")

    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ciss_nao_configurado"  # `config.ciss_db` é None neste fixture


# --- sincronização de cliente via sendClientes (addendum 2026-10-20) -------


def test_enviar_carga_sincroniza_cliente_antes_de_enviar(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(
        {"M-1": _motorista_fusion("M-1")}, veiculos={"V-1": _veiculo_fusion("V-1", "ABC1234")}
    )
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe=DETALHE, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623}}
    )
    fusion = FusionClienteFalso(resultado_save={"success": "1", "errors": []})
    app.config["FUSION_CLIENT"] = fusion

    resp = client.post(
        f"{PREFIXO}/cargas/3/22099141/enviar",
        json={"motorista_codigo_erp": "M-1", "veiculo_codigo_erp": "V-1"},
    )

    assert resp.get_json()["status"] == "enviada"
    assert fusion.enviado_clientes[0]["codigo_cliente"] == "26623"


def test_enviar_pre_carga_sincroniza_cliente_antes_de_enviar(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(
        {"M-1": _motorista_fusion("M-1")}, veiculos={"V-1": _veiculo_fusion("V-1", "ABC1234")}
    )
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe_pre_carga=DETALHE_PRE_CARGA,
        itens_pre_carga=[ITEM_PRE_CARGA],
        clientes={26623: {"CODIGO_CLIENTE": 26623}},
    )
    fusion = FusionClienteFalso(resultado_save={"success": "1", "errors": []})
    app.config["FUSION_CLIENT"] = fusion

    resp = client.post(
        f"{PREFIXO}/pre-cargas/3/796109/enviar",
        json={"motorista_codigo_erp": "M-1", "veiculo_codigo_erp": "V-1"},
    )

    assert resp.get_json()["status"] == "enviada"
    assert fusion.enviado_clientes[0]["codigo_cliente"] == "26623"


def test_enviar_carga_cliente_nao_sincronizado_fica_pendente(client, app, config):
    usuario_com_a_tool(client, config)
    app.config["FUSION_PORTAL_CLIENT"] = FusionPortalClienteFalso(
        {"M-1": _motorista_fusion("M-1")}, veiculos={"V-1": _veiculo_fusion("V-1", "ABC1234")}
    )
    app.config["CISS_DB_CLIENT"] = CissClientFalso(
        detalhe=DETALHE, itens=[ITEM], clientes={26623: {"CODIGO_CLIENTE": 26623}}
    )
    fusion = FusionClienteFalso(resultado_clientes={"msg": "Cliente invalido"})
    app.config["FUSION_CLIENT"] = fusion

    resp = client.post(
        f"{PREFIXO}/cargas/3/22099141/enviar",
        json={"motorista_codigo_erp": "M-1", "veiculo_codigo_erp": "V-1"},
    )

    corpo = resp.get_json()
    assert corpo["status"] == "pendente"
    assert "Cliente invalido" in corpo["motivo"]
    assert fusion.enviado is None
