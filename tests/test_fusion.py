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
