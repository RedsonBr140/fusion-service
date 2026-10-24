"""Testes de cargas_fusion/service.py (porte de meta-natal) — cadastro local (SQLite real,
via database.init_database) e montagem do payload do saveCarga. O Fusion é
sempre um dublê: nenhum teste aqui toca a rede.
"""

import re
from datetime import datetime, timedelta, timezone

import pytest

from database import connect, init_database
from integrations.fusion import FusionUnavailable
from integrations.fusion_portal import FusionPortalError
from cargas_fusion import service


class FusionPortalClienteFalso:
    """Dublê de integrations.fusion_portal.FusionPortalClient — motorista e
    veículo não têm mais tabela local, são lidos ao vivo do Fusion."""

    def __init__(self, motoristas: dict | None = None, veiculos: dict | None = None, erro=None):
        self._motoristas = motoristas or {}
        self._veiculos = veiculos or {}
        self._erro = erro

    def motorista_por_codigo_erp(self, codigo_erp):
        if self._erro is not None:
            raise self._erro
        return self._motoristas.get(codigo_erp)

    def buscar_motoristas(self, termo, *, rows=20):
        if self._erro is not None:
            raise self._erro
        termo_up = termo.upper()
        return [m for m in self._motoristas.values() if termo_up in m["nome"].upper()]

    def veiculo_por_placa(self, placa):
        if self._erro is not None:
            raise self._erro
        placa_norm = re.sub(r"[^A-Z0-9]", "", placa.upper())
        for veiculo in self._veiculos.values():
            if re.sub(r"[^A-Z0-9]", "", veiculo["placa"].upper()) == placa_norm:
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


@pytest.fixture
def conn(tmp_path):
    db_path = tmp_path / "toolkit.db"
    init_database(db_path)
    conexao = connect(db_path)
    yield conexao
    conexao.close()


# --- veículo (lido ao vivo do Fusion) --------------------------------------


def test_veiculo_sugerido_casa_pela_placa():
    veiculo = {"codigo_erp": "7", "placa": "KGJ6098", "descricao": "WOLKS"}
    portal = FusionPortalClienteFalso(veiculos={"7": veiculo})

    assert service.veiculo_sugerido(portal, "KGJ-6098") == veiculo
    assert service.veiculo_sugerido(portal, "nao-existe") is None
    assert service.veiculo_sugerido(portal, None) is None
    assert service.veiculo_sugerido(None, "KGJ-6098") is None


def test_veiculo_sugerido_falha_no_portal_devolve_none_sem_subir():
    portal = FusionPortalClienteFalso(erro=FusionPortalError("fora do ar"))

    assert service.veiculo_sugerido(portal, "KGJ-6098") is None


def test_veiculo_por_codigo_erp():
    veiculo = {"codigo_erp": "7", "placa": "KGJ6098", "descricao": "WOLKS"}
    portal = FusionPortalClienteFalso(veiculos={"7": veiculo})

    assert service.veiculo_por_codigo_erp(portal, "7") == veiculo
    assert service.veiculo_por_codigo_erp(portal, "nao-existe") is None
    assert service.veiculo_por_codigo_erp(portal, None) is None
    assert service.veiculo_por_codigo_erp(None, "7") is None


def test_veiculo_por_codigo_erp_falha_no_portal_devolve_none_sem_subir():
    portal = FusionPortalClienteFalso(erro=FusionPortalError("fora do ar"))

    assert service.veiculo_por_codigo_erp(portal, "7") is None


def test_buscar_veiculos_fusion_busca_por_placa_ou_descricao():
    veiculo = {"codigo_erp": "7", "placa": "KGJ6098", "descricao": "WOLKS 15-190"}
    portal = FusionPortalClienteFalso(veiculos={"7": veiculo})

    assert service.buscar_veiculos_fusion(portal, "kgj6098") == [veiculo]
    assert service.buscar_veiculos_fusion(portal, "wolks") == [veiculo]
    assert service.buscar_veiculos_fusion(portal, "") == []
    assert service.buscar_veiculos_fusion(None, "wolks") == []


def test_buscar_veiculos_fusion_propaga_erro_do_portal():
    """Diferente de veiculo_sugerido/veiculo_por_codigo_erp: aqui é uma
    busca explícita de quem está montando o envio, não deve falhar em
    silêncio."""
    portal = FusionPortalClienteFalso(erro=FusionPortalError("fora do ar"))

    with pytest.raises(FusionPortalError):
        service.buscar_veiculos_fusion(portal, "wolks")


def test_veiculo_local_para_envio_fusion_tem_o_shape_do_savecarga():
    linha = {
        "codigo_erp": "7", "placa": "KGJ6098", "descricao": "WOLKS", "modelo": "15-190",
        "ano_modelo": 2013, "ano_fabricacao": 2013, "peso_max_entregas": 7000.0,
        "volume_max_entregas": 8.19, "qtd_pallets_veiculo": 10.0,
    }

    payload = service.veiculo_local_para_envio_fusion(linha)

    assert payload["codigo_erp"] == "7"
    assert payload["anoModelo"] == "2013"
    assert payload["anoFabricacao"] == "2013"
    assert payload["peso_max_entregas"] == "7000.000"


def test_veiculo_local_para_envio_fusion_sem_capacidade_nunca_manda_zero_cru():
    """Carga 1_22142248 em produção: mandar peso_max_entregas/
    volume_max_entregas/qtd_pallets_veiculo como número 0 (sem fonte no
    portal) fez o Fusion rejeitar o saveCarga inteiro como array
    malformado — mesma quirk do PHP empty() tratando zero numérico como
    "não informado"."""
    linha = {"codigo_erp": "25", "placa": "NWY2177"}

    payload = service.veiculo_local_para_envio_fusion(linha)

    assert payload["peso_max_entregas"] == "0.000"
    assert payload["volume_max_entregas"] == "0.000"
    assert payload["qtd_pallets_veiculo"] == "0.000"


# --- motorista (lido ao vivo do Fusion, 2026-10-20) -------------------------


def test_motorista_sugerido_casa_pelo_codigo_erp_do_idmotorista():
    """IDMOTORISTA do CISS é o IDCLIFOR, e por convenção (mesma do
    cliente) é também o codigo_erp com que o motorista foi cadastrado no
    Fusion."""
    motorista = {"codigo_erp": "63588", "nome": "Fulano"}
    portal = FusionPortalClienteFalso({"63588": motorista})

    assert service.motorista_sugerido(portal, 63588) == motorista
    assert service.motorista_sugerido(portal, 99999) is None
    assert service.motorista_sugerido(portal, None) is None
    assert service.motorista_sugerido(None, 63588) is None


def test_motorista_sugerido_falha_no_portal_devolve_none_sem_subir():
    portal = FusionPortalClienteFalso(erro=FusionPortalError("fora do ar"))

    assert service.motorista_sugerido(portal, 63588) is None


def test_motorista_por_codigo_erp():
    motorista = {"codigo_erp": "M-1", "nome": "Fulano"}
    portal = FusionPortalClienteFalso({"M-1": motorista})

    assert service.motorista_por_codigo_erp(portal, "M-1") == motorista
    assert service.motorista_por_codigo_erp(portal, "nao-existe") is None
    assert service.motorista_por_codigo_erp(portal, None) is None
    assert service.motorista_por_codigo_erp(None, "M-1") is None


def test_motorista_por_codigo_erp_falha_no_portal_devolve_none_sem_subir():
    portal = FusionPortalClienteFalso(erro=FusionPortalError("fora do ar"))

    assert service.motorista_por_codigo_erp(portal, "M-1") is None


