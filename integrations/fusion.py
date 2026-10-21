"""Única fronteira do projeto com o webservice SOAP do Fusion.

Nada com formato de SOAP/XML deste webservice atravessa este arquivo em
direção a core/ ou a uma tool — mesmo princípio de integrations/ciss_auth.py
e integrations/alter_vision.py. Contrato mapeado em
docs/superpowers/specs/2026-10-14-cargas-fusion-design.md §5 e §7, lido
direto do WSDL real (int.fusiondms.com.br/clientws/clientws.wsdl): SOAP 1.1,
estilo RPC/encoded, não document/literal. Os dados de domínio (saveCarga,
sendMotoristas, ...) são sempre uma string JSON dentro do parâmetro
`array_dados` — o SOAP aqui é só o transporte.

Envelope e parsing confirmados contra o Fusion de produção (não só o WSDL):
`saveCarga`, `saveEntregaServico`, `consultarVeiculo` e `sendVeiculos` já
foram chamados de verdade com dado de teste identificável (spec §7,
"Validação de ponta a ponta"). O catálogo de formatos de resposta do
Fusion (sucesso/erro) é mais instável do que o WSDL sugere — ver
`tools/cargas_fusion/service._resultado_indica_sucesso`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import requests

if TYPE_CHECKING:
    from config import FusionConfig

logger = logging.getLogger(__name__)

# Confirmado contra o WSDL real, não o resumo em PDF — ver §7.1 da spec.
_INPUT_NAMESPACE = "urn:myInputNamespace"
_SOAP_ENVELOPE_NS = "http://schemas.xmlsoap.org/soap/envelope/"
_SOAP_ENCODING_NS = "http://schemas.xmlsoap.org/soap/encoding/"

# saveCarga pode levar até 100 itens numa chamada só (limite documentado);
# a leitura precisa de folga sobre o tempo de login/senha do CISS.
CALL_TIMEOUT = (5.0, 30.0)

_ENVELOPE = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<SOAP-ENV:Envelope '
    'xmlns:SOAP-ENV="{soap_ns}" '
    'xmlns:xsd="http://www.w3.org/2001/XMLSchema" '
    'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" '
    'xmlns:SOAP-ENC="{encoding_ns}" '
    'SOAP-ENV:encodingStyle="{encoding_ns}">'
    "<SOAP-ENV:Body>"
    '<ns1:{metodo} xmlns:ns1="{input_ns}">{partes}</ns1:{metodo}>'
    "</SOAP-ENV:Body>"
    "</SOAP-ENV:Envelope>"
)


class FusionError(Exception):
    """Base das falhas de integração com o Fusion."""


class FusionUnavailable(FusionError):
    """O Fusion está inalcançável, com falha, ou respondeu algo inesperado."""


def _montar_envelope(metodo: str, partes: dict[str, str]) -> str:
    corpo = "".join(
        f'<{nome} xsi:type="xsd:string">{escape(valor)}</{nome}>'
        for nome, valor in partes.items()
    )
    return _ENVELOPE.format(
        soap_ns=_SOAP_ENVELOPE_NS,
        encoding_ns=_SOAP_ENCODING_NS,
        input_ns=_INPUT_NAMESPACE,
        metodo=metodo,
        partes=corpo,
    )


def _nome_local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _decodificar_resparam(texto: str) -> object:
    """O corpo de resParam normalmente é uma string JSON — e, no exemplo
    da doc do saveCarga, vem com aspas extras ao redor (JSON duas vezes
    codificado). Mas nem sempre: `consultarVeiculo` devolve texto puro
    quando o veículo não existe (`"Veiculo nao encontrado!"`, sem aspas
    nem chaves) — confirmado contra o Fusion de produção. Por isso isto
    nunca levanta: quando não é JSON válido, devolve o texto cru, e quem
    chama decide o que esse texto significa.
    """
    try:
        valor = json.loads(texto)
    except ValueError:
        return texto
    if isinstance(valor, str):
        try:
            valor = json.loads(valor)
        except ValueError:
            pass
    return valor


def _sem_aspas_duplas(valor: object) -> object:
    """Fusion não tolera aspas duplas dentro de `array_dados` — mesmo
    corretamente escapadas no JSON (`\\"`), o parser do lado de lá quebra
    o array inteiro, sem apontar qual campo. Confirmado em produção:
    descrição de produto `1" TIPO CUNHA` (IDSUBPRODUTO 139919, carga
    `1_22142248`) bastou para `saveEntregaServico` rejeitar a chamada
    inteira com `"Error ao no array_dados informado..."` — bisecionado
    item a item contra produção até achar exatamente esse. Troca por
    aspa simples em toda string, recursivamente, antes de serializar —
    mais seguro que corrigir caso a caso em cada call site, já que
    qualquer campo de texto livre (descrição de produto, nome, endereço)
    pode trazer uma aspa do CISS."""
    if isinstance(valor, str):
        return valor.replace('"', "'")
    if isinstance(valor, list):
        return [_sem_aspas_duplas(v) for v in valor]
    if isinstance(valor, dict):
        return {chave: _sem_aspas_duplas(v) for chave, v in valor.items()}
    return valor


_CAMPOS_CREDENCIAL = ("login", "senha")


def _partes_sem_credenciais(partes: dict[str, str]) -> dict[str, str]:
    return {nome: valor for nome, valor in partes.items() if nome not in _CAMPOS_CREDENCIAL}


def _chamar(api_url: str, metodo: str, partes: dict[str, str]) -> object:
    """POST do envelope SOAP e devolve o conteúdo de `resParam` já
    decodificado do JSON. Loga o restante de `partes` (ex.: `array_dados`,
    `placaOuId`) e o corpo cru de `resParam` — nunca `login`/`senha`.
    """
    envelope = _montar_envelope(metodo, partes)

    logger.info(
        "Fusion chamando %s com %s", metodo, json.dumps(_partes_sem_credenciais(partes), ensure_ascii=False)
    )

    try:
        resposta = requests.post(
            api_url,
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction": f'"urn:{metodo}"',
            },
            timeout=CALL_TIMEOUT,
        )
    except requests.RequestException as exc:
        logger.warning("Fusion inalcançável (%s) chamando %s", type(exc).__name__, metodo)
        raise FusionUnavailable from None

    if resposta.status_code != 200:
        logger.warning("Fusion respondeu status %s para %s", resposta.status_code, metodo)
        raise FusionUnavailable from None

    try:
        raiz = ElementTree.fromstring(resposta.content)
    except ElementTree.ParseError:
        logger.warning("Resposta do Fusion não é XML válido para %s", metodo)
        raise FusionUnavailable from None

    elemento = next((el for el in raiz.iter() if _nome_local(el.tag) == "resParam"), None)
    if elemento is None or elemento.text is None:
        logger.warning("Resposta do Fusion sem resParam para %s", metodo)
        raise FusionUnavailable from None

    logger.info("Fusion respondeu a %s com %s", metodo, elemento.text)
    return _decodificar_resparam(elemento.text)


class FusionClient:
    """Descartável por chamada, como o CissClient: login/senha vão em cada
    requisição, nunca numa sessão compartilhada entre chamadas concorrentes.

    Só expõe os métodos de domínio que esta integração usa hoje (ver §7.3
    da spec) — não é um wrapper genérico do WSDL inteiro.
    """

    def __init__(self, config: "FusionConfig") -> None:
        self._config = config

    def _chamar_array(self, metodo: str, array_dados: list | dict) -> object:
        return _chamar(
            self._config.api_url,
            metodo,
            {
                "login": self._config.login,
                "senha": self._config.senha,
                "array_dados": json.dumps(_sem_aspas_duplas(array_dados), ensure_ascii=False),
            },
        )

    def save_carga(self, cargas: list[dict]) -> object:
        """Até 100 cargas por chamada — limite documentado do Fusion.
        Paginar em lotes de 100 é responsabilidade de quem chama.
        """
        return self._chamar_array("saveCarga", cargas)

    def save_entrega_servico(self, entregas: list[dict]) -> object:
        """Cria/atualiza o pedido no Fusion — upsert por
        `(empresa_fat, pedido_orig)`, segundo a doc. É aqui que o
        detalhe de produto (`itens[]`) entra; `saveCarga` não tem onde
        carregar isso (spec §4.3, achado ao vivo em 2026-10-14). Até 100
        por chamada, mesmo limite documentado do saveCarga.
        """
        return self._chamar_array("saveEntregaServico", entregas)

    def send_motoristas(self, motoristas: list[dict]) -> object:
        """Upsert por `codigo_erp` — nunca duplica, mas também nunca deixa
        ler de volta o que já existe (não há `consultarMotorista` no WSDL,
        confirmado em §7.1 da spec)."""
        return self._chamar_array("sendMotoristas", motoristas)

    def send_veiculos(self, veiculos: list[dict]) -> object:
        """Upsert por `codigo_erp`, como `send_motoristas`."""
        return self._chamar_array("sendVeiculos", veiculos)

    def send_clientes(self, clientes: list[dict]) -> object:
        """Upsert por `codigo_cliente` — sem `consultarCliente` no WSDL,
        mesma situação de motorista. Confirmado em produção (2026-10-20):
        o `cliente{}` embutido no stub de `entrega[]`/`saveEntregaServico`
        **não cria** o cadastro no Fusion, só referencia um já existente —
        sem essa chamada antes, a entrega é rejeitada com "Cliente ... nao
        foi localizado". Shape documentado em
        docs/superpowers/specs/2026-10-14-cargas-fusion-design.md §7 (doc
        `sendClientes` colada pelo Redson)."""
        return self._chamar_array("sendClientes", clientes)

    def consultar_veiculo(self, placa_ou_id: str) -> dict | None:
        """Único método com assinatura diferente dos demais: o WSDL real
        confirma o parâmetro `placaOuId`, sem `array_dados` (§7.1).

        `None` quando o veículo não existe no Fusion — confirmado contra
        produção que a resposta nesse caso é texto puro
        (`"Veiculo nao encontrado!"`), não JSON. Qualquer resposta que não
        seja um dict é tratada como "não encontrado", não como erro: um
        texto inesperado não é motivo para derrubar a sincronização.
        """
        resultado = _chamar(
            self._config.api_url,
            "consultarVeiculo",
            {
                "login": self._config.login,
                "senha": self._config.senha,
                "placaOuId": placa_ou_id,
            },
        )
        return resultado if isinstance(resultado, dict) else None

    def consultar_todos_veiculos(self, filtros: dict | None = None) -> object:
        return self._chamar_array("consultarTodosVeiculos", filtros or {})
