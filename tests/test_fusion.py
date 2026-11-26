"""Testes de integrations/fusion.py — HTTP mockado com `responses`, mesmo
padrão de test_alter_vision.py e test_ciss_auth.py. Envelope e parsing
seguem o WSDL real (ver docs/superpowers/specs/2026-10-14-cargas-fusion-design.md
§7.1); não há chamada real ao Fusion de produção nesta suíte.
"""

import json
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import pytest
import requests
import responses

from config import FusionConfig
from integrations.fusion import FusionClient, FusionUnavailable

API_URL = "https://fusion.test/clientws/clientws.php"


def _config() -> FusionConfig:
    return FusionConfig(
        api_url=API_URL, login="usuario-fusion", senha="senha-fusion", empresa_id="1"
    )


def _client() -> FusionClient:
    return FusionClient(_config())


def _resposta_resparam(corpo_json: str) -> str:
    """Monta a resposta SOAP com o mesmo formato do exemplo da doc do
    saveCarga: resParam com aspas extras ao redor do JSON."""
    return (
        '<SOAP-ENV:Envelope SOAP-ENV:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/" '
        'xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="urn:myOutputNamespace" xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
        'xmlns:SOAP-ENC="http://schemas.xmlsoap.org/soap/encoding/">'
        "<SOAP-ENV:Body><ns1:saveCargaResponse>"
        f'<resParam xsi:type="xsd:string">{corpo_json}</resParam>'
        "</ns1:saveCargaResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )


@responses.activate
def test_save_carga_decodifica_resparam_com_json_duas_vezes_codificado():
    """O exemplo da doc do saveCarga mostra o resParam com o JSON envolto
    em aspas extras — um JSON dentro de outro JSON."""
    dado = {"success": "8814621", "errors": []}
    corpo_duplamente_codificado = json.dumps(json.dumps(dado))
    responses.add(
        responses.POST,
        API_URL,
        body=_resposta_resparam(escape(corpo_duplamente_codificado)),
        status=200,
    )

    resultado = _client().save_carga([{"entrega": []}])

    assert resultado == dado


@responses.activate
def test_save_carga_decodifica_resparam_sem_aspas_extras():
    """Mesmo sem o duplo encode, o resultado tem que decodificar certo."""
    dado = {"success": ["123"], "errors": []}
    responses.add(
        responses.POST, API_URL, body=_resposta_resparam(escape(json.dumps(dado))), status=200
    )

    resultado = _client().save_carga([{"entrega": []}])

    assert resultado == dado


@responses.activate
def test_envelope_tem_metodo_login_senha_e_array_dados_como_json():
    responses.add(responses.POST, API_URL, body=_resposta_resparam('{}'), status=200)

    _client().save_carga([{"codigo_erp": "ABC123"}])

    enviado = responses.calls[0].request.body.decode("utf-8")
    raiz = ElementTree.fromstring(enviado)

    corpo_metodo = next(el for el in raiz.iter() if el.tag.endswith("}saveCarga") or el.tag == "saveCarga")
    partes = {el.tag.rsplit("}", 1)[-1]: el.text for el in corpo_metodo}

    assert partes["login"] == "usuario-fusion"
    assert partes["senha"] == "senha-fusion"
    assert '"codigo_erp": "ABC123"' in partes["array_dados"]


@responses.activate
def test_soap_action_e_content_type_do_metodo_chamado():
    responses.add(responses.POST, API_URL, body=_resposta_resparam('{}'), status=200)

    _client().send_motoristas([{"nome": "FULANO"}])

    requisicao = responses.calls[0].request
    assert requisicao.headers["SOAPAction"] == '"urn:sendMotoristas"'
    assert requisicao.headers["Content-Type"] == "text/xml; charset=utf-8"


@responses.activate
def test_consultar_veiculo_usa_placa_ou_id_sem_array_dados():
    responses.add(responses.POST, API_URL, body=_resposta_resparam('{"placa": "ABC1234"}'), status=200)

    resultado = _client().consultar_veiculo("ABC1234")

    assert resultado == {"placa": "ABC1234"}
    enviado = responses.calls[0].request.body.decode("utf-8")
    assert "<placaOuId" in enviado
    assert "<array_dados" not in enviado


@responses.activate
def test_envelope_escapa_conteudo_com_caracteres_especiais():
    """Um valor com `<`/`&` no JSON não pode quebrar o XML enviado."""
    responses.add(responses.POST, API_URL, body=_resposta_resparam('{}'), status=200)

    _client().save_carga([{"obs": "Rota A & B <prioridade>"}])

    enviado = responses.calls[0].request.body.decode("utf-8")
    # Precisa parsear sem erro — se não escapou direito, isto já falha.
    ElementTree.fromstring(enviado)
    assert "Rota A &amp; B &lt;prioridade&gt;" in enviado


def test_servidor_inalcancavel_vira_fusion_unavailable():
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mocked:
        mocked.add(responses.POST, API_URL, body=requests.ConnectionError())

        with pytest.raises(FusionUnavailable):
            _client().save_carga([{}])


@responses.activate
def test_status_diferente_de_200_vira_fusion_unavailable():
    responses.add(responses.POST, API_URL, body="erro interno", status=500)

    with pytest.raises(FusionUnavailable):
        _client().save_carga([{}])


@responses.activate
def test_xml_invalido_vira_fusion_unavailable():
    responses.add(responses.POST, API_URL, body="isto não é XML", status=200)

    with pytest.raises(FusionUnavailable):
        _client().save_carga([{}])


@responses.activate
def test_resposta_sem_resparam_vira_fusion_unavailable():
    responses.add(
        responses.POST,
        API_URL,
        body=(
            '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">'
            "<SOAP-ENV:Body></SOAP-ENV:Body></SOAP-ENV:Envelope>"
        ),
        status=200,
    )

    with pytest.raises(FusionUnavailable):
        _client().save_carga([{}])


@responses.activate
def test_resparam_com_texto_puro_nao_e_erro_devolve_o_texto():
    """Confirmado em produção: consultarVeiculo devolve texto puro, não
    JSON, quando o veículo não existe (`"Veiculo nao encontrado!"`). Isso
    não pode virar FusionUnavailable — quem chama decide o que significa."""
    responses.add(responses.POST, API_URL, body=_resposta_resparam("Veiculo nao encontrado!"), status=200)

    resultado = _client().save_carga([{}])

    assert resultado == "Veiculo nao encontrado!"


@responses.activate
def test_consultar_veiculo_devolve_none_quando_resposta_e_texto_puro():
    responses.add(responses.POST, API_URL, body=_resposta_resparam("Veiculo nao encontrado!"), status=200)

    assert _client().consultar_veiculo("ZZZ9999") is None


@responses.activate
def test_nenhuma_credencial_aparece_na_excecao():
    responses.add(responses.POST, API_URL, body="erro interno", status=500)

    with pytest.raises(FusionUnavailable) as erro:
        _client().save_carga([{}])

    assert "senha-fusion" not in str(erro.value)
    assert "usuario-fusion" not in str(erro.value)


# --- getIntErp / setIntErp ---------------------------------------------


def _resposta_resparam_vazio() -> str:
    return (
        '<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" '
        'xmlns:ns1="urn:myOutputNamespace" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
        '<SOAP-ENV:Body><ns1:getIntErpResponse><resParam xsi:type="xsd:string"/>'
        "</ns1:getIntErpResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )


@responses.activate
def test_get_int_erp_manda_limite_e_devolve_lista():
    dado = [{"CODIGO_INT": "1330221", "TIPO_INT": "T10_ROMANEIO_ROTEIRIZADO", "t10_id": "823636"}]
    responses.add(responses.POST, API_URL, body=_resposta_resparam(escape(json.dumps(dado))), status=200)

    resultado = _client().get_int_erp(50)

    assert resultado == dado
    corpo = responses.calls[0].request.body.decode("utf-8")
    assert "<limite_padrao" in corpo and ">50</limite_padrao>" in corpo
    assert "urn:getIntErp" in responses.calls[0].request.headers["SOAPAction"]


@responses.activate
def test_get_int_erp_fila_vazia_com_resparam_vazio():
    responses.add(responses.POST, API_URL, body=_resposta_resparam_vazio(), status=200)
    assert _client().get_int_erp() == []


@pytest.mark.parametrize("corpo", ["[]", "", json.dumps("")])
@responses.activate
def test_get_int_erp_fila_vazia_em_json(corpo):
    responses.add(responses.POST, API_URL, body=_resposta_resparam(escape(corpo)), status=200)
    assert _client().get_int_erp() == []


@responses.activate
def test_get_int_erp_objeto_unico_vira_lista():
    dado = {"CODIGO_INT": "1", "TIPO_INT": "T10_ROMANEIO_MONTAGEM_CARGA"}
    responses.add(responses.POST, API_URL, body=_resposta_resparam(escape(json.dumps(dado))), status=200)
    assert _client().get_int_erp() == [dado]


@pytest.mark.parametrize("corpo", ['{"msg": "login invalido"}', "Erro qualquer"])
@responses.activate
def test_get_int_erp_formato_inesperado_levanta(corpo):
    responses.add(responses.POST, API_URL, body=_resposta_resparam(escape(corpo)), status=200)
    with pytest.raises(FusionUnavailable):
        _client().get_int_erp()


@responses.activate
def test_set_int_erp_manda_int_id_e_nao_loga_senha(caplog):
    responses.add(responses.POST, API_URL, body=_resposta_resparam("OK"), status=200)

    with caplog.at_level("INFO"):
        resultado = _client().set_int_erp("587221", "Internalizado")

    assert resultado == "OK"
    corpo = responses.calls[0].request.body.decode("utf-8")
    assert ">587221</int_id>" in corpo and ">Internalizado</retorno_carga>" in corpo
    assert "senha-fusion" not in caplog.text


@responses.activate
def test_set_int_erp_manda_carga_quando_informada():
    responses.add(responses.POST, API_URL, body=_resposta_resparam("OK"), status=200)
    _client().set_int_erp("587221", "Internalizado", carga="816609")
    assert ">816609</carga>" in responses.calls[0].request.body.decode("utf-8")


@responses.activate
def test_obtem_eventos_manda_id_inicial_e_devolve_lista():
    dado = [{"t15_id": "53475272", "t15_tipo": "7", "t15_romaneio_id": "816622"}]
    responses.add(responses.POST, API_URL, body=_resposta_resparam(escape(json.dumps(dado))), status=200)

    assert _client().obtem_eventos(53475271) == dado
    corpo = responses.calls[0].request.body.decode("utf-8")
    assert ">53475271</idInicial>" in corpo
    assert "urn:obtemEventosPorID" in responses.calls[0].request.headers["SOAPAction"]


@responses.activate
def test_obtem_eventos_fila_vazia():
    responses.add(responses.POST, API_URL, body=_resposta_resparam(escape("[]")), status=200)
    assert _client().obtem_eventos(999999999) == []


@pytest.mark.parametrize("corpo", ['{"msg": "login invalido"}', "Erro qualquer", '[{"x": 1}]'])
@responses.activate
def test_obtem_eventos_formato_inesperado_levanta(corpo):
    responses.add(responses.POST, API_URL, body=_resposta_resparam(escape(corpo)), status=200)
    with pytest.raises(FusionUnavailable):
        _client().obtem_eventos()


@responses.activate
def test_obtem_itens_devolvidos_devolve_so_os_itens():
    dado = [{"ENTREGA": {"t15_id": "53294700"}, "ITENS_DEVOLVIDOS": [
        {"codigo_erp_produto": "135793@135793", "qtd_original": "1", "qtd_devolvido": "1"}]}]
    responses.add(responses.POST, API_URL, body=_resposta_resparam(escape(json.dumps(dado))), status=200)

    assert _client().obtem_itens_devolvidos("53294700") == dado[0]["ITENS_DEVOLVIDOS"]
    assert ">53294700</idEvento>" in responses.calls[0].request.body.decode("utf-8")