def test_buscar_motoristas_fusion_busca_por_nome():
    motorista = {"codigo_erp": "M-1", "nome": "Fulano de Tal"}
    portal = FusionPortalClienteFalso({"M-1": motorista})

    assert service.buscar_motoristas_fusion(portal, "fulano") == [motorista]
    assert service.buscar_motoristas_fusion(portal, "") == []
    assert service.buscar_motoristas_fusion(None, "fulano") == []


def test_buscar_motoristas_fusion_propaga_erro_do_portal():
    """Diferente de motorista_sugerido/motorista_por_codigo_erp: aqui é
    uma busca explícita de quem está montando o envio, não deve falhar
    em silêncio."""
    portal = FusionPortalClienteFalso(erro=FusionPortalError("fora do ar"))

    with pytest.raises(FusionPortalError):
        service.buscar_motoristas_fusion(portal, "fulano")


def test_salvar_ajudante_nao_quebra_sem_as_colunas_de_motorista(conn):
    """fusion_ajudantes não tem cpf/telefone/endereco/uf/cidade/cep — a
    tentativa de inserir essas colunas quebraria com 'no such column'."""
    linha = service.salvar_ajudante(conn, {"codigo_erp": "A-1", "nome": "Ciclano"})

    assert linha["codigo_erp"] == "A-1"
    assert "cpf" not in linha


def test_atualizar_ajudante_ignora_campos_que_nao_existem_na_tabela(conn):
    linha = service.salvar_ajudante(conn, {"codigo_erp": "A-1", "nome": "Ciclano"})

    atualizado = service.atualizar_ajudante(conn, linha["id"], {"nome": "Beltrano", "cpf": "123"})

    assert atualizado["nome"] == "Beltrano"
    assert "cpf" not in atualizado


def test_pessoa_local_para_envio_fusion_marca_o_tipo():
    linha = {"codigo_erp": "M-1", "nome": "Fulano", "cpf": "", "telefone": "", "endereco": "", "uf": "", "cidade": "", "cep": ""}

    payload = service._pessoa_local_para_envio_fusion(linha, "Ajudante")

    assert payload["tipo"] == "Ajudante"
    assert payload["codigo_erp"] == "M-1"


# --- sincronizar ajudante (único cadastro que ainda sincroniza) -----------


class FusionClienteFalso:
    def __init__(self, erro=None):
        self._erro = erro
        self.enviados_motorista = None

    def send_motoristas(self, motoristas):
        if self._erro is not None:
            raise self._erro
        self.enviados_motorista = motoristas


def test_sincronizar_ajudante_marca_o_tipo_ajudante(conn):
    linha = service.salvar_ajudante(conn, {"codigo_erp": "A-1", "nome": "Ciclano"})
    fusion = FusionClienteFalso()

    service.sincronizar_ajudante(conn, fusion, linha["id"])

    assert fusion.enviados_motorista[0]["tipo"] == "Ajudante"


def test_sincronizar_pessoa_inexistente_falha(conn):
    with pytest.raises(service.SincronizacaoFusionError, match="não encontrado"):
        service.sincronizar_ajudante(conn, FusionClienteFalso(), 999)


def test_sincronizar_pessoa_sem_fusion_falha(conn):
    linha = service.salvar_ajudante(conn, {"codigo_erp": "A-1", "nome": "Ciclano"})

    with pytest.raises(service.SincronizacaoFusionError, match="não configurado"):
        service.sincronizar_ajudante(conn, None, linha["id"])


# --- montagem do payload --------------------------------------------------


def test_codigo_carga_e_codigo_pedido():
    assert service.codigo_carga(3, 22099141) == "3_22099141"
    assert service.codigo_pedido(22094297, 22099141) == "22094297_22099141"


class CissClientDocumentoFalso:
    """Dublê mínimo pra localizar_carga_por_documento — só os três
    métodos que ela chama."""

    def __init__(self, carga=None, por_pedido=None, por_nota=None):
        self._carga = carga
        self._por_pedido = por_pedido
        self._por_nota = por_nota

    def get_carga_detalhe(self, id_empresa, id_planilha):
        return self._carga if (id_empresa, id_planilha) == self._carga else None

    def get_carga_por_pedido(self, numero_pedido):
        return self._por_pedido

    def get_carga_por_nota(self, numnota, serienota):
        return self._por_nota


def test_localizar_carga_por_documento_pelo_codigo():
    client = CissClientDocumentoFalso(carga=(1, 22144395))
    assert service.localizar_carga_por_documento(client, "1_22144395") == (1, 22144395)


def test_localizar_carga_por_documento_pelo_codigo_inexistente_devolve_none():
    client = CissClientDocumentoFalso(carga=(1, 22144395))
    assert service.localizar_carga_por_documento(client, "1_999999") is None


def test_localizar_carga_por_documento_pelo_pedido():
    client = CissClientDocumentoFalso(por_pedido=(1, 22144395))
    assert service.localizar_carga_por_documento(client, "616139") == (1, 22144395)


def test_localizar_carga_por_documento_pela_nf():
    client = CissClientDocumentoFalso(por_nota=(1, 22144395))
    assert service.localizar_carga_por_documento(client, "166105-2") == (1, 22144395)


def test_localizar_carga_por_documento_termo_vazio_ou_nao_reconhecido():
    client = CissClientDocumentoFalso()
    assert service.localizar_carga_por_documento(client, "") is None
    assert service.localizar_carga_por_documento(client, "   ") is None
    assert service.localizar_carga_por_documento(client, "abc") is None
    assert service.localizar_carga_por_documento(client, "1_abc") is None


def test_peso_item_usa_peso_de_movimento_quando_nao_zero():
    assert service._peso_item({"PESOBRUTO": 0.5, "PRODUTO_PESOBRUTO": 2.0, "QTDPRODUTO": 6.0}) == 0.5


def test_peso_item_cai_pro_catalogo_vezes_qtd_quando_movimento_e_zero():
    """Achado em produção (carga 1_22142248): ESTOQUE_ANALITICO.PESOBRUTO
    (o movimento) vem 0 em ~96% das linhas desta base — não é "produto
    sem peso cadastrado" como a spec original assumiu. PRODUTO_GRADE
    .PESOBRUTO é por unidade da grade, por isso × QTDPRODUTO — mesmo
    tratamento que LARGURA/ALTURA/COMPRIMENTO (mesma tabela) já recebem
    no cálculo de cubagem."""
    item = {"PESOBRUTO": 0.0, "PRODUTO_PESOBRUTO": 2.0, "QTDPRODUTO": 6.0}
    assert service._peso_item(item) == 12.0


def test_peso_item_sem_fonte_nenhuma_e_zero():
    assert service._peso_item({"PESOBRUTO": None, "PRODUTO_PESOBRUTO": None, "QTDPRODUTO": 4.0}) == 0


def test_preco_item_usa_valunitbruto_quando_disponivel():
    """Achado em produção (carga 1_22142248): VALUNITBRUTO (preço de
    catálogo, bruto) é consistentemente maior que VALTOTLIQUIDO/QTDPRODUTO
    (a média líquida que o cálculo antigo usava) — a diferença é o
    desconto aplicado na venda, não um erro."""
    item = {"VALUNITBRUTO": 20.99, "VALTOTLIQUIDO": 74.0, "QTDPRODUTO": 4.0}
    assert service._preco_item(item) == 20.99


def test_preco_item_cai_pro_calculo_antigo_sem_valunitbruto():
    """VALUNITBRUTO vem NULL/zero numa fração pequena das linhas (mesma
    taxa de ausência de VALTOTLIQUIDO, ~0.45% — confirmado contra
    ESTOQUE_ANALITICO de produção) — cai pro cálculo antigo em vez de
    mandar preço zerado."""
    item = {"VALUNITBRUTO": 0.0, "VALTOTLIQUIDO": 74.0, "QTDPRODUTO": 4.0}
    assert service._preco_item(item) == 18.5

    item_sem_coluna = {"VALTOTLIQUIDO": 74.0, "QTDPRODUTO": 4.0}
    assert service._preco_item(item_sem_coluna) == 18.5


