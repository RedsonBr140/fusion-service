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
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
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
# Leitura destrutiva (getIntErp, obtemEventosPorID): o Fusion avança o
# cursor ao montar a resposta — desistir de esperar é perder o lote. Visto
# em produção (2026-11-26): obtemEventosPorID passou de 30s no meio da
# drenagem. Espera bem mais antes de abandonar.
CALL_TIMEOUT_DESTRUTIVO = (5.0, 180.0)

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


def _chamar(
    api_url: str,
    metodo: str,
    partes: dict[str, str],
    *,
    resparam_vazio_ok: bool = False,
    timeout: tuple[float, float] = CALL_TIMEOUT,
) -> object:
    """POST do envelope SOAP e devolve o conteúdo de `resParam` já
    decodificado do JSON. Loga o restante de `partes` (ex.: `array_dados`,
    `placaOuId`) e o corpo cru de `resParam` — nunca `login`/`senha`.

    `resparam_vazio_ok`: `getIntErp` com a fila vazia responde "vazio"
    (doc) — um `<resParam/>` sem texto ali é resposta legítima, não falha
    de transporte. Nas demais chamadas continua sendo erro.
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
            timeout=timeout,
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
    if elemento is not None and elemento.text is None and resparam_vazio_ok:
        logger.info("Fusion respondeu a %s com resParam vazio", metodo)
        return ""
    if elemento is None or elemento.text is None:
        logger.warning("Resposta do Fusion sem resParam para %s", metodo)
        raise FusionUnavailable from None

    logger.info("Fusion respondeu a %s com %s", metodo, elemento.text)
    return _decodificar_resparam(elemento.text)


def _heartbeat_url(api_url: str) -> str:
    """O heartbeat não é SOAP — é um endpoint legado (webservice v2 do
    integrador Delphi), `{host-raiz}/php/webservice/set_dt_ult_int_erp.php`,
    fora do path `/clientws/...` do WSDL. Deriva o host raiz de
    `FUSION_API_URL` em vez de exigir uma segunda URL de config."""
    partes = urlsplit(api_url)
    return f"{partes.scheme}://{partes.netloc}/php/webservice/set_dt_ult_int_erp.php"


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

    def get_int_erp(self, limite: int = 50) -> list[dict]:
        """Próximas pendências da fila Fusion → ERP (roteirizações:
        `T10_ROMANEIO_MONTAGEM_CARGA`, `T10_ROMANEIO_ROTEIRIZADO`, ...).

        **Leitura destrutiva**: toda pendência devolvida sai da fila do
        lado do Fusion na hora (doc do getIntErp) — não existe "olhar sem
        consumir". Quem chama precisa persistir o que recebeu antes de
        fazer qualquer outra coisa. O corpo cru da resposta fica no log
        de `_chamar` (INFO), que é a última rede de segurança.

        `limite_padrao`: sem ele o Fusion devolve 1 pendência por
        chamada; máximo documentado 50. Fila vazia → `[]`. Qualquer outra
        coisa que não seja lista/objeto de pendência (texto de erro,
        `{"msg": ...}`) levanta `FusionUnavailable` — não dá pra saber se
        algo foi consumido, mas engolir em silêncio seria pior.
        """
        resultado = _chamar(
            self._config.api_url,
            "getIntErp",
            {
                "login": self._config.login,
                "senha": self._config.senha,
                "limite_padrao": str(max(1, min(limite, 50))),
            },
            resparam_vazio_ok=True,
            timeout=CALL_TIMEOUT_DESTRUTIVO,
        )
        if resultado in ("", None, [], {}) or (isinstance(resultado, str) and not resultado.strip()):
            return []
        if isinstance(resultado, dict) and "CODIGO_INT" in resultado:
            resultado = [resultado]
        if isinstance(resultado, list) and all(isinstance(item, dict) for item in resultado):
            return resultado
        logger.warning("getIntErp devolveu formato inesperado: %r", resultado)
        raise FusionUnavailable

    def obtem_eventos(self, id_inicial: int | None = None) -> list[dict]:
        """Próximos (até 50) eventos do app do motorista — `obtemEventosPorID`.

        **Leitura destrutiva**, confirmado em produção (2026-11-26): o
        Fusion guarda um cursor por integração; a mesma chamada repetida
        devolve os 50 *seguintes*. `idInicial` só funciona como piso
        (valor acima do cursor → `[]`), nunca rebobina, e
        `eventosLidosOuNao(flag_lido='N')` também não traz de volta —
        testado. Mesma regra do `get_int_erp`: quem chama persiste antes
        de qualquer outra coisa; o corpo cru fica no log de `_chamar`.
        Fila vazia → `[]`; formato inesperado → `FusionUnavailable`."""
        resultado = _chamar(
            self._config.api_url,
            "obtemEventosPorID",
            {
                "login": self._config.login,
                "senha": self._config.senha,
                "idInicial": str(id_inicial or 0),
            },
            resparam_vazio_ok=True,
            timeout=CALL_TIMEOUT_DESTRUTIVO,
        )
        if resultado in ("", None, [], {}) or (isinstance(resultado, str) and not resultado.strip()):
            return []
        if isinstance(resultado, dict) and "t15_id" in resultado:
            resultado = [resultado]
        if isinstance(resultado, list) and all(isinstance(e, dict) and e.get("t15_id") for e in resultado):
            return resultado
        logger.warning("obtemEventosPorID devolveu formato inesperado: %r", resultado)
        raise FusionUnavailable

    def obtem_itens_devolvidos(self, id_evento: str) -> list[dict]:
        """Itens de um evento de devolução — `obtemEventosItensDevolvidos`
        (fora da doc, só no WSDL). Leitura pura: repetir devolve o mesmo.
        Resposta `[{"ENTREGA": {...}, "ITENS_DEVOLVIDOS": [...]}]`; cada
        item: `codigo_erp_produto` (`IDSUBPRODUTO`, ou `IDPRODUTO@IDSUBPRODUTO`
        no integrador nativo), `qtd_original`, `qtd_devolvido` (nulo = item
        não devolvido, numa devolução parcial), `id_motivo_devolucao`,
        `motivo_devolucao`. Devolve só a lista de itens."""
        resultado = _chamar(
            self._config.api_url,
            "obtemEventosItensDevolvidos",
            {"login": self._config.login, "senha": self._config.senha, "idEvento": str(id_evento)},
            resparam_vazio_ok=True,
        )
        if resultado in ("", None, [], {}):
            return []
        blocos = resultado if isinstance(resultado, list) else [resultado]
        if not all(isinstance(b, dict) for b in blocos):
            logger.warning("obtemEventosItensDevolvidos devolveu formato inesperado: %r", resultado)
            raise FusionUnavailable
        return [item for b in blocos for item in (b.get("ITENS_DEVOLVIDOS") or []) if isinstance(item, dict)]

    def detalhe_entrega(self, pedido_erp: str) -> dict | None:
        """Estado atual de uma entrega pelo código do pedido (leitura, não
        consome nada). `status` vem inteiro como string (`"1"`, `"7"`),
        `fusion_romaneio_id` diz em qual romaneio ela está agora. Pedido
        inexistente responde texto puro (`"Nao foi encontrado pedido com
        codigo: ..."`) → `None`, mesmo tratamento de `consultar_veiculo`."""
        resultado = _chamar(
            self._config.api_url,
            "detalheEntrega",
            {"login": self._config.login, "senha": self._config.senha, "pedido_erp": pedido_erp},
        )
        if isinstance(resultado, list) and len(resultado) == 1:
            resultado = resultado[0]
        return resultado if isinstance(resultado, dict) else None

    def set_int_erp(self, int_id: str, retorno_carga: str, carga: str | None = None) -> object:
        """Confirma ao Fusion que a pendência `int_id` (`CODIGO_INT` do
        getIntErp) foi recebida e internalizada no ERP. Sucesso documentado
        é o texto `"OK"`; devolve o retorno cru, quem chama decide.

        `carga`: número da carga no ERP. Confirmado em produção
        (2026-11-26) que sem ele o Fusion trata a chamada como **erro** —
        responde `{"<int_id>": "Erro: Retorno carga: <retorno_carga>"}` e
        grava nosso texto como motivo da falha."""
        partes = {
            "login": self._config.login,
            "senha": self._config.senha,
            "int_id": str(int_id),
            "retorno_carga": retorno_carga,
        }
        if carga:
            partes["carga"] = str(carga)
        return _chamar(self._config.api_url, "setIntErp", partes)

    def avisar_vivo(self, ultimo_ciclo: datetime) -> None:
        """Heartbeat do webservice legado (não o SOAP do WSDL) —
        `set_dt_ult_int_erp.php`, equivalente ao `Checkpoint` do
        integrador Delphi original: avisa o Fusion que esta integração
        está viva e acabou de terminar um ciclo. `ultimo_ciclo` precisa
        já vir em hora local de Brasília (GMT-3) — mesmo fuso que o
        integrador legado grava, não UTC.

        O nome exato do parâmetro de data não está confirmado (binário
        decompilado não resolveu o format string) — manda sob os dois
        nomes mais plausíveis (`data`, `dt_ult_sinc`); um parâmetro
        desconhecido não quebra a chamada, mesma tolerância já observada
        no SOAP (ex.: `obs`/`observacao` em `saveEntregaServico`).

        Usa `empresa_id` (id interno do Fusion), nunca o CNPJ — campo
        diferente de `login`/`senha` do SOAP, mas mesma credencial de
        webservice (`senha_ws`).
        """
        timestamp = ultimo_ciclo.strftime("%Y-%m-%d %H:%M:%S")
        dados = {
            "senha_ws": self._config.senha,
            "empresa_id": self._config.empresa_id,
            "data": timestamp,
            "dt_ult_sinc": timestamp,
        }
        url = _heartbeat_url(self._config.api_url)

        logger.info(
            "Fusion heartbeat set_dt_ult_int_erp empresa_id=%s data=%s",
            self._config.empresa_id,
            timestamp,
        )

        try:
            resposta = requests.post(url, data=dados, timeout=CALL_TIMEOUT)
        except requests.RequestException as exc:
            logger.warning("Fusion inalcançável (%s) chamando heartbeat", type(exc).__name__)
            raise FusionUnavailable from None

        if resposta.status_code != 200:
            logger.warning("Fusion respondeu status %s para heartbeat", resposta.status_code)
            raise FusionUnavailable from None