DETALHE = {
    "IDEMPRESA": 3,
    "IDPLANILHA": 22099141,
    "UF": "PE",
    "DESCRCIDADE": "RECIFE",
    "OBSERVACAO": None,
    "DTHORASAIDA": None,
}

ITEM = {
    "IDPLANILHADOCORI": 22083047,
    "IDORCAMENTO": 612442,
    "IDSUBPRODUTO": 141624,
    "NUMNOTA": 165858,
    "SERIENOTA": "2",
    "IDCLIFOR": 26623,
    "IDCLIFORENTREGA": 26623,
    "VALTOTLIQUIDO": 730.01,
    "VALUNITBRUTO": 811.12,
    "PESOBRUTO": 0.5,
    "DESCRCOMPRODUTO": "MINI CHURRASQUEIRA PRE MOLDADO TIJOLINHO COM BALCAO VERMELHA",
}

CLIENTE = {
    "CODIGO_CLIENTE": 26623,
    "RAZAO_CLIENTE": "FULANO",
    "DESCR_CLIENTE": "FULANO",
    "CNPJ_CLIENTE": "12345678900",
    "END_CLIENTE": "RUA X",
    "BAIRRO_CLIENTE": "CENTRO",
    "CIDADE_CLIENTE": "RECIFE",
    "UF_CLIENTE": "PE",
    "CEP_CLIENTE": "50000000",
}

MOTORISTA = {"codigo_erp": "M-1", "nome": "Fulano", "cpf": "", "telefone": "", "endereco": "", "uf": "", "cidade": "", "cep": ""}
VEICULO = {"codigo_erp": "V-1", "placa": "KGJ6098", "descricao": "WOLKS", "modelo": "15-190", "ano_modelo": 2013, "ano_fabricacao": 2013, "peso_max_entregas": 7000.0, "volume_max_entregas": 8.0, "qtd_pallets_veiculo": 10.0}


def test_montar_payload_carga_monta_entrega_e_carga():
    payload_carga, entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [ITEM], {26623: CLIENTE}, MOTORISTA, VEICULO
    )

    assert avisos == []
    assert avisos_bloqueantes == []
    assert payload_carga["cargas"][0]["codigo_erp"] == "3_22099141"
    assert payload_carga["cargas"][0]["codmotorista"] == "M-1"
    assert payload_carga["cargas"][0]["codveiculo"] == "V-1"
    # Único cliente na carga (um só pedido) — nome anexado ao destino.
    assert payload_carga["cargas"][0]["destino"] == "RECIFE/PE - FULANO"

    # entrega[] do saveCarga é um stub mínimo (D10) — não carrega valor/peso/cliente.
    # "612442" é o IDORCAMENTO real (não o IDPLANILHADOCORI=22083047,
    # que é só a pré-nota) — confirmado contra produção (carga 1_22100858).
    assert payload_carga["entrega"][0]["pedido_erp"] == "612442_22099141"
    assert payload_carga["entrega"][0]["codigo_erp_filial_fat"] == "3"
    assert payload_carga["entrega"][0]["codigo_erp_filial_log"] == "3"
    assert payload_carga["entrega"][0]["codigo_erp_filial_digit"] == "3"
    # Confirmado em produção: o Fusion exige filial_padrao/codigo_cliente
    # direto na entrega (não só dentro de cliente{}), e cubagem/peso/
    # podeformarcarga, mesmo sem fonte real de cubagem (cai pra "0.000",
    # nunca "0" puro — ver _numero_informado).
    assert payload_carga["entrega"][0]["filial_padrao"] == "3"
    assert payload_carga["entrega"][0]["codigo_cliente"] == "26623"
    assert payload_carga["entrega"][0]["cubagem"] == "0.000"
    assert payload_carga["entrega"][0]["peso"] == "0.500"
    assert payload_carga["entrega"][0]["podeformarcarga"] == "N"
    assert payload_carga["entrega"][0]["cliente"] == {
        "filial_padrao": "3",
        "codigo_cliente": "26623",
        # "1", nunca "0" — o Fusion trata "0" como vazio (empty() do PHP).
        "cliente_cod_praca_erp": "1",
        "cliente_descricao_praca": ".",
        "cliente_cod_rota_erp": "1",
        "cliente_descricao_rota": ".",
    }
    assert payload_carga["cargas"][0]["entregas_carga"] == [
        {"pedido_erp": "612442_22099141", "codigo_erp_filial": "3", "seq_entrega": "1"}
    ]

    # O detalhe do pedido (valor/peso/itens de produto) vai para saveEntregaServico.
    assert entregas_servico[0]["pedido_erp"] == "612442_22099141"
    assert entregas_servico[0]["status"] == "1"
    assert entregas_servico[0]["ent_ou_serv"] == "Entrega"
    assert entregas_servico[0]["valor"] == "730.010"
    assert entregas_servico[0]["peso"] == "0.500"
    assert entregas_servico[0]["cubagem"] == "0.000"
    assert entregas_servico[0]["podeformarcarga"] == "N"
    assert entregas_servico[0]["codigo_cliente"] == "26623"
    assert entregas_servico[0]["filial_padrao"] == "3"
    assert entregas_servico[0]["empresa_fat"] == "3"
    assert entregas_servico[0]["itens"] == [
        {
            "cod_produto_erp": "141624",
            "qtd": "0.000",
            "peso": "0.500",
            "preco": "811.120",
            "subtotal": "730.010",
            "descricao": "MINI CHURRASQUEIRA PRE MOLDADO TIJOLINHO COM BALCAO VERMELHA",
        }
    ]


def test_montar_payload_carga_ignora_pedido_sem_orcamento_vinculado():
    """Quando get_itens_carga não resolveu IDORCAMENTO (carga sem origem em
    orçamento, ou NF/pedido ainda não gerados), a entrega não entra no
    envio — nem em saveCarga, nem em saveEntregaServico — só o aviso."""
    item_sem_orcamento = {**ITEM, "IDORCAMENTO": None}
    payload_carga, entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [item_sem_orcamento], {26623: CLIENTE}, MOTORISTA, VEICULO
    )

    assert payload_carga["entrega"] == []
    assert entregas_servico == []
    assert any("Pedido (orçamento)" in a for a in avisos)
    # Não bloqueante (D11): sem orçamento vinculado não é um erro que
    # devesse travar o resto da carga — aqui não há resto, mas o aviso
    # não deve aparecer entre os bloqueantes mesmo assim.
    assert avisos_bloqueantes == []


def test_montar_payload_carga_envia_pedidos_com_orcamento_ignorando_os_sem():
    """Uma carga com pedidos mistos (com e sem orçamento) envia só os que
    têm IDORCAMENTO — a seq_entrega não pula número por causa do ignorado."""
    item_sem_orcamento = {
        **ITEM, "IDORCAMENTO": None, "IDPLANILHADOCORI": 999999, "NUMSEQUENCIA": 99,
    }
    payload_carga, entregas_servico, avisos, _avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [item_sem_orcamento, ITEM], {26623: CLIENTE}, MOTORISTA, VEICULO
    )

    assert len(payload_carga["entrega"]) == 1
    assert len(entregas_servico) == 1
    assert payload_carga["entrega"][0]["pedido_erp"] == "612442_22099141"
    assert payload_carga["cargas"][0]["entregas_carga"][0]["seq_entrega"] == "1"
    assert any("Pedido (orçamento)" in a for a in avisos)


def test_montar_payload_carga_agrupa_itens_do_mesmo_pedido_numa_so_entrega():
    item_mesmo_pedido = {**ITEM, "IDSUBPRODUTO": 999, "VALTOTLIQUIDO": 10.0, "PESOBRUTO": 1.0}
    payload_carga, entregas_servico, _, _ = service.montar_payload_carga(
        DETALHE, [ITEM, item_mesmo_pedido], {26623: CLIENTE}, MOTORISTA, VEICULO
    )

    assert len(payload_carga["entrega"]) == 1
    assert len(entregas_servico) == 1
    assert len(entregas_servico[0]["itens"]) == 2
    assert entregas_servico[0]["valor"] == "740.010"
    assert entregas_servico[0]["peso"] == "1.500"


def test_montar_payload_carga_nao_anexa_cliente_ao_destino_com_mais_de_um():
    """Carga com pedidos de clientes diferentes — sem como escolher qual
    nome mostrar, o destino fica só cidade/UF, sem o " - nome"."""
    outro_cliente = {**CLIENTE, "CODIGO_CLIENTE": 99999, "RAZAO_CLIENTE": "OUTRO"}
    item_outro_pedido = {
        **ITEM,
        "IDPLANILHADOCORI": 999998,
        "IDORCAMENTO": 777777,
        "IDCLIFORENTREGA": 99999,
        "IDCLIFOR": 99999,
        "NUMSEQUENCIA": 2,
    }
    payload_carga, _, _, _ = service.montar_payload_carga(
        DETALHE, [ITEM, item_outro_pedido], {26623: CLIENTE, 99999: outro_cliente}, MOTORISTA, VEICULO
    )

    assert payload_carga["cargas"][0]["destino"] == "RECIFE/PE"


def test_montar_payload_carga_calcula_cubagem_real_a_partir_das_dimensoes():
    """cubagem = LARGURA × ALTURA × COMPRIMENTO (cm, catálogo) × QTDPRODUTO,
    somada por pedido e convertida de cm³ pra m³."""
    item_com_dimensoes = {
        **ITEM,
        "LARGURA": 40.0,
        "ALTURA": 60.0,
        "COMPRIMENTO": 12.0,
        "QTDPRODUTO": 2.0,
    }
    payload_carga, _, _, _ = service.montar_payload_carga(
        DETALHE, [item_com_dimensoes], {26623: CLIENTE}, MOTORISTA, VEICULO
    )

    # 40 * 60 * 12 * 2 = 57600 cm3 = 0.0576 m3
    assert payload_carga["entrega"][0]["cubagem"] == "0.058"


def test_montar_payload_carga_avisa_sobre_motorista_e_veiculo_ausentes():
    payload_carga, _, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [ITEM], {26623: CLIENTE}, None, None
    )

    assert any("motorista" in a for a in avisos)
    assert any("veículo" in a for a in avisos)
    assert "motorista" not in payload_carga
    assert "veiculo" not in payload_carga
    # Bloqueante de verdade: há entrega pra mandar, mas falta motorista/veículo.
    assert avisos_bloqueantes == avisos


def test_montar_payload_carga_sem_itens_pra_enviar_nao_cobra_motorista_e_veiculo():
    """Carga cujo único pedido já foi coberto por pré-carga: não há nada
    pra anexar motorista/veículo, então a ausência deles não é um
    problema de verdade — sem essa guarda, uma carga 100% resolvida por
    outro canal ficaria travada pra sempre (importa pro envio automático,
    que não deveria reprocessar isso indefinidamente)."""
    payload_carga, entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [ITEM], {26623: CLIENTE}, None, None, pedidos_excluidos={ITEM["IDPLANILHADOCORI"]}
    )

    assert payload_carga["entrega"] == []
    assert entregas_servico == []
    assert not any("motorista" in a for a in avisos)
    assert not any("veículo" in a for a in avisos)
    assert avisos_bloqueantes == []


def test_montar_payload_carga_avisa_sobre_cliente_nao_encontrado():
    _, _, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [ITEM], {26623: None}, MOTORISTA, VEICULO
    )

    assert any("não encontrado no CISS" in a for a in avisos)
    assert avisos_bloqueantes == avisos


def test_montar_payload_carga_dt_saida_e_sempre_amanha_as_7_ignorando_o_ciss():
    """D3 da spec: dt_saida nunca vem do CISS — regra fixa, mesmo quando
    DTHORASAIDA está preenchido com outra coisa."""
    detalhe_com_dthorasaida = {**DETALHE, "DTHORASAIDA": "2019-01-01 00:00:00"}
    payload_carga, _, _, _ = service.montar_payload_carga(
        detalhe_com_dthorasaida, [ITEM], {26623: CLIENTE}, MOTORISTA, VEICULO
    )

    dt_saida = payload_carga["cargas"][0]["dt_saida"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} 07:00:00", dt_saida)
    # Brasília, não o fuso de quem roda o teste (CI roda em UTC) — mesmo
    # cálculo de _dt_saida_padrao, pra não quebrar por 3h de diferença.
    agora_brasilia = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-3)))
    amanha = (agora_brasilia + timedelta(days=1)).strftime("%Y-%m-%d")
    assert dt_saida.startswith(amanha)


def test_montar_payload_carga_aceita_dt_saida_escolhida_na_tela():
    payload_carga, _, _, _ = service.montar_payload_carga(
        DETALHE, [ITEM], {26623: CLIENTE}, MOTORISTA, VEICULO, dt_saida="2026-11-05 07:00:00"
    )

    assert payload_carga["cargas"][0]["dt_saida"] == "2026-11-05 07:00:00"


def test_normalizar_dt_saida_aceita_formato_do_datetime_local_input():
    assert service.normalizar_dt_saida("2026-11-05T07:00") == "2026-11-05 07:00:00"
    assert service.normalizar_dt_saida("2026-11-05T07:00:00") == "2026-11-05 07:00:00"


def test_normalizar_dt_saida_rejeita_formato_invalido():
    with pytest.raises(service.DtSaidaInvalida):
        service.normalizar_dt_saida("não é uma data")


# --- fila de envio --------------------------------------------------------


def test_resultado_indica_sucesso_com_success_preenchido():
    assert service._resultado_indica_sucesso({"success": "123", "errors": []}) == (True, None)


def test_resultado_indica_sucesso_falha_com_mensagem_msg():
    """Confirmado em produção: o Fusion rejeita a carga inteira com `msg`,
    formato diferente de `{success, errors}`."""
    sucesso, motivo = service._resultado_indica_sucesso({"msg": "codmotorista () nao encontrado"})
    assert sucesso is False
    assert "nao encontrado" in motivo


def test_resultado_indica_sucesso_falha_com_errors_preenchido():
    sucesso, motivo = service._resultado_indica_sucesso({"success": "", "errors": ["item inválido"]})
    assert sucesso is False
    assert "item inválido" in motivo


def test_resultado_indica_sucesso_formato_inesperado():
    sucesso, _ = service._resultado_indica_sucesso({"nada_reconhecivel": True})
    assert sucesso is False


def test_resultado_indica_sucesso_string_crua_e_tratada_como_falha():
    """saveCarga com array_dados malformado devolve uma string crua, nem
    chega a ser JSON — confirmado em produção (spec §7.1, formato 6)."""
    sucesso, motivo = service._resultado_indica_sucesso(
        "O Array de Dados do saveCarga tem que ser um array"
    )
    assert sucesso is False
    assert "Array de Dados" in motivo


def test_resultado_indica_sucesso_com_success_como_lista_e_sucesso():
    """saveEntregaServico real devolve success como array, não string
    (spec §7.1, formato 4)."""
    assert service._resultado_indica_sucesso({"success": ["TESTE_CLAUDE_001"], "errors": []}) == (
        True,
        None,
    )


def test_resultado_indica_sucesso_com_success_lista_vazia_e_falha():
    sucesso, _ = service._resultado_indica_sucesso({"success": [], "errors": []})
    assert sucesso is False


def test_resultado_indica_sucesso_com_erro_detalhes_e_falha():
    """saveEntregaServico rejeitando por validação de praça — formato 3 da
    spec §7.1, sem msg nem errors preenchidos."""
    sucesso, motivo = service._resultado_indica_sucesso(
        {"erro_detalhes": [{"pedido": "22094297_22099141", "descricao": "Praca nao cadastrada"}], "success": []}
    )
    assert sucesso is False
    assert "Praca nao cadastrada" in motivo


def test_resultado_indica_sucesso_com_success_string_qtderros_virgula_id():
    """saveCarga real de sucesso: success é "<qtd_erros>,<id_interno>",
    uma string só (spec §7.1, formato 5)."""
    assert service._resultado_indica_sucesso({"success": "0,809283", "errors": []}) == (True, None)


def test_resultado_indica_sucesso_com_success_bool_false_e_falha():
    """atualizaCarga real: success como booleano (spec §7.1, formato 7)."""
    sucesso, _ = service._resultado_indica_sucesso({"success": False, "errors": ["erro"]})
    assert sucesso is False


class FusionSaveCargaFalso:
    def __init__(self, resultado=None, resultado_entrega_servico=None, resultado_clientes=None, erro=None):
        self._resultado = resultado if resultado is not None else {"success": "1", "errors": []}
        self._resultado_entrega_servico = (
            resultado_entrega_servico
            if resultado_entrega_servico is not None
            else {"success": ["ok"], "errors": []}
        )
        self._resultado_clientes = (
            resultado_clientes if resultado_clientes is not None else {"success": ["ok"], "errors": []}
        )
        self._erro = erro
        self.enviado = None
        self.enviado_entrega_servico = None
        self.enviado_clientes = None

    def save_carga(self, cargas):
        if self._erro is not None:
            raise self._erro
        self.enviado = cargas
        return self._resultado

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


def test_processar_envio_com_aviso_nao_chama_o_fusion(conn):
    fusion = FusionSaveCargaFalso()

    saida = service.processar_envio(
        conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]},
        [], ["sem motorista"], ["sem motorista"],
    )

    assert saida["status"] == "pendente"
    assert fusion.enviado is None
    assert service.listar_fila(conn)[0]["tentativas"] == 1


def test_processar_envio_sem_fusion_configurado_enfileira(conn):
    saida = service.processar_envio(
        conn, None, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    assert saida["status"] == "pendente"
    assert "não configurado" in saida["motivo"]


def test_processar_envio_fusion_indisponivel_enfileira(conn):
    fusion = FusionSaveCargaFalso(erro=FusionUnavailable())

    saida = service.processar_envio(
        conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]},
        [{"pedido_erp": "x"}], [], [],
    )

    assert saida["status"] == "pendente"
    assert "indisponível" in saida["motivo"].lower()


def test_processar_envio_fusion_rejeita_enfileira_com_o_motivo(conn):
    fusion = FusionSaveCargaFalso(resultado={"msg": "codmotorista () nao encontrado"})

    saida = service.processar_envio(
        conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    assert saida["status"] == "pendente"
    assert "nao encontrado" in saida["motivo"]


def test_processar_envio_entrega_servico_rejeitada_nao_chama_save_carga(conn):
    """D10: se saveEntregaServico falhar, saveCarga nem é tentado."""
    fusion = FusionSaveCargaFalso(
        resultado_entrega_servico={"erro_detalhes": [{"pedido": "x", "descricao": "Praca nao cadastrada"}], "success": []}
    )

    saida = service.processar_envio(
        conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]},
        [{"pedido_erp": "x"}], [], [],
    )

    assert saida["status"] == "pendente"
    assert "Praca nao cadastrada" in saida["motivo"]
    assert fusion.enviado is None


def test_processar_envio_feliz_marca_enviada(conn):
    fusion = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})

    payload_carga = {"entrega": [{"pedido_erp": "x"}], "cargas": [{"codigo_erp": "3_22099141"}]}
    payload_entregas_servico = [{"pedido_erp": "22094297_22099141"}]
    saida = service.processar_envio(
        conn, fusion, 3, 22099141, payload_carga, payload_entregas_servico, [], []
    )

    assert saida["status"] == "enviada"
    assert fusion.enviado == [payload_carga]
    assert fusion.enviado_entrega_servico == payload_entregas_servico
    assert service.listar_fila(conn) == []


def test_processar_envio_feliz_depois_de_uma_falha_marca_a_fila_como_enviada(conn):
    service.processar_envio(
        conn, None, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )
    assert service.listar_fila(conn)[0]["status"] == "PENDENTE"

    fusion = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    service.processar_envio(
        conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    assert service.listar_fila(conn) == []
    assert service.listar_fila(conn, apenas_pendentes=False)[0]["status"] == "ENVIADA"


def test_processar_envio_feliz_de_primeira_tambem_fica_rastreavel(conn):
    """Um envio que dá certo sem nunca ter passado pela fila antes também
    precisa virar registro — é a base de buscar_item_fila/forcar."""
    fusion = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})

    service.processar_envio(
        conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    item = service.buscar_item_fila(conn, 3, 22099141)
    assert item["status"] == "ENVIADA"
    # "T"/"Z" explícitos, não a string ingênua do sqlite3 — sem isso o
    # frontend lê CURRENT_TIMESTAMP (sempre UTC) como hora local e exibe
    # 3h a mais do horário de Brasília real.
    assert item["enviado_em"].endswith("Z")
    assert "T" in item["enviado_em"]


def test_sqlite_utc_iso():
    assert service._sqlite_utc_iso("2026-10-21 15:16:59") == "2026-10-21T15:16:59Z"
    assert service._sqlite_utc_iso(None) is None
    assert service._sqlite_utc_iso("") == ""


def test_processar_envio_carga_ja_enviada_nao_chama_o_fusion_de_novo(conn):
    primeira = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    service.processar_envio(
        conn, primeira, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    segunda = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    saida = service.processar_envio(
        conn, segunda, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    assert saida["status"] == "ja_enviada"
    assert saida["fila"]["status"] == "ENVIADA"
    assert segunda.enviado is None
    assert segunda.enviado_entrega_servico is None


def test_processar_envio_carga_ja_enviada_com_forcar_chama_o_fusion(conn):
    primeira = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    service.processar_envio(conn, primeira, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], [])

    segunda = FusionSaveCargaFalso(resultado={"success": "456", "errors": []})
    payload_carga = {"entrega": [{"pedido_erp": "x"}], "cargas": [{"codigo_erp": "3_22099141"}]}
    saida = service.processar_envio(conn, segunda, 3, 22099141, payload_carga, [], [], [], forcar=True)

    assert saida["status"] == "enviada"
    assert segunda.enviado == [payload_carga]


def test_cargas_ja_enviadas_inclui_enviada_de_verdade_e_marcada_manualmente(conn):
    service.marcar_integrada_manualmente(conn, 1, 100)
    fusion = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    service.processar_envio(conn, fusion, 2, 200, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], [])
    service._registrar_falha_na_fila(conn, 3, 300, "sem motorista", [])

    assert service.cargas_ja_enviadas(conn) == {(1, 100), (2, 200)}


def test_marcar_integrada_manualmente_nao_chama_o_fusion(conn):
    item = service.marcar_integrada_manualmente(conn, 3, 22099141)

    assert item["status"] == "ENVIADA"
    assert item["integrada_manualmente"] is True
    assert item["enviado_em"] is not None


def test_processar_envio_de_carga_marcada_manualmente_recusa_sem_forcar(conn):
    service.marcar_integrada_manualmente(conn, 3, 22099141)

    fusion = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    saida = service.processar_envio(conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], [])

    assert saida["status"] == "ja_enviada"
    assert saida["fila"]["integrada_manualmente"] is True
    assert fusion.enviado is None


def test_processar_envio_real_depois_de_marcada_manualmente_desliga_a_flag(conn):
    """Um envio de verdade (forçado) sobrescreve a marcação manual — a
    carga passa a refletir que de fato foi enviada pelo Fusion desta vez."""
    service.marcar_integrada_manualmente(conn, 3, 22099141)

    fusion = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    saida = service.processar_envio(conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], [], forcar=True)

    assert saida["status"] == "enviada"
    assert saida["fila"]["integrada_manualmente"] is False


def test_registrar_falha_na_fila_incrementa_tentativas(conn):
    service._registrar_falha_na_fila(conn, 3, 22099141, "erro 1", [])
    fila = service._registrar_falha_na_fila(conn, 3, 22099141, "erro 2", [])

    assert fila["tentativas"] == 2
    assert fila["motivo"] == "erro 2"


def test_remover_da_fila(conn):
    service._registrar_falha_na_fila(conn, 3, 22099141, "erro", [])
    id_ = service.listar_fila(conn)[0]["id"]

    assert service.remover_da_fila(conn, id_) is True
    assert service.listar_fila(conn) == []


def test_remover_da_fila_inexistente(conn):
    assert service.remover_da_fila(conn, 999) is False


# --- escolha manual de motorista/veículo/entregas --------------------------


def test_filtrar_itens_selecionados_sem_selecao_devolve_todos():
    itens = [{"NUMSEQUENCIA": 1}, {"NUMSEQUENCIA": 2}]

    assert service.filtrar_itens_selecionados(itens, None) == itens


def test_filtrar_itens_selecionados_restringe_pela_sequencia():
    itens = [{"NUMSEQUENCIA": 1}, {"NUMSEQUENCIA": 2}, {"NUMSEQUENCIA": 3}]

    assert service.filtrar_itens_selecionados(itens, {1, 3}) == [
        {"NUMSEQUENCIA": 1},
        {"NUMSEQUENCIA": 3},
    ]


def test_filtrar_itens_selecionados_vazio_devolve_lista_vazia():
    """Selecionar nada é diferente de não selecionar nada — a pessoa
    desmarcou tudo de propósito."""
    itens = [{"NUMSEQUENCIA": 1}]

    assert service.filtrar_itens_selecionados(itens, set()) == []


def test_montar_payload_carga_avisa_quando_nao_ha_itens():
    payload, entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [], {}, MOTORISTA, VEICULO
    )

    assert "Nenhuma entrega selecionada" in " ".join(avisos)
    assert payload["entrega"] == []
    assert payload["cargas"][0]["entregas_carga"] == []
    assert entregas_servico == []
    # Bloqueante de verdade: a carga não tem nenhuma entrega, diferente de
    # "toda entrega já foi coberta por outro canal".
    assert avisos_bloqueantes == avisos


# --- Pré-carga: payload e codigo_erp ---------------------------------------
# addendum à spec 2026-10-14-cargas-fusion-design.md: a pré-carga
# (DBA.PRE_CARGA) é enviada ao Fusion como se já fosse uma carga formada,
# mas sem sugestão automática de motorista/veículo e com um namespace de
# codigo_erp próprio (nunca colide com o de uma carga real).

DETALHE_PRE_CARGA = {
    "IDPRECARGA": 796109,
    "IDEMPRESA": 2,
    "DESCRPRECARGA": "RECIFE CENTRO",
}

ITEM_PRE_CARGA = {
    "IDPLANILHADOCORI": 22118405,
    "IDORCAMENTO": 614596,
    "IDSUBPRODUTO": 9397,
    "IDCLIFOR": 26623,
    "IDCLIFORENTREGA": 26623,
    "VALTOTLIQUIDO": 510.0,
    "PESOBRUTO": 0.0,
    "DESCRCOMPRODUTO": "CIMENTO POTY CPII F 32 50KG",
}


def test_codigo_pre_carga_nunca_colide_com_codigo_carga():
    assert service.codigo_pre_carga(2, 796109) == "PC_2_796109"
    assert service.codigo_pre_carga(2, 796109) != service.codigo_carga(2, 796109)


def test_codigo_pedido_generico():
    assert service.codigo_pedido_generico(614596, "PC796109") == "614596_PC796109"


def test_montar_payload_pre_carga_usa_o_namespace_proprio():
    payload_carga, entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_pre_carga(
        DETALHE_PRE_CARGA, [ITEM_PRE_CARGA], {26623: CLIENTE}, MOTORISTA, VEICULO
    )

    assert avisos == []
    assert avisos_bloqueantes == []
    assert payload_carga["cargas"][0]["codigo_erp"] == "PC_2_796109"
    assert payload_carga["cargas"][0]["codmotorista"] == "M-1"
    assert payload_carga["cargas"][0]["codveiculo"] == "V-1"
    # PRE_CARGA não tem UF/cidade (fato do schema) — mas com um único
    # cliente no pedido, o destino vira só o nome, sem "" ou " - " solto.
    assert payload_carga["cargas"][0]["destino"] == "FULANO"
    assert payload_carga["cargas"][0]["obs"] == "RECIFE CENTRO"
    assert payload_carga["entrega"][0]["pedido_erp"] == "614596_PC796109"
    assert entregas_servico[0]["pedido_erp"] == "614596_PC796109"
    assert entregas_servico[0]["valor"] == "510.000"


def test_montar_payload_pre_carga_sem_nf_fica_vazia_nao_errada():
    """Sem carga formada, não há NF real ainda — nf/serie ficam vazios em
    vez de repetir o erro do D11 original (ler a pré-nota como NF)."""
    _, entregas_servico, _, _ = service.montar_payload_pre_carga(
        DETALHE_PRE_CARGA, [ITEM_PRE_CARGA], {26623: CLIENTE}, MOTORISTA, VEICULO
    )

    assert entregas_servico[0]["nf"] == ""
    assert entregas_servico[0]["serie"] == ""


def test_montar_payload_pre_carga_avisa_sobre_motorista_e_veiculo_ausentes():
    """Sem sugestão automática (o CISS nunca preenche motorista/veículo
    numa pré-carga) — a ausência vira o mesmo aviso bloqueante de uma
    carga real sem cadastro correspondente."""
    payload_carga, _, avisos, avisos_bloqueantes = service.montar_payload_pre_carga(
        DETALHE_PRE_CARGA, [ITEM_PRE_CARGA], {26623: CLIENTE}, None, None
    )

    assert any("motorista" in a for a in avisos)
    assert any("veículo" in a for a in avisos)
    assert "motorista" not in payload_carga
    assert "veiculo" not in payload_carga
    assert avisos_bloqueantes == avisos


# --- montar_payload_carga: pedidos já enviados como pré-carga --------------


def test_montar_payload_carga_pula_pedido_ja_coberto_por_pre_carga():
    payload_carga, entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE,
        [ITEM],
        {26623: CLIENTE},
        MOTORISTA,
        VEICULO,
        pedidos_excluidos={ITEM["IDPLANILHADOCORI"]},
    )

    assert payload_carga["entrega"] == []
    assert payload_carga["cargas"][0]["entregas_carga"] == []
    assert entregas_servico == []
    assert any("já enviado ao Fusion como pré-carga" in a for a in avisos)
    # Não bloqueante — já coberto por outro canal, não é um erro: uma
    # carga 100% assim deve poder ser marcada enviada sem chamar o Fusion
    # (ver processar_envio), não ficar travada em "pendente" pra sempre.
    assert avisos_bloqueantes == []


def test_montar_payload_carga_sem_exclusao_envia_tudo_normalmente():
    payload_carga, entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [ITEM], {26623: CLIENTE}, MOTORISTA, VEICULO, pedidos_excluidos=None
    )

    assert len(payload_carga["entrega"]) == 1
    assert len(entregas_servico) == 1
    assert not any("pré-carga" in a for a in avisos)
    assert avisos_bloqueantes == []


def test_processar_envio_com_cobertura_parcial_de_pre_carga_envia_so_o_pedido_novo(conn):
    """Carga com dois pedidos, um já coberto por pré-carga e outro novo —
    o aviso informativo do pedido coberto não deve travar o envio do
    pedido novo (a causa raiz do pedido do usuário: um aviso não
    bloqueante bloqueando o resto da carga)."""
    item_novo = {**ITEM, "IDPLANILHADOCORI": 999997, "IDORCAMENTO": 888888, "NUMSEQUENCIA": 2}
    payload_carga, entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [ITEM, item_novo], {26623: CLIENTE}, MOTORISTA, VEICULO,
        pedidos_excluidos={ITEM["IDPLANILHADOCORI"]},
    )

    assert any("já enviado ao Fusion como pré-carga" in a for a in avisos)
    assert avisos_bloqueantes == []
    assert len(payload_carga["entrega"]) == 1
    assert payload_carga["entrega"][0]["pedido_erp"] == "888888_22099141"

    fusion = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    saida = service.processar_envio(
        conn, fusion, 3, 22099141, payload_carga, entregas_servico, avisos, avisos_bloqueantes,
        clientes_por_idclifor={26623: CLIENTE},
    )

    assert saida["status"] == "enviada"
    assert fusion.enviado == [payload_carga]
    assert len(fusion.enviado_entrega_servico) == 1
    assert fusion.enviado_entrega_servico[0]["pedido_erp"] == "888888_22099141"


def test_processar_envio_carga_totalmente_coberta_por_pre_carga_nao_chama_o_fusion(conn):
    """Carga cujo único pedido já foi enviado via pré-carga: nada de novo
    pra mandar, marca enviada direto — sem essa guarda, uma carga 100%
    resolvida por outro canal (pré-carga manual) ficaria travada em
    "pendente" pra sempre, acumulando tentativas a cada reprocessamento
    automático."""
    payload_carga, entregas_servico, avisos, avisos_bloqueantes = service.montar_payload_carga(
        DETALHE, [ITEM], {26623: CLIENTE}, MOTORISTA, VEICULO,
        pedidos_excluidos={ITEM["IDPLANILHADOCORI"]},
    )

    fusion = FusionSaveCargaFalso()
    saida = service.processar_envio(
        conn, fusion, 3, 22099141, payload_carga, entregas_servico, avisos, avisos_bloqueantes,
    )

    assert saida["status"] == "enviada"
    assert fusion.enviado is None
    assert fusion.enviado_entrega_servico is None
    assert saida["fila"]["status"] == "ENVIADA"


def test_processar_envio_pre_carga_sem_nada_pra_enviar_nao_chama_o_fusion(conn):
    """Mesmo shortcut do lado da pré-carga: nenhuma entrega no payload (por
    qualquer motivo não bloqueante) marca enviada direto, sem chamar o
    Fusion — crítico pro envio automático não gerar retry eterno."""
    payload_carga = {"entrega": [], "cargas": [{"codigo_erp": "PC_2_796109"}]}

    fusion = FusionSaveCargaFalso()
    saida = service.processar_envio_pre_carga(
        conn, fusion, 2, 796109, "PC_2_796109", None, None, None, None,
        payload_carga, [], [], [],
    )

    assert saida["status"] == "enviada"
    assert fusion.enviado is None
    assert fusion.enviado_entrega_servico is None


# --- pedidos_cobertos_por_pre_carga -----------------------------------------


def test_pedidos_cobertos_por_pre_carga_sem_origem_devolve_vazio(conn):
    assert service.pedidos_cobertos_por_pre_carga(conn, {}) == set()


def test_pedidos_cobertos_por_pre_carga_so_conta_pre_carga_enviada(conn):
    service._registrar_falha_na_fila_pre_carga(
        conn, 2, 796109, "PC_2_796109", None, None, None, None, "sem motorista", []
    )
    service._marcar_enviada_na_fila_pre_carga(conn, 2, 796167, "PC_2_796167", None, None, None, None)

    origem_por_pedido = {22118405: {796109}, 22134633: {796167}, 22134815: {999999}}

    assert service.pedidos_cobertos_por_pre_carga(conn, origem_por_pedido) == {22134633}


# --- fila de pré-carga -------------------------------------------------------


def test_processar_envio_pre_carga_com_aviso_nao_chama_o_fusion(conn):
    fusion = FusionSaveCargaFalso()

    saida = service.processar_envio_pre_carga(
        conn, fusion, 2, 796109, "PC_2_796109", "M-1", "Fulano", "V-1", "WOLKS",
        {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], ["sem motorista"], ["sem motorista"],
    )

    assert saida["status"] == "pendente"
    assert fusion.enviado is None
    assert service.listar_fila_pre_cargas(conn)[0]["tentativas"] == 1
    assert service.listar_fila_pre_cargas(conn)[0]["motorista_codigo_erp"] == "M-1"
    assert service.listar_fila_pre_cargas(conn)[0]["motorista_nome"] == "Fulano"
    assert service.listar_fila_pre_cargas(conn)[0]["veiculo_codigo_erp"] == "V-1"
    assert service.listar_fila_pre_cargas(conn)[0]["veiculo_descricao"] == "WOLKS"


def test_processar_envio_pre_carga_sem_fusion_configurado_enfileira(conn):
    saida = service.processar_envio_pre_carga(
        conn, None, 2, 796109, "PC_2_796109", None, None, None, None, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    assert saida["status"] == "pendente"
    assert "não configurado" in saida["motivo"]


def test_processar_envio_pre_carga_feliz_marca_enviada(conn):
    fusion = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})

    saida = service.processar_envio_pre_carga(
        conn, fusion, 2, 796109, "PC_2_796109", None, None, None, None,
        {"entrega": [{"pedido_erp": "x"}], "cargas": [{"codigo_erp": "PC_2_796109"}]}, [], [], [],
    )

    assert saida["status"] == "enviada"
    assert service.listar_fila_pre_cargas(conn) == []
    item = service.buscar_item_fila_pre_carga(conn, 2, 796109)
    assert item["status"] == "ENVIADA"
    assert item["codigo_erp"] == "PC_2_796109"


def test_processar_envio_pre_carga_ja_enviada_nao_chama_o_fusion_de_novo(conn):
    primeira = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    service.processar_envio_pre_carga(
        conn, primeira, 2, 796109, "PC_2_796109", None, None, None, None, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    segunda = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    saida = service.processar_envio_pre_carga(
        conn, segunda, 2, 796109, "PC_2_796109", None, None, None, None, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    assert saida["status"] == "ja_enviada"
    assert segunda.enviado is None


def test_processar_envio_pre_carga_ja_enviada_com_forcar_chama_o_fusion(conn):
    primeira = FusionSaveCargaFalso(resultado={"success": "123", "errors": []})
    service.processar_envio_pre_carga(
        conn, primeira, 2, 796109, "PC_2_796109", None, None, None, None, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [], [], []
    )

    segunda = FusionSaveCargaFalso(resultado={"success": "456", "errors": []})
    payload_carga = {"entrega": [{"pedido_erp": "x"}], "cargas": [{"codigo_erp": "PC_2_796109"}]}
    saida = service.processar_envio_pre_carga(
        conn, segunda, 2, 796109, "PC_2_796109", None, None, None, None, payload_carga, [], [], [], forcar=True
    )

    assert saida["status"] == "enviada"
    assert segunda.enviado == [payload_carga]


def test_pre_cargas_ja_enviadas(conn):
    service.processar_envio_pre_carga(
        conn,
        FusionSaveCargaFalso(resultado={"success": "1", "errors": []}),
        2,
        796109,
        "PC_2_796109",
        None,
        None,
        None,
        None,
        {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]},
        [],
        [],
        [],
    )
    service._registrar_falha_na_fila_pre_carga(
        conn, 2, 796167, "PC_2_796167", None, None, None, None, "sem motorista", []
    )

    assert service.pre_cargas_ja_enviadas(conn) == {(2, 796109)}


def test_remover_da_fila_pre_carga(conn):
    service._registrar_falha_na_fila_pre_carga(conn, 2, 796109, "PC_2_796109", None, None, None, None, "erro", [])
    id_ = service.listar_fila_pre_cargas(conn)[0]["id"]

    assert service.remover_da_fila_pre_carga(conn, id_) is True
    assert service.listar_fila_pre_cargas(conn) == []


def test_remover_da_fila_pre_carga_inexistente(conn):
    assert service.remover_da_fila_pre_carga(conn, 999) is False


# --- sincronização de cliente via sendClientes ------------------------------
# achado em produção, 2026-10-20: o cliente{} embutido no stub de
# entrega[]/saveEntregaServico não cria o cadastro no Fusion, só referencia
# um já existente — sem sendClientes antes, "Cliente ... nao foi localizado".


def test_cliente_para_sendclientes_monta_o_shape_da_doc():
    payload = service._cliente_para_sendclientes(CLIENTE, 3)

    assert payload["campo_alt"] == "NEW_59"
    assert payload["seq_id"] == "26623"
    assert payload["codigo_cliente"] == "26623"
    assert payload["filial_padrao"] == "3"
    assert payload["descr_cliente"] == "FULANO"
    assert payload["razao_cliente"] == "FULANO"
    assert payload["cnpj_cpf_cliente"] == "12345678900"
    assert payload["cliente_cod_rota_erp"] == "1"
    assert payload["cidade_cliente"] == "RECIFE"
    assert payload["uf_cliente"] == "PE"


def test_cliente_para_sendclientes_inclui_data_ult_compra():
    """Achado em produção (cliente 71451, carga 2_22142332): sem este
    campo o sendClientes rejeita o lote inteiro — "campos obrigatorios:
    data_ult_compra"."""
    cliente = {**CLIENTE, "DATA_ULTIMA_COMPRA": "2026-10-21"}

    payload = service._cliente_para_sendclientes(cliente, 3)

    assert payload["data_ult_compra"] == "2026-10-21"


def test_clientes_ja_sincronizados_vazio_sem_consulta(conn):
    assert service.clientes_ja_sincronizados(conn, []) == set()


def test_clientes_ja_sincronizados_so_os_marcados(conn):
    service._marcar_clientes_sincronizados(conn, [26623])

    assert service.clientes_ja_sincronizados(conn, [26623, 999]) == {26623}


def test_sincronizar_clientes_pendentes_sem_cliente_nao_chama_o_fusion(conn):
    fusion = FusionSaveCargaFalso()

    erro = service._sincronizar_clientes_pendentes(conn, fusion, {}, 3)

    assert erro is None
    assert fusion.enviado_clientes is None


def test_sincronizar_clientes_pendentes_envia_so_os_novos(conn):
    service._marcar_clientes_sincronizados(conn, [26623])
    fusion = FusionSaveCargaFalso()

    erro = service._sincronizar_clientes_pendentes(
        conn, fusion, {26623: CLIENTE, 99999: {**CLIENTE, "CODIGO_CLIENTE": 99999}}, 3
    )

    assert erro is None
    assert len(fusion.enviado_clientes) == 1
    assert fusion.enviado_clientes[0]["codigo_cliente"] == "99999"
    assert service.clientes_ja_sincronizados(conn, [26623, 99999]) == {26623, 99999}


def test_sincronizar_clientes_pendentes_ignora_cliente_none():
    """Cliente não encontrado no CISS (None) já vira aviso bloqueante em
    montar_payload_carga — não faz sentido tentar sincronizar algo que não
    existe."""
    fusion = FusionSaveCargaFalso()

    erro = service._sincronizar_clientes_pendentes(None, fusion, {26623: None}, 3)

    assert erro is None
    assert fusion.enviado_clientes is None


def test_sincronizar_clientes_pendentes_propaga_indisponibilidade(conn):
    fusion = FusionSaveCargaFalso(erro=FusionUnavailable())

    erro = service._sincronizar_clientes_pendentes(conn, fusion, {26623: CLIENTE}, 3)

    assert erro is not None
    assert "indisponível" in erro.lower()
    assert service.clientes_ja_sincronizados(conn, [26623]) == set()


def test_sincronizar_clientes_pendentes_rejeitado_nao_marca_sincronizado(conn):
    fusion = FusionSaveCargaFalso(resultado_clientes={"msg": "cliente invalido"})

    erro = service._sincronizar_clientes_pendentes(conn, fusion, {26623: CLIENTE}, 3)

    assert erro == "cliente invalido"
    assert service.clientes_ja_sincronizados(conn, [26623]) == set()


def test_processar_envio_sincroniza_cliente_antes_do_entrega_servico(conn):
    fusion = FusionSaveCargaFalso(resultado={"success": "1", "errors": []})

    service.processar_envio(
        conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [{"pedido_erp": "x"}], [], [],
        clientes_por_idclifor={26623: CLIENTE},
    )

    assert fusion.enviado_clientes[0]["codigo_cliente"] == "26623"
    assert service.clientes_ja_sincronizados(conn, [26623]) == {26623}


def test_processar_envio_nao_resincroniza_cliente_ja_enviado(conn):
    service._marcar_clientes_sincronizados(conn, [26623])
    fusion = FusionSaveCargaFalso(resultado={"success": "1", "errors": []})

    service.processar_envio(
        conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [{"pedido_erp": "x"}], [], [],
        clientes_por_idclifor={26623: CLIENTE},
    )

    assert fusion.enviado_clientes is None


def test_processar_envio_falha_ao_sincronizar_cliente_nao_chama_entrega_servico(conn):
    fusion = FusionSaveCargaFalso(resultado_clientes={"msg": "cliente invalido"})

    saida = service.processar_envio(
        conn, fusion, 3, 22099141, {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [{"pedido_erp": "x"}], [], [],
        clientes_por_idclifor={26623: CLIENTE},
    )

    assert saida["status"] == "pendente"
    assert saida["motivo"] == "cliente invalido"
    assert fusion.enviado_entrega_servico is None
    assert fusion.enviado is None


def test_processar_envio_pre_carga_sincroniza_cliente(conn):
    fusion = FusionSaveCargaFalso(resultado={"success": "1", "errors": []})

    service.processar_envio_pre_carga(
        conn, fusion, 2, 796109, "PC_2_796109", None, None, None, None,
        {"entrega": [{"pedido_erp": "x"}], "cargas": [{}]}, [{"pedido_erp": "x"}], [], [],
        clientes_por_idclifor={26623: CLIENTE},
    )

    assert fusion.enviado_clientes[0]["codigo_cliente"] == "26623"
    assert service.clientes_ja_sincronizados(conn, [26623]) == {26623}
