"""Regras de negócio da tool Cargas Fusion.

`integrations/ciss.py` devolve dado cru do CISS; `integrations/fusion.py`
fala SOAP cru com o Fusion. Este arquivo é onde os dois se encontram: monta
o payload do `saveCarga` e mantém o cadastro local de ajudante (spec
2026-10-14-cargas-fusion-design.md §7). Motorista e veículo não têm mais
cadastro local — são lidos ao vivo do portal web do Fusion
(`integrations/fusion_portal.py`), cada um com o mesmo trio de funções
(`*_sugerido`/`*_por_codigo_erp`/`buscar_*_fusion`).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta

from integrations.fusion import FusionClient, FusionUnavailable
from integrations.fusion_portal import FusionPortalClient, FusionPortalError

logger = logging.getLogger(__name__)


def _texto(valor) -> str | None:
    if valor is None:
        return None
    texto = str(valor).strip()
    return texto or None


def _sqlite_utc_iso(valor: str | None) -> str | None:
    """`CURRENT_TIMESTAMP` do sqlite3 é sempre UTC, mas devolve uma
    string ingênua (`"YYYY-MM-DD HH:MM:SS"`, sem `T` nem `Z`) — sem essa
    marcação, o frontend (`new Date(...)`) lê como hora local do
    navegador e não desconta nada ao exibir, mostrando 3h a mais do
    horário de Brasília real (o inverso da quirk das datas do CISS, que
    vêm marcadas como GMT sem ser — ver `integrations/ciss.py`)."""
    if not valor:
        return valor
    return valor.replace(" ", "T") + "Z"


def _numero(valor) -> float | None:
    if valor is None or valor == "":
        return None
    try:
        return float(valor)
    except (TypeError, ValueError):
        return None


def _inteiro(valor) -> int | None:
    numero = _numero(valor)
    return int(numero) if numero is not None else None


def _numero_informado(valor: float) -> str:
    """Formata um número garantindo que a string nunca seja exatamente
    `"0"` — confirmado em produção que o Fusion (PHP) trata a string "0"
    como vazia (`empty()` do PHP é `true` para `"0"`, mas falso para
    `"0.000"`), rejeitando campos numéricos "preenchidos" com ela como
    "não informados". Usado em cubagem/peso do stub de `entrega[]`, que o
    Fusion exige como campo obrigatório mesmo quando o CISS não tem o
    dado (produto sem dimensão/peso cadastrado)."""
    return f"{valor:.3f}"


def _peso_item(item: Mapping[str, object]) -> float:
    """O peso de uma linha de `get_itens_carga` — nunca `ESTOQUE_ANALITICO
    .PESOBRUTO` sozinho. Achado em produção (carga `1_22142248`, os 10
    itens): `EA.PESOBRUTO` vem `0` em ~96% das linhas de
    `ESTOQUE_ANALITICO` nesta base (amostra de 500k linhas, só 3.6% com
    valor) — não é "produto sem peso cadastrado" como a spec original
    assumiu, é a coluna errada quase sempre vazia. `PRODUTO_GRADE
    .PESOBRUTO` (`PRODUTO_PESOBRUTO` aqui) é o peso de catálogo por
    unidade da grade — populado pra 9 dos 10 itens daquela carga — e
    precisa de `× QTDPRODUTO` pra virar peso da linha, mesmo tratamento
    que `LARGURA`/`ALTURA`/`COMPRIMENTO` (mesma tabela) já recebem no
    cálculo de cubagem. `EA.PESOBRUTO`, quando de fato populado, já é o
    peso da linha (vem de um movimento real, não de catálogo) — por isso
    ganha prioridade sobre o cálculo de catálogo quando não é zero."""
    peso_movimento = _numero(item.get("PESOBRUTO")) or 0
    if peso_movimento:
        return peso_movimento
    peso_catalogo = _numero(item.get("PRODUTO_PESOBRUTO")) or 0
    qtd = _numero(item.get("QTDPRODUTO")) or 0
    return peso_catalogo * qtd


# --- Veículo: lido ao vivo do Fusion ---------------------------------------
# Sem cadastro local (mesma mudança de motorista, 2026-10-20): fusion-py já
# tem o lado do veículo resolvido (`FusionClient.buscar_veiculos`, grade
# `get_transportes.php`), sem campo de filtro confirmado — lista tudo e
# filtra em Python, tanto aqui quanto em integrations/fusion_portal.py.


def veiculo_local_para_envio_fusion(linha: Mapping[str, object]) -> dict[str, object]:
    """O shape que `sendVeiculos`/`saveCarga` esperam — ver o exemplo da
    doc do saveCarga: maioria dos campos em snake_case, mas `anoModelo` e
    `anoFabricacao` continuam camelCase mesmo no envio. `linha` vem do
    portal (`FusionPortalClient.veiculo_por_placa`/`veiculo_por_codigo_erp`),
    sem `peso_max_entregas`/`volume_max_entregas`/`qtd_pallets_veiculo` — a
    grade do portal não traz esses campos, nunca teve fonte melhor que
    placeholder pra eles (nem quando vinham do antigo cadastro local,
    semeado do CISS). Por isso vão sempre via `_numero_informado`, nunca
    como `0`/`0.0` cru: confirmado em produção (carga `1_22142248`) que
    mandar esses três campos como número 0 (não string) faz o `saveCarga`
    inteiro ser rejeitado como array malformado — mesma quirk do PHP
    `empty()` tratando zero numérico como "não informado", documentada
    para os demais campos obrigatórios deste payload."""
    return {
        "codigo_erp": linha["codigo_erp"],
        "placa": linha["placa"],
        "descricao": linha.get("descricao") or "",
        "modelo": linha.get("modelo") or "",
        "anoModelo": str(linha["ano_modelo"]) if linha.get("ano_modelo") is not None else "",
        "anoFabricacao": str(linha["ano_fabricacao"]) if linha.get("ano_fabricacao") is not None else "",
        "peso_max_entregas": _numero_informado(_numero(linha.get("peso_max_entregas")) or 0),
        "volume_max_entregas": _numero_informado(_numero(linha.get("volume_max_entregas")) or 0),
        "qtd_pallets_veiculo": _numero_informado(_numero(linha.get("qtd_pallets_veiculo")) or 0),
    }


def veiculo_sugerido(
    fusion_portal_client: FusionPortalClient | None, placa_transp: object
) -> dict | None:
    """Sugestão automática pra carga real, casando pela `PLACATRANSP` que
    veio do CISS — mesmo papel de `motorista_sugerido`, mas por placa, não
    por id. `None` sem erro quando o portal não está configurado, a carga
    não tem placa, ou o veículo não está (ainda) cadastrado no Fusion."""
    if fusion_portal_client is None or not placa_transp:
        return None
    try:
        return fusion_portal_client.veiculo_por_placa(str(placa_transp))
    except FusionPortalError:
        logger.warning("Falha ao consultar veículo sugerido (PLACATRANSP=%s) no portal Fusion", placa_transp)
        return None


def veiculo_por_codigo_erp(
    fusion_portal_client: FusionPortalClient | None, codigo_erp: str | None
) -> dict | None:
    """O veículo escolhido manualmente na tela — override de uma carga
    real, ou a escolha obrigatória de uma pré-carga. Mesmo papel de
    `motorista_por_codigo_erp`."""
    if fusion_portal_client is None or not codigo_erp:
        return None
    try:
        return fusion_portal_client.veiculo_por_codigo_erp(codigo_erp)
    except FusionPortalError:
        logger.warning("Falha ao consultar veículo %s no portal Fusion", codigo_erp)
        return None


def buscar_veiculos_fusion(fusion_portal_client: FusionPortalClient | None, termo: str) -> list[dict]:
    """Busca ao vivo no portal do Fusion, por placa ou descrição —
    substitui a listagem da extinta tabela local `fusion_veiculos`. Mesmo
    papel de `buscar_motoristas_fusion`: propaga `FusionPortalError`, não
    falha em silêncio — é uma busca explícita de quem está montando o
    envio."""
    if fusion_portal_client is None or not termo.strip():
        return []
    return fusion_portal_client.buscar_veiculos(termo.strip())


class SincronizacaoFusionError(Exception):
    """Falha ao sincronizar cadastro local com o Fusion — mensagem já em
    português, pronta para a resposta HTTP. Hoje só usada por ajudante:
    motorista e veículo não têm mais cadastro local pra sincronizar."""


# --- Cadastro local: ajudante ---------------------------------------------
# Motorista (2026-10-20) e veículo não têm mais cadastro local — ver
# `motorista_sugerido`/`motorista_por_codigo_erp`/`buscar_motoristas_fusion`
# e `veiculo_sugerido`/`veiculo_por_codigo_erp`/`buscar_veiculos_fusion`, que
# leem direto do Fusion via `fusion-py`. Ajudante continua local: não há
# `buscar_ajudantes` no Fusion (o papel "ajudante" não existe lá como grade
# própria, só como `sendMotoristas` com `tipo="Ajudante"` — não há
# `get_ajudantes.php` equivalente ao `get_motoristas.php`).

# _salvar_pessoa/_atualizar_pessoa usam isto para saber quais colunas extra
# existem de verdade em cada tabela — fusion_ajudantes não tem cpf/telefone/
# endereco/uf/cidade/cep.
_CAMPOS_EXTRA_POR_TABELA = {
    "fusion_ajudantes": (),
}


def _linha_pessoa(linha: sqlite3.Row) -> dict[str, object]:
    dado = {
        "id": linha["id"],
        "idclifor_ciss": linha["idclifor_ciss"],
        "codigo_erp": linha["codigo_erp"],
        "nome": linha["nome"],
        "ativo": bool(linha["ativo"]),
    }
    colunas = linha.keys()
    for campo in ("cpf", "telefone", "endereco", "uf", "cidade", "cep"):
        if campo in colunas:
            dado[campo] = linha[campo]
    return dado


def _listar_pessoas(conn: sqlite3.Connection, tabela: str, *, apenas_ativos: bool) -> list[dict]:
    sql = f"SELECT * FROM {tabela}"
    if apenas_ativos:
        sql += " WHERE ativo = 1"
    sql += " ORDER BY nome"
    return [_linha_pessoa(linha) for linha in conn.execute(sql)]


def _salvar_pessoa(conn: sqlite3.Connection, tabela: str, dados: Mapping[str, object]) -> dict:
    codigo_erp = _texto(dados.get("codigo_erp"))
    nome = _texto(dados.get("nome"))
    if not codigo_erp:
        raise ValueError("Informe o codigo_erp.")
    if not nome:
        raise ValueError("Informe o nome.")

    idclifor_ciss = _inteiro(dados.get("idclifor_ciss"))
    colunas_extra = _CAMPOS_EXTRA_POR_TABELA[tabela]
    colunas_sql = ", ".join(("idclifor_ciss", "codigo_erp", "nome", *colunas_extra))
    marcadores = ", ".join(["?"] * (3 + len(colunas_extra)))
    atualiza = ", ".join(f"{c} = excluded.{c}" for c in ("codigo_erp", "nome", *colunas_extra))

    valores = [idclifor_ciss, codigo_erp, nome, *(_texto(dados.get(c)) for c in colunas_extra)]

    with conn:
        if idclifor_ciss is not None:
            conn.execute(
                f"INSERT INTO {tabela} ({colunas_sql}) VALUES ({marcadores}) "
                f"ON CONFLICT (idclifor_ciss) DO UPDATE SET {atualiza}, ativo = 1, "
                "updated_at = CURRENT_TIMESTAMP",
                valores,
            )
            linha = conn.execute(
                f"SELECT * FROM {tabela} WHERE idclifor_ciss = ?", (idclifor_ciss,)
            ).fetchone()
        else:
            conn.execute(
                f"INSERT INTO {tabela} ({colunas_sql}) VALUES ({marcadores}) "
                f"ON CONFLICT (codigo_erp) DO UPDATE SET {atualiza}, ativo = 1, "
                "updated_at = CURRENT_TIMESTAMP",
                valores,
            )
            linha = conn.execute(
                f"SELECT * FROM {tabela} WHERE codigo_erp = ?", (codigo_erp,)
            ).fetchone()
    return _linha_pessoa(linha)


def _atualizar_pessoa(
    conn: sqlite3.Connection, tabela: str, id_: int, dados: Mapping[str, object]
) -> dict | None:
    campos, valores = [], []
    campos_validos = ("codigo_erp", "nome", *_CAMPOS_EXTRA_POR_TABELA[tabela], "ativo")
    for campo in campos_validos:
        if campo not in dados:
            continue
        campos.append(f"{campo} = ?")
        valores.append(int(bool(dados[campo])) if campo == "ativo" else dados[campo])

    if campos:
        campos.append("updated_at = CURRENT_TIMESTAMP")
        with conn:
            conn.execute(f"UPDATE {tabela} SET {', '.join(campos)} WHERE id = ?", (*valores, id_))

    linha = conn.execute(f"SELECT * FROM {tabela} WHERE id = ?", (id_,)).fetchone()
    return _linha_pessoa(linha) if linha else None


def _pessoa_local_para_envio_fusion(linha: Mapping[str, object], tipo: str) -> dict[str, object]:
    return {
        "codigo_erp": linha["codigo_erp"],
        "nome": linha.get("nome") or "",
        "cpf": linha.get("cpf") or "",
        "telefone": linha.get("telefone") or "",
        "endereco": linha.get("endereco") or "",
        "uf": linha.get("uf") or "",
        "cidade": linha.get("cidade") or "",
        "cep": linha.get("cep") or "",
        "tipo": tipo,
    }


def _sincronizar_pessoa(
    conn: sqlite3.Connection, fusion_client: FusionClient | None, tabela: str, id_: int, tipo: str
) -> dict:
    """Só empurra — não existe `consultarMotorista` no Fusion (spec §7.1,
    confirmado contra o WSDL real), então não há como ler de volta antes."""
    if fusion_client is None:
        raise SincronizacaoFusionError("Fusion não configurado.")

    linha = conn.execute(f"SELECT * FROM {tabela} WHERE id = ?", (id_,)).fetchone()
    if linha is None:
        raise SincronizacaoFusionError("Cadastro não encontrado.")

    local = _linha_pessoa(linha)
    try:
        fusion_client.send_motoristas([_pessoa_local_para_envio_fusion(local, tipo)])
    except FusionUnavailable:
        raise SincronizacaoFusionError("Fusion indisponível. Tente novamente.") from None
    return local


def motorista_sugerido(
    fusion_portal_client: FusionPortalClient | None, id_motorista_ciss: object
) -> dict | None:
    """Sugestão automática pra carga real, casando pelo `IDMOTORISTA` que
    veio do CISS. `IDMOTORISTA` é o próprio `IDCLIFOR` de
    `DBA.CLIENTE_FORNECEDOR` — mesma convenção do cliente
    (`CissClient.get_cliente_fusion` usa `CF.IDCLIFOR AS CODIGO_CLIENTE`) —
    e, por construção, é também o `codigo_erp` com que o motorista foi
    cadastrado no Fusion (confirmado contra os 3 motoristas que existiam na
    extinta tabela local antes desta mudança: `codigo_erp == idclifor_ciss`
    nos três). Não há `consultarMotorista` no WSDL SOAP — a confirmação é
    ao vivo, pelo portal web (`fusion-py`, 2026-10-20).

    `None` sem erro quando o portal não está configurado, a carga não tem
    `IDMOTORISTA`, ou o motorista não está (ainda) cadastrado no Fusion —
    os três casos resultam no mesmo aviso bloqueante em `_montar_payload`."""
    if fusion_portal_client is None or not id_motorista_ciss:
        return None
    try:
        return fusion_portal_client.motorista_por_codigo_erp(str(id_motorista_ciss))
    except FusionPortalError:
        logger.warning("Falha ao consultar motorista sugerido (IDMOTORISTA=%s) no portal Fusion", id_motorista_ciss)
        return None


def motorista_por_codigo_erp(
    fusion_portal_client: FusionPortalClient | None, codigo_erp: str | None
) -> dict | None:
    """O motorista escolhido manualmente na tela — override de uma carga
    real, ou a escolha obrigatória de uma pré-carga."""
    if fusion_portal_client is None or not codigo_erp:
        return None
    try:
        return fusion_portal_client.motorista_por_codigo_erp(codigo_erp)
    except FusionPortalError:
        logger.warning("Falha ao consultar motorista %s no portal Fusion", codigo_erp)
        return None


def buscar_motoristas_fusion(fusion_portal_client: FusionPortalClient | None, termo: str) -> list[dict]:
    """Busca ao vivo no portal do Fusion, por nome — substitui a listagem
    da extinta tabela local `fusion_motoristas` (2026-10-20): motorista não
    tem mais cadastro próprio nesta tool, o Fusion é a única fonte.
    Propaga `FusionPortalError` — diferente de `motorista_sugerido`/
    `motorista_por_codigo_erp`, aqui é uma busca explícita da pessoa que
    está montando o envio, não deve falhar em silêncio."""
    if fusion_portal_client is None or not termo.strip():
        return []
    return fusion_portal_client.buscar_motoristas(termo.strip())


def listar_ajudantes(conn: sqlite3.Connection, *, apenas_ativos: bool = True) -> list[dict]:
    return _listar_pessoas(conn, "fusion_ajudantes", apenas_ativos=apenas_ativos)


def salvar_ajudante(conn: sqlite3.Connection, dados: Mapping[str, object]) -> dict:
    return _salvar_pessoa(conn, "fusion_ajudantes", {"codigo_erp": dados.get("codigo_erp"), "nome": dados.get("nome"), "idclifor_ciss": dados.get("idclifor_ciss")})


def atualizar_ajudante(conn: sqlite3.Connection, id_: int, dados: Mapping[str, object]) -> dict | None:
    permitido = {k: v for k, v in dados.items() if k in ("codigo_erp", "nome", "ativo")}
    return _atualizar_pessoa(conn, "fusion_ajudantes", id_, permitido)


def sincronizar_ajudante(conn: sqlite3.Connection, fusion_client: FusionClient | None, id_: int) -> dict:
    return _sincronizar_pessoa(conn, fusion_client, "fusion_ajudantes", id_, "Ajudante")


# --- Montagem do payload do saveCarga ------------------------------------


def codigo_carga(id_empresa: int, id_planilha: int) -> str:
    return f"{id_empresa}_{id_planilha}"


def localizar_carga_por_documento(client, termo: str) -> tuple[int, int] | None:
    """Acha a carga a partir de um dos três jeitos de identificar ela:
    o próprio código (`"{id_empresa}_{id_planilha}"`), o pedido real
    (`IDORCAMENTO`) sozinho, ou a NF no formato `"numnota-serie"`. Tenta
    cada formato pela marca que o distingue (`_`, `-`, só dígitos) — não
    há ambiguidade real entre eles. `None` quando o termo não bate com
    nenhum formato reconhecido, ou bate mas não acha carga nenhuma (os
    três caminhos devolvem `None` nesse caso, nunca levantam)."""
    termo = termo.strip()
    if not termo:
        return None

    if "_" in termo:
        id_empresa_str, _, id_planilha_str = termo.partition("_")
        if id_empresa_str.isdigit() and id_planilha_str.isdigit():
            id_empresa, id_planilha = int(id_empresa_str), int(id_planilha_str)
            if client.get_carga_detalhe(id_empresa, id_planilha) is not None:
                return id_empresa, id_planilha
        return None

    if "-" in termo:
        numnota_str, _, serie = termo.partition("-")
        if numnota_str.isdigit():
            return client.get_carga_por_nota(int(numnota_str), serie.strip())
        return None

    if termo.isdigit():
        return client.get_carga_por_pedido(int(termo))

    return None


def codigo_pedido(numero_pedido: int, id_planilha_carga: int) -> str:
    """Convenção fixada com o Redson (spec §4.2): o mesmo pedido em duas
    cargas diferentes (redespacho, p.ex.) vira dois `pedido_erp` distintos,
    sem colidir. `numero_pedido` é sempre o `IDORCAMENTO` real — pedido sem
    orçamento vinculado nem chega a chamar esta função (`montar_payload_carga`
    pula essas entregas), nunca a NF. Caso particular de
    `codigo_pedido_generico` com `id_planilha_carga` como sufixo — mantido
    à parte porque é o formato testado/documentado desde a spec original."""
    return codigo_pedido_generico(numero_pedido, str(id_planilha_carga))


def _cliente_stub_para_fusion(cliente_ciss: Mapping[str, object] | None, id_empresa: int) -> dict:
    """O mínimo que o stub de `entrega[]` do `saveCarga` precisa (spec
    §4.3.2, confirmado por eliminação contra o Fusion de produção): sem o
    `cliente{}`, mesmo que só com `filial_padrao`, o `saveCarga` devolve o
    erro genérico de filial. Não foi testado se um cliente real exige mais
    campos — ver §8 da spec.

    `cliente_cod_praca_erp`/`cliente_descricao_praca` precisam vir
    preenchidos (a tool não rastreia praça/rota de verdade, spec §8): sem
    isso o Fusion rejeita com "Praca do Cliente[] nao cadastrada ... e
    Praca do Pedido[...] nao informada", confirmado em produção. O código
    do placeholder é **"1", nunca "0"** — confirmado em produção que "0"
    continua dando o mesmo erro mesmo preenchido: o lado do Fusion trata a
    string "0" como vazia (clássico de `empty()` em PHP, que é `true` pra
    `"0"`), então um código "preenchido" com "0" ainda conta como "não
    informado" pra validação deles. Mesmo placeholder usado em
    `praca_cod_erp`/`rota_cod_erp` do `saveEntregaServico` — qualquer valor
    que não seja `"0"` serve, já que a tool não tem praça/rota real."""
    return {
        "filial_padrao": str(id_empresa),
        "codigo_cliente": str((cliente_ciss or {}).get("CODIGO_CLIENTE") or ""),
        "cliente_cod_praca_erp": "1",
        "cliente_descricao_praca": ".",
        "cliente_cod_rota_erp": "1",
        "cliente_descricao_rota": ".",
    }


def _cliente_para_sendclientes(cliente_ciss: Mapping[str, object], id_empresa: int) -> dict:
    """O shape do `sendClientes` (doc Fusion colada pelo Redson,
    2026-10-20) — diferente do `cliente{}` embutido no stub de `entrega[]`
    (`_cliente_stub_para_fusion`): nomes de campo não batem, e esta
    chamada é quem de fato cria/atualiza o cadastro no Fusion — o stub só
    referencia um já existente. `campo_alt`/`seq_id` seguem o exemplo da
    doc (`campo_alt` sempre `"NEW_59"`; `seq_id` repete o código do ERP).
    `cliente_cod_rota_erp` é obrigatório na doc, mas a tool não rastreia
    rota real — mesmo placeholder `"1"` (nunca `"0"`) usado em todo lugar
    que precisa de um valor "preenchido" pro Fusion, pela mesma razão de
    `_cliente_stub_para_fusion`. As colunas de `cliente_ciss` já vêm com
    esses nomes — `get_cliente_fusion` foi desenhada em cima do `cliente{}`
    do stub, que por coincidência usa quase os mesmos nomes do
    `sendClientes` (`CNPJ_CLIENTE` é a única exceção — `sendClientes`
    espera `cnpj_cpf_cliente`)."""
    codigo = str(cliente_ciss.get("CODIGO_CLIENTE") or "")
    return {
        "campo_alt": "NEW_59",
        "seq_id": codigo,
        "codigo_cliente": codigo,
        "filial_padrao": str(id_empresa),
        "descr_cliente": _texto(cliente_ciss.get("DESCR_CLIENTE")) or "",
        "razao_cliente": _texto(cliente_ciss.get("RAZAO_CLIENTE")) or "",
        "cnpj_cpf_cliente": _texto(cliente_ciss.get("CNPJ_CLIENTE")) or "",
        "cliente_cod_rota_erp": "1",
        "cep_cliente": _texto(cliente_ciss.get("CEP_CLIENTE")) or "",
        "end_cliente": _texto(cliente_ciss.get("END_CLIENTE")) or "",
        "num_end_cliente": _texto(cliente_ciss.get("NUM_END_CLIENTE")) or "",
        "bairro_cliente": _texto(cliente_ciss.get("BAIRRO_CLIENTE")) or "",
        "cidade_cliente": _texto(cliente_ciss.get("CIDADE_CLIENTE")) or "",
        "uf_cliente": _texto(cliente_ciss.get("UF_CLIENTE")) or "",
        "email1_cliente": _texto(cliente_ciss.get("EMAIL1_CLIENTE")) or "",
        "tel1_cliente": _texto(cliente_ciss.get("TEL1_CLIENTE")) or "",
        "data_cadastro_cliente": _texto(cliente_ciss.get("DATA_CADASTRO_CLIENTE")) or "",
        # Achado em produção (cliente 71451, carga 2_22142332): sem este
        # campo o sendClientes rejeita o lote inteiro — "campos
        # obrigatorios: data_ult_compra". Sem coluna própria no CISS,
        # get_cliente_fusion deriva de MAX(NOTAS.DTMOVIMENTO).
        "data_ult_compra": _texto(cliente_ciss.get("DATA_ULTIMA_COMPRA")) or "",
    }


def clientes_ja_sincronizados(conn: sqlite3.Connection, ids_clifor: Sequence[int]) -> set[int]:
    """`IDCLIFOR` de todo cliente já empurrado pro Fusion via
    `sendClientes` — tabela local, sem `consultarCliente` no WSDL pra
    checar do lado de lá (mesma situação de motorista)."""
    ids = set(ids_clifor)
    if not ids:
        return set()
    marcadores = ", ".join(["?"] * len(ids))
    linhas = conn.execute(
        f"SELECT idclifor_ciss FROM fusion_clientes_sincronizados WHERE idclifor_ciss IN ({marcadores})",
        tuple(ids),
    )
    return {linha["idclifor_ciss"] for linha in linhas}


def _marcar_clientes_sincronizados(conn: sqlite3.Connection, ids_clifor: Sequence[int]) -> None:
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO fusion_clientes_sincronizados (idclifor_ciss) VALUES (?)",
            [(id_,) for id_ in ids_clifor],
        )


def _sincronizar_clientes_pendentes(
    conn: sqlite3.Connection,
    fusion_client: FusionClient,
    clientes_por_idclifor: Mapping[int, Mapping[str, object] | None],
    id_empresa: int,
    *,
    fusion_portal_client: FusionPortalClient | None = None,
) -> str | None:
    """Garante que todo cliente desta entrega já foi empurrado pro Fusion
    via `sendClientes` antes do `saveEntregaServico` — confirmado em
    produção (2026-10-20) que o `cliente{}` embutido no stub **não cria**
    o cadastro, só referencia um já existente (`"Cliente ... nao foi
    localizado"`). Sincroniza cada `IDCLIFOR` uma vez só, marcado
    localmente, pra não reenviar o cadastro em todo envio.

    `fusion_portal_client`, quando configurado, confere antes no portal web
    do Fusion (`buscar_clientes`, via `fusion-py` — não há `consultarCliente`
    no WSDL do SOAP) se o cliente já está cadastrado por outro meio que não
    este `sendClientes` — ex.: cadastro feito direto no Fusion, ou por outra
    integração. Encontrado assim, só marca localmente e poupa a chamada ao
    `sendClientes`. Falha na consulta ao portal (portal fora do ar, etc.)
    não bloqueia o envio: cai de volta no `sendClientes` cego, que é seguro
    (upsert por `codigo_cliente`).

    Devolve uma mensagem de erro quando a sincronização falha — quem
    chama trata isso como falha bloqueante, igual a "Fusion indisponível".
    `None` quando não havia nada pendente, ou quando sincronizou com
    sucesso.
    """
    ids_envolvidos = [idc for idc, cliente in clientes_por_idclifor.items() if cliente]
    faltantes = set(ids_envolvidos) - clientes_ja_sincronizados(conn, ids_envolvidos)
    if not faltantes:
        return None

    if fusion_portal_client is not None:
        ja_cadastrados: set[int] = set()
        for idc in faltantes:
            codigo = str(clientes_por_idclifor[idc].get("CODIGO_CLIENTE") or "")
            if not codigo:
                continue
            try:
                if fusion_portal_client.cliente_cadastrado(codigo):
                    ja_cadastrados.add(idc)
            except FusionPortalError:
                logger.warning("Falha ao consultar cliente %s no portal Fusion", codigo)

        if ja_cadastrados:
            _marcar_clientes_sincronizados(conn, list(ja_cadastrados))
            faltantes -= ja_cadastrados

        if not faltantes:
            return None

    payload = [_cliente_para_sendclientes(clientes_por_idclifor[idc], id_empresa) for idc in faltantes]
    try:
        resultado = fusion_client.send_clientes(payload)
    except FusionUnavailable:
        return "Fusion indisponível ao sincronizar cliente."

    sucesso, motivo = _resultado_indica_sucesso(resultado)
    if not sucesso:
        return motivo

    _marcar_clientes_sincronizados(conn, list(faltantes))
    return None


def _agrupar_itens_por_pedido(
    itens: Sequence[Mapping[str, object]]
) -> list[tuple[int, list[Mapping[str, object]]]]:
    """Uma "entrega" é um pedido, não uma linha de produto — D10 da spec.
    O agrupamento continua ancorado em `IDPLANILHADOCORI` (a pré-nota, não
    o pedido real — ver `get_itens_carga`), porque é a chave que sempre
    existe; `montar_payload_carga` decide, por grupo, se há `IDORCAMENTO`
    pra enviar ou se a entrega fica de fora. Preserva a ordem de primeira
    aparição."""
    grupos: dict[int, list[Mapping[str, object]]] = {}
    ordem: list[int] = []
    for item in itens:
        chave = item["IDPLANILHADOCORI"]
        if chave not in grupos:
            grupos[chave] = []
            ordem.append(chave)
        grupos[chave].append(item)
    return [(chave, grupos[chave]) for chave in ordem]


def _dt_saida_padrao() -> str:
    """D3 da spec: `dt_saida` nunca vem do CISS — é sempre `<dia atual + 1>
    07:00:00`, regra fixa calculada no momento do envio, independente de
    `DTHORASAIDA`/`DTCONFIRMACAO`/qualquer coluna da carga. Achado em
    produção: sem isso a carga não aparece em nenhuma consulta por data
    (`t10_data_saida` fica `0000-00-00`), e reenviar `saveCarga` para um
    `codigo_erp` já existente não corrige o campo depois — só a primeira
    chamada que cria a carga conta."""
    amanha = datetime.now() + timedelta(days=1)
    return amanha.replace(hour=7, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")


def codigo_pedido_generico(numero_pedido: int, sufixo: str) -> str:
    """O mesmo cálculo de `codigo_pedido`, generalizado para um sufixo que
    não é necessariamente `id_planilha` — pré-carga ainda não tem um
    (`montar_payload_pre_carga` usa um sufixo próprio, sem colidir com o
    de nenhuma carga real)."""
    return f"{numero_pedido}_{sufixo}"


def _montar_payload(
    cod_carga: str,
    id_empresa: int,
    destino: str,
    observacao: str,
    itens: Sequence[Mapping[str, object]],
    clientes_por_idclifor: Mapping[int, Mapping[str, object] | None],
    motorista: Mapping[str, object] | None,
    veiculo: Mapping[str, object] | None,
    sufixo_pedido: str,
    *,
    pedidos_excluidos: set[int] | None = None,
) -> tuple[dict, list[dict], list[str]]:
    """Monta o payload do `saveCarga` (com `entrega[]` como stub mínimo) e,
    separado, a lista de pedidos para `saveEntregaServico` — quem carrega o
    detalhe de produto que o `saveCarga` nunca teve como receber (D10 da
    spec, achado contra o Fusion de produção). Devolve também os avisos do
    que não pôde ser preenchido.

    Compartilhado entre `montar_payload_carga` (carga real,
    `DBA.RETIRADA_PRODUTOS`) e `montar_payload_pre_carga` (pré-carga,
    `DBA.PRE_CARGA` — addendum à spec): a única diferença de verdade entre
    os dois é de onde vêm `cod_carga`/`destino`/`observacao`/`itens`, e o
    sufixo do `pedido_erp` (`id_planilha` da carga, ou um código próprio de
    pré-carga, pra nunca colidir). Motorista/veículo são obrigatórios nos
    dois: numa carga real, pela sugestão automática (`PLACATRANSP`/
    `IDMOTORISTA`) ou pela escolha manual; numa pré-carga, só pela escolha
    manual — o CISS nunca preenche isso lá.

    `pedidos_excluidos` (`id_pedido_origem`, a chave de
    `_agrupar_itens_por_pedido`) são pedidos que já foram enviados ao
    Fusion como parte de uma pré-carga — usado só por `montar_payload_carga`
    pra não reenviar o que já foi mandado antes da carga existir.

    Pedido sem `IDORCAMENTO` (sem orçamento vinculado no CISS) não entra
    no envio — nem em `entrega[]`/`entregas_carga` do `saveCarga`, nem em
    `saveEntregaServico` — só gera aviso não bloqueante; o resto da carga
    segue normalmente (D11 revisado).

    `saveEntregaServico` precisa rodar antes do `saveCarga` que referencia
    esses pedidos (spec §5/§7.3) — `processar_envio` decide essa ordem, não
    esta função.

    `clientes_por_idclifor` vem pré-buscado (um `get_cliente_fusion` por
    cliente distinto da carga) — montar o payload não deveria decidir
    quando consultar o CISS.
    """
    avisos: list[str] = []
    pedidos_excluidos = pedidos_excluidos or set()

    if not itens:
        avisos.append("Nenhuma entrega selecionada para esta carga.")

    entrega_stubs: list[dict] = []
    entregas_carga: list[dict] = []
    entregas_servico: list[dict] = []
    # Pra decidir se todo pedido desta carga é do mesmo cliente (e, se
    # for, anexar o nome dele no destino — ver fim da função).
    ids_clifor_dos_pedidos: set[int] = set()

    seq = 0
    for id_pedido_origem, itens_do_pedido in _agrupar_itens_por_pedido(itens):
        if id_pedido_origem in pedidos_excluidos:
            avisos.append(
                f"Pedido {id_pedido_origem} já enviado ao Fusion como pré-carga — não reenviado."
            )
            continue

        primeiro = itens_do_pedido[0]

        # IDORCAMENTO é o número de pedido real (ver get_itens_carga).
        # Sem ele não há pedido_erp estável pro Fusion — o pedido fica de
        # fora do envio (não entra mais como pré-nota, D11 revisado:
        # pedido sem orçamento vinculado não deve aparecer pro Fusion).
        numero_pedido = primeiro.get("IDORCAMENTO")
        if numero_pedido is None:
            avisos.append(
                f"Pedido (orçamento) do item {id_pedido_origem} não encontrado no CISS — "
                "entrega não enviada ao Fusion."
            )
            continue

        seq += 1
        id_clifor = primeiro.get("IDCLIFORENTREGA") or primeiro.get("IDCLIFOR")
        cliente_ciss = clientes_por_idclifor.get(id_clifor) if id_clifor else None
        if id_clifor and cliente_ciss is None:
            avisos.append(f"Cliente {id_clifor} do pedido {id_pedido_origem} não encontrado no CISS.")
        if id_clifor:
            ids_clifor_dos_pedidos.add(id_clifor)

        cod_pedido = codigo_pedido_generico(numero_pedido, sufixo_pedido)
        valor_total = sum(_numero(i.get("VALTOTLIQUIDO")) or 0 for i in itens_do_pedido)
        peso_total = sum(_peso_item(i) for i in itens_do_pedido)
        # Cubagem real, a partir de LARGURA/ALTURA/COMPRIMENTO do catálogo
        # (PRODUTO_GRADE, cm) × QTDPRODUTO — cm³ para m³. Produto sem
        # dimensão cadastrada contribui 0 (comum, spec §8); o Fusion exige
        # o campo mesmo assim (ver _numero_informado).
        cubagem_total = sum(
            (_numero(i.get("LARGURA")) or 0)
            * (_numero(i.get("ALTURA")) or 0)
            * (_numero(i.get("COMPRIMENTO")) or 0)
            * (_numero(i.get("QTDPRODUTO")) or 0)
            for i in itens_do_pedido
        ) / 1_000_000

        entregas_servico.append(
            {
                "pedido_erp": cod_pedido,
                "num_ped_conf": cod_pedido,
                "pedido_orig": cod_pedido,
                "nf": _texto(primeiro.get("NUMNOTA")) or "",
                "serie": _texto(primeiro.get("SERIENOTA")) or "",
                # _numero_informado, não str() cru: valor_total é soma de
                # VALTOTLIQUIDO (int 0 quando todas as linhas vêm NULL),
                # e str(0) == "0" cai na mesma quirk do PESOBRUTO abaixo.
                "valor": _numero_informado(valor_total),
                "peso": _numero_informado(peso_total),
                "vendedor_erp": _texto(primeiro.get("IDVENDEDOR")) or "",
                # Nomes diferentes do stub de saveCarga (empresa_fat/log/digit,
                # não codigo_erp_filial_fat/log/digit) — spec §4.3.1.
                "empresa_fat": str(id_empresa),
                "empresa_log": str(id_empresa),
                "empresa_digit": str(id_empresa),
                # Confirmado em produção: o saveEntregaServico exige os mesmos
                # campos obrigatórios do stub de entrega do saveCarga
                # (cubagem, podeformarcarga, peso, codigo_cliente,
                # filial_padrao) — sem eles, "Campos obrigatorios (...) na
                # entrega da posicao N do array_dados".
                "cubagem": _numero_informado(cubagem_total),
                "podeformarcarga": "N",
                "codigo_cliente": str((cliente_ciss or {}).get("CODIGO_CLIENTE") or ""),
                "filial_padrao": str(id_empresa),
                # Sem fonte confirmada no CISS (spec §8) — placeholder, como
                # o cliente_cod_praca_erp do stub. "1", nunca "0": o Fusion
                # trata a string "0" como vazia (empty() do PHP), então "0"
                # continua contando como "não informado" na validação deles
                # — confirmado em produção.
                "praca_cod_erp": "1",
                "praca_descricao": ".",
                "rota_cod_erp": "1",
                "rota_descricao": ".",
                "ent_ou_serv": "Entrega",
                "status": "1",
                "itens": [
                    {
                        "cod_produto_erp": str(i.get("IDSUBPRODUTO") or ""),
                        # qtd/peso: mesma quirk do "0" que preco/subtotal
                        # abaixo — confirmado em produção (carga
                        # 1_22142248, itens sem PESOBRUTO cadastrado):
                        # mandar "0" cru aqui rejeita o saveEntregaServico
                        # inteiro como array malformado, sem apontar qual
                        # campo. _numero_informado garante "0.000", nunca "0".
                        "qtd": _numero_informado(_numero(i.get("QTDPRODUTO")) or 0),
                        "peso": _numero_informado(_peso_item(i)),
                        # descricao resolvido: PRODUTO.DESCRCOMPRODUTO, via
                        # get_itens_carga (confirmado contra o schema real do
                        # DB2 — estava em PRODUTO, não em PRODUTO_GRADE como a
                        # spec §8 supunha).
                        # preco/subtotal confirmados em produção como
                        # obrigatórios ("Campos obrigatorios (preco,
                        # subtotal) dos itens"). VALTOTLIQUIDO (já usado
                        # acima para valor_total) é o subtotal da própria
                        # linha; preco é o unitário derivado dele —
                        # _numero_informado por causa da mesma quirk do "0".
                        "preco": _numero_informado(
                            (_numero(i.get("VALTOTLIQUIDO")) or 0) / (_numero(i.get("QTDPRODUTO")) or 1)
                        ),
                        "subtotal": _numero_informado(_numero(i.get("VALTOTLIQUIDO")) or 0),
                        "descricao": _texto(i.get("DESCRCOMPRODUTO")) or "",
                    }
                    for i in itens_do_pedido
                ],
            }
        )

        entregas_carga.append(
            {
                "pedido_erp": cod_pedido,
                "codigo_erp_filial": str(id_empresa),
                "seq_entrega": str(seq),
            }
        )

        entrega_stubs.append(
            {
                "pedido_erp": cod_pedido,
                "codigo_erp_filial_fat": str(id_empresa),
                "codigo_erp_filial_log": str(id_empresa),
                "codigo_erp_filial_digit": str(id_empresa),
                # Confirmado em produção: o Fusion exige filial_padrao e
                # codigo_cliente direto no nível da entrega, não só dentro
                # de cliente{} — "Campos obrigatorios (cubagem,
                # podeformarcarga, peso, codigo_cliente, filial_padrao)".
                "filial_padrao": str(id_empresa),
                "codigo_cliente": str((cliente_ciss or {}).get("CODIGO_CLIENTE") or ""),
                "cubagem": _numero_informado(cubagem_total),
                "peso": _numero_informado(peso_total),
                # "N": a tool já formou a carga manualmente — não deixa o
                # motor de roteirização do próprio Fusion reaproveitar essa
                # entrega pra montar outra carga (mesmo default usado no
                # teste de ponta a ponta original, spec §7.4).
                "podeformarcarga": "N",
                "cliente": _cliente_stub_para_fusion(cliente_ciss, id_empresa),
            }
        )

    if motorista is None:
        avisos.append("Nenhum motorista encontrado no Fusion para esta carga.")
    if veiculo is None:
        avisos.append("Nenhum veículo encontrado no Fusion para esta carga.")

    # Pedido pra um cliente só (o caso comum — carga pra uma única entrega,
    # ou várias entregas da mesma obra/cliente): anexa o nome no destino,
    # "TRIUNFO/PE - REDSON DOS SANTOS SILVA". Mais de um cliente na mesma
    # carga não teria como escolher qual nome mostrar — fica só a cidade/UF.
    # Fusion não documenta limite de tamanho pro campo `destino`; se
    # truncar meio nome, é comportamento deles, não bug daqui.
    destino_final = destino
    if len(ids_clifor_dos_pedidos) == 1:
        (unico_id_clifor,) = ids_clifor_dos_pedidos
        nome_cliente = _texto((clientes_por_idclifor.get(unico_id_clifor) or {}).get("RAZAO_CLIENTE"))
        if nome_cliente:
            destino_final = f"{destino} - {nome_cliente}" if destino else nome_cliente

    carga_payload = {
        "codigo_erp": cod_carga,
        "dt_saida": _dt_saida_padrao(),
        "codmotorista": motorista["codigo_erp"] if motorista else "",
        "codveiculo": veiculo["codigo_erp"] if veiculo else "",
        "destino": destino_final,
        "codfilialsaida": str(id_empresa),
        "obs": observacao,
        "modo_rota": "R",
        "sn_redespacho": "N",
        "entregas_carga": entregas_carga,
    }

    payload_carga: dict = {"entrega": entrega_stubs, "cargas": [carga_payload]}
    if motorista is not None:
        payload_carga["motorista"] = [_pessoa_local_para_envio_fusion(motorista, "Motorista")]
    if veiculo is not None:
        payload_carga["veiculo"] = [veiculo_local_para_envio_fusion(veiculo)]

    return payload_carga, entregas_servico, avisos


def montar_payload_carga(
    detalhe: Mapping[str, object],
    itens: Sequence[Mapping[str, object]],
    clientes_por_idclifor: Mapping[int, Mapping[str, object] | None],
    motorista: Mapping[str, object] | None,
    veiculo: Mapping[str, object] | None,
    *,
    pedidos_excluidos: set[int] | None = None,
) -> tuple[dict, list[dict], list[str]]:
    """Monta o envio de uma carga real (`DBA.RETIRADA_PRODUTOS`) — ver
    `_montar_payload` para o shape completo e os avisos gerados.

    `pedidos_excluidos` vem de `pedidos_cobertos_por_pre_carga`: pedidos
    desta carga cujos itens já foram enviados ao Fusion como parte de uma
    pré-carga `ENVIADA` — ficam de fora deste envio (com aviso não
    bloqueante), pra não duplicar no Fusion."""
    id_empresa = detalhe["IDEMPRESA"]
    id_planilha = detalhe["IDPLANILHA"]
    destino_partes = [p for p in (detalhe.get("DESCRCIDADE"), detalhe.get("UF")) if p]
    return _montar_payload(
        codigo_carga(id_empresa, id_planilha),
        id_empresa,
        "/".join(destino_partes) if destino_partes else "",
        detalhe.get("OBSERVACAO") or "",
        itens,
        clientes_por_idclifor,
        motorista,
        veiculo,
        str(id_planilha),
        pedidos_excluidos=pedidos_excluidos,
    )


def codigo_pre_carga(id_empresa: int, id_precarga: int) -> str:
    """Namespace próprio do `codigo_erp` de pré-carga — nunca colide com o
    de uma carga real (`codigo_carga`): nenhuma pré-carga tem `id_planilha`
    ainda, então não dá pra reaproveitar o mesmo formato."""
    return f"PC_{id_empresa}_{id_precarga}"


def montar_payload_pre_carga(
    detalhe: Mapping[str, object],
    itens: Sequence[Mapping[str, object]],
    clientes_por_idclifor: Mapping[int, Mapping[str, object] | None],
    motorista: Mapping[str, object] | None,
    veiculo: Mapping[str, object] | None,
) -> tuple[dict, list[dict], list[str]]:
    """Monta o envio de uma pré-carga (`DBA.PRE_CARGA`) como se já fosse
    uma carga formada — ver `_montar_payload` para o shape completo.

    Sem sugestão automática de motorista/veículo (o CISS nunca preenche
    isso numa pré-carga, confirmado em produção): omiti-los gera o mesmo
    aviso bloqueante de uma carga real sem cadastro correspondente —
    quem está enviando escolhe os dois na tela antes de confirmar.

    `destino` fica vazio: `PRE_CARGA` não tem `UF`/cidade (fato do schema,
    não lacuna de levantamento)."""
    id_empresa = detalhe["IDEMPRESA"]
    id_precarga = detalhe["IDPRECARGA"]
    return _montar_payload(
        codigo_pre_carga(id_empresa, id_precarga),
        id_empresa,
        "",
        detalhe.get("DESCRPRECARGA") or "",
        itens,
        clientes_por_idclifor,
        motorista,
        veiculo,
        f"PC{id_precarga}",
    )


def filtrar_itens_selecionados(
    itens: Sequence[Mapping[str, object]], numeros_sequencia: set[int] | None
) -> list[Mapping[str, object]]:
    """Os itens da carga, restritos à seleção da tela.

    `numeros_sequencia=None` significa "nenhuma seleção explícita" — todos
    os itens entram, o comportamento de antes de existir seleção. Uma
    seleção vazia (`set()`) é diferente: significa que a pessoa desmarcou
    tudo, e o resultado é uma lista vazia mesmo — quem monta o payload que
    decida se isso vira aviso bloqueante.
    """
    if numeros_sequencia is None:
        return list(itens)
    return [item for item in itens if item.get("NUMSEQUENCIA") in numeros_sequencia]


# --- Fila de envio ---------------------------------------------------------
# Nenhuma tentativa de envio falha pode só sumir num log — vira linha em
# `cargas_fusion_fila`, visível e reenviável depois que o cadastro for
# corrigido. Confirmado em produção: uma carga sem motorista é rejeitada
# pelo próprio Fusion ("codmotorista () ou cpf () nao encontrado..."), não
# só pelos avisos que a gente já antecipa.


def _linha_fila(linha: sqlite3.Row) -> dict[str, object]:
    return {
        "id": linha["id"],
        "id_empresa": linha["id_empresa"],
        "id_planilha": linha["id_planilha"],
        "status": linha["status"],
        "motivo": linha["motivo"],
        "avisos": json.loads(linha["avisos"]) if linha["avisos"] else [],
        "tentativas": linha["tentativas"],
        "enviado_em": _sqlite_utc_iso(linha["enviado_em"]),
        "integrada_manualmente": bool(linha["integrada_manualmente"]),
    }


def listar_fila(conn: sqlite3.Connection, *, apenas_pendentes: bool = True) -> list[dict]:
    sql = "SELECT * FROM cargas_fusion_fila"
    if apenas_pendentes:
        sql += " WHERE status = 'PENDENTE'"
    sql += " ORDER BY updated_at DESC"
    return [_linha_fila(linha) for linha in conn.execute(sql)]


def remover_da_fila(conn: sqlite3.Connection, id_: int) -> bool:
    with conn:
        cursor = conn.execute("DELETE FROM cargas_fusion_fila WHERE id = ?", (id_,))
    return cursor.rowcount > 0


def buscar_item_fila(conn: sqlite3.Connection, id_empresa: int, id_planilha: int) -> dict | None:
    linha = conn.execute(
        "SELECT * FROM cargas_fusion_fila WHERE id_empresa = ? AND id_planilha = ?",
        (id_empresa, id_planilha),
    ).fetchone()
    return _linha_fila(linha) if linha else None


def cargas_ja_enviadas(conn: sqlite3.Connection) -> set[tuple[int, int]]:
    """`(id_empresa, id_planilha)` de toda carga com `status = 'ENVIADA'` —
    enviada de verdade ou marcada manualmente (`integrada_manualmente`),
    as duas contam igual aqui. Usado pelo filtro "Não mostrar cargas já
    enviadas" de `buscar_cargas`; é tabela local (SQLite), não CISS — não
    precisa de IN parametrizado nem de lote, a tabela é pequena."""
    linhas = conn.execute("SELECT id_empresa, id_planilha FROM cargas_fusion_fila WHERE status = 'ENVIADA'")
    return {(linha["id_empresa"], linha["id_planilha"]) for linha in linhas}


def _registrar_falha_na_fila(
    conn: sqlite3.Connection,
    id_empresa: int,
    id_planilha: int,
    motivo: str,
    avisos: Sequence[str],
) -> dict:
    with conn:
        conn.execute(
            "INSERT INTO cargas_fusion_fila "
            "(id_empresa, id_planilha, status, motivo, avisos, tentativas) "
            "VALUES (?, ?, 'PENDENTE', ?, ?, 1) "
            "ON CONFLICT (id_empresa, id_planilha) DO UPDATE SET "
            "status = 'PENDENTE', motivo = excluded.motivo, avisos = excluded.avisos, "
            "tentativas = cargas_fusion_fila.tentativas + 1, integrada_manualmente = 0, "
            "updated_at = CURRENT_TIMESTAMP",
            (id_empresa, id_planilha, motivo, json.dumps(list(avisos), ensure_ascii=False)),
        )
    linha = conn.execute(
        "SELECT * FROM cargas_fusion_fila WHERE id_empresa = ? AND id_planilha = ?",
        (id_empresa, id_planilha),
    ).fetchone()
    return _linha_fila(linha)


def _marcar_enviada_na_fila(
    conn: sqlite3.Connection,
    id_empresa: int,
    id_planilha: int,
    *,
    integrada_manualmente: bool = False,
) -> dict:
    """UPSERT, não UPDATE: um envio que dá certo de primeira (sem nunca ter
    falhado antes) também precisa de registro — é o que `processar_envio`
    usa pra reconhecer que a carga já foi enviada e recusar reenviá-la por
    engano. `integrada_manualmente` marca a origem (ver
    `marcar_integrada_manualmente`) — sempre reescrita no upsert, pra um
    envio de verdade sobrescrever uma marcação manual antiga."""
    with conn:
        conn.execute(
            "INSERT INTO cargas_fusion_fila "
            "(id_empresa, id_planilha, status, motivo, avisos, tentativas, "
            "enviado_em, integrada_manualmente) "
            "VALUES (?, ?, 'ENVIADA', NULL, '[]', 0, CURRENT_TIMESTAMP, ?) "
            "ON CONFLICT (id_empresa, id_planilha) DO UPDATE SET "
            "status = 'ENVIADA', motivo = NULL, "
            "enviado_em = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP, "
            "integrada_manualmente = excluded.integrada_manualmente",
            (id_empresa, id_planilha, int(integrada_manualmente)),
        )
    return buscar_item_fila(conn, id_empresa, id_planilha)


def marcar_integrada_manualmente(conn: sqlite3.Connection, id_empresa: int, id_planilha: int) -> dict:
    """Marca a carga como já enviada ao Fusion sem chamar o Fusion — para
    carga que já foi integrada por fora desta tool (pela integração nativa
    do Fusion, ou manualmente), e que por isso nunca deveria ser reenviada
    por aqui sem querer. `processar_envio` trata essa marcação exatamente
    como um envio real: recusa reenviar sem `forcar=True`."""
    return _marcar_enviada_na_fila(conn, id_empresa, id_planilha, integrada_manualmente=True)


def _resultado_indica_sucesso(resultado: object) -> tuple[bool, str | None]:
    """O formato de erro do Fusion é instável: ora `{'success': ..., 'errors': [...]}`,
    ora só `{'msg': '...'}` quando rejeita a carga inteira, ora
    `erro_detalhes`/`success` como lista quando é o `saveEntregaServico`
    rejeitando por validação, ora `success` como booleano (`atualizaCarga`),
    ora uma string crua sem nem ser JSON (`saveCarga` com array malformado)
    — catálogo de sete formatos confirmados em produção, spec §7.1. A
    checagem é defensiva por isso: cada formato é tratado antes de cair no
    "sem sucesso nem erro reconhecível".
    """
    if isinstance(resultado, str):
        return False, resultado
    if not isinstance(resultado, Mapping):
        return False, "Resposta do Fusion em formato inesperado."
    if resultado.get("msg"):
        return False, str(resultado["msg"])
    erro_detalhes = resultado.get("erro_detalhes")
    if erro_detalhes:
        return False, "; ".join(
            ": ".join(str(v) for v in (detalhe.get("pedido"), detalhe.get("descricao")) if v)
            for detalhe in erro_detalhes
        )
    erros = resultado.get("errors")
    if erros:
        return False, "; ".join(str(erro) for erro in erros)

    success = resultado.get("success")
    if isinstance(success, bool):
        return (True, None) if success else (False, "Fusion recusou sem detalhar o motivo.")
    if isinstance(success, list):
        return (True, None) if success else (False, "Resposta do Fusion sem sucesso nem erro reconhecível.")
    if isinstance(success, str) and success:
        # Formato `"<qtd_erros>,<id_interno>"` do saveCarga real (spec §7.1,
        # formato 5) — só existe quando há vírgula; `"8814621"` sozinho
        # (exemplo da doc) é só um id de sucesso, sem contagem de erro.
        if "," in success:
            qtd_erros, _, _ = success.partition(",")
            if qtd_erros.strip().lstrip("-").isdigit() and int(qtd_erros) > 0:
                return False, f"Fusion reportou {qtd_erros.strip()} erro(s)."
        return True, None
    return False, "Resposta do Fusion sem sucesso nem erro reconhecível."


def processar_envio(
    conn: sqlite3.Connection,
    fusion_client: FusionClient | None,
    id_empresa: int,
    id_planilha: int,
    payload_carga: Mapping[str, object],
    payload_entregas_servico: Sequence[Mapping[str, object]],
    avisos: Sequence[str],
    *,
    clientes_por_idclifor: Mapping[int, Mapping[str, object] | None] | None = None,
    fusion_portal_client: FusionPortalClient | None = None,
    forcar: bool = False,
) -> dict:
    """Envia a carga ao Fusion, ou enfileira para resolução manual.

    Uma carga com `status = 'ENVIADA'` em `cargas_fusion_fila` não é
    reenviada — nem chama o Fusion — a menos que `forcar=True`. É o que
    evita reenvio acidental (ex.: duplo clique, recarregar a prévia) criar
    `saveCarga`/`saveEntregaServico` duplicados no Fusion, que não tem
    idempotência garantida pelo `codigo_erp`/`pedido_erp` (spec não
    confirmou upsert para esses dois métodos, diferente de
    `sendMotoristas`/`sendVeiculos`).

    Avisos bloqueantes (motorista/veículo não cadastrado, cliente não
    encontrado) nem chegam a gerar uma chamada ao Fusion — já se sabe que
    seriam rejeitados, e poupar a chamada poupa também o Fusion de
    produção de receber uma carga que a gente já sabe que está incompleta.

    `clientes_por_idclifor` (o mesmo dict pré-buscado que
    `montar_payload_carga` usa) dispara `sendClientes` para todo cliente
    ainda não sincronizado localmente, antes do `saveEntregaServico` —
    achado em produção (2026-10-20): o `cliente{}` embutido no stub não
    cria o cadastro no Fusion, só referencia um já existente. Omitido
    (`None`), nenhum cliente é sincronizado — usado pelos testes que não
    envolvem cliente real.

    `saveEntregaServico` roda antes de `saveCarga` (D10 da spec): é ele que
    registra o pedido com os itens de produto; `saveCarga` só referencia o
    `pedido_erp` já registrado através do stub de `entrega[]`. Se o
    `saveEntregaServico` falhar, a carga nem chega a ser tentada.
    """
    if not forcar:
        item_existente = buscar_item_fila(conn, id_empresa, id_planilha)
        if item_existente is not None and item_existente["status"] == "ENVIADA":
            return {"status": "ja_enviada", "fila": item_existente}

    if avisos:
        motivo = "; ".join(avisos)
        fila = _registrar_falha_na_fila(conn, id_empresa, id_planilha, motivo, avisos)
        return {"status": "pendente", "motivo": motivo, "avisos": list(avisos), "fila": fila}

    if fusion_client is None:
        fila = _registrar_falha_na_fila(conn, id_empresa, id_planilha, "Fusion não configurado.", ())
        return {"status": "pendente", "motivo": "Fusion não configurado.", "avisos": [], "fila": fila}

    if clientes_por_idclifor:
        erro_sync = _sincronizar_clientes_pendentes(
            conn,
            fusion_client,
            clientes_por_idclifor,
            id_empresa,
            fusion_portal_client=fusion_portal_client,
        )
        if erro_sync:
            fila = _registrar_falha_na_fila(conn, id_empresa, id_planilha, erro_sync, ())
            return {"status": "pendente", "motivo": erro_sync, "avisos": [], "fila": fila}

    if payload_entregas_servico:
        try:
            resultado_entregas = fusion_client.save_entrega_servico(list(payload_entregas_servico))
        except FusionUnavailable:
            fila = _registrar_falha_na_fila(conn, id_empresa, id_planilha, "Fusion indisponível.", ())
            return {"status": "pendente", "motivo": "Fusion indisponível.", "avisos": [], "fila": fila}

        sucesso, motivo = _resultado_indica_sucesso(resultado_entregas)
        if not sucesso:
            fila = _registrar_falha_na_fila(conn, id_empresa, id_planilha, motivo, ())
            return {
                "status": "pendente",
                "motivo": motivo,
                "avisos": [],
                "resultado": resultado_entregas,
                "fila": fila,
            }

    try:
        resultado = fusion_client.save_carga([dict(payload_carga)])
    except FusionUnavailable:
        fila = _registrar_falha_na_fila(conn, id_empresa, id_planilha, "Fusion indisponível.", ())
        return {"status": "pendente", "motivo": "Fusion indisponível.", "avisos": [], "fila": fila}

    sucesso, motivo = _resultado_indica_sucesso(resultado)
    if not sucesso:
        fila = _registrar_falha_na_fila(conn, id_empresa, id_planilha, motivo, ())
        return {"status": "pendente", "motivo": motivo, "avisos": [], "resultado": resultado, "fila": fila}

    fila = _marcar_enviada_na_fila(conn, id_empresa, id_planilha)
    return {"status": "enviada", "resultado": resultado, "fila": fila}


# --- Fila de envio de pré-carga --------------------------------------------
# Paralela à fila de cargas (acima), mas com chave própria
# (id_empresa, id_precarga) — pré-carga não tem id_planilha até o CISS
# consolidá-la numa carga real — e com o motorista/veículo escolhidos
# manualmente na tela, gravados junto (a pré-carga não tem sugestão
# automática: ver montar_payload_pre_carga).


def _linha_fila_pre_carga(linha: sqlite3.Row) -> dict[str, object]:
    return {
        "id": linha["id"],
        "id_empresa": linha["id_empresa"],
        "id_precarga": linha["id_precarga"],
        "codigo_erp": linha["codigo_erp"],
        "motorista_codigo_erp": linha["motorista_codigo_erp"],
        "motorista_nome": linha["motorista_nome"],
        "veiculo_codigo_erp": linha["veiculo_codigo_erp"],
        "veiculo_descricao": linha["veiculo_descricao"],
        "status": linha["status"],
        "motivo": linha["motivo"],
        "avisos": json.loads(linha["avisos"]) if linha["avisos"] else [],
        "tentativas": linha["tentativas"],
        "enviado_em": _sqlite_utc_iso(linha["enviado_em"]),
    }


def listar_fila_pre_cargas(conn: sqlite3.Connection, *, apenas_pendentes: bool = True) -> list[dict]:
    sql = "SELECT * FROM cargas_fusion_precarga_fila"
    if apenas_pendentes:
        sql += " WHERE status = 'PENDENTE'"
    sql += " ORDER BY updated_at DESC"
    return [_linha_fila_pre_carga(linha) for linha in conn.execute(sql)]


def remover_da_fila_pre_carga(conn: sqlite3.Connection, id_: int) -> bool:
    with conn:
        cursor = conn.execute("DELETE FROM cargas_fusion_precarga_fila WHERE id = ?", (id_,))
    return cursor.rowcount > 0


def buscar_item_fila_pre_carga(conn: sqlite3.Connection, id_empresa: int, id_precarga: int) -> dict | None:
    linha = conn.execute(
        "SELECT * FROM cargas_fusion_precarga_fila WHERE id_empresa = ? AND id_precarga = ?",
        (id_empresa, id_precarga),
    ).fetchone()
    return _linha_fila_pre_carga(linha) if linha else None


def pre_cargas_ja_enviadas(conn: sqlite3.Connection) -> set[tuple[int, int]]:
    """`(id_empresa, id_precarga)` de toda pré-carga com `status = 'ENVIADA'`
    — mesmo papel de `cargas_ja_enviadas`, pro filtro "não mostrar pré-carga
    já enviada" da busca."""
    linhas = conn.execute(
        "SELECT id_empresa, id_precarga FROM cargas_fusion_precarga_fila WHERE status = 'ENVIADA'"
    )
    return {(linha["id_empresa"], linha["id_precarga"]) for linha in linhas}


def pedidos_cobertos_por_pre_carga(
    conn: sqlite3.Connection, origem_por_pedido: Mapping[int, set[int]]
) -> set[int]:
    """`id_pedido_origem` (chave de `_agrupar_itens_por_pedido`) de toda
    entrega de uma carga cujos itens já foram enviados ao Fusion como
    parte de uma pré-carga `ENVIADA` — usado por quem monta o envio da
    carga real (via `montar_payload_carga(..., pedidos_excluidos=...)`)
    pra não duplicar no Fusion o que já foi mandado quando ainda era
    pré-carga. `origem_por_pedido` vem de
    `CissClient.get_origem_pre_cargas_da_carga`.

    `IDPRECARGA` é chave primária de uma coluna só em `DBA.PRE_CARGA`
    (confirmado contra o catálogo do DB2) — já é globalmente único, por
    isso a consulta local não precisa parear com `id_empresa`.
    """
    todas_precargas = {idprecarga for conjunto in origem_por_pedido.values() for idprecarga in conjunto}
    if not todas_precargas:
        return set()
    marcadores = ", ".join(["?"] * len(todas_precargas))
    linhas = conn.execute(
        "SELECT id_precarga FROM cargas_fusion_precarga_fila "
        f"WHERE status = 'ENVIADA' AND id_precarga IN ({marcadores})",
        tuple(todas_precargas),
    )
    enviadas = {linha["id_precarga"] for linha in linhas}
    return {pedido for pedido, precargas in origem_por_pedido.items() if precargas & enviadas}


def _registrar_falha_na_fila_pre_carga(
    conn: sqlite3.Connection,
    id_empresa: int,
    id_precarga: int,
    codigo_erp: str,
    motorista_codigo_erp: str | None,
    motorista_nome: str | None,
    veiculo_codigo_erp: str | None,
    veiculo_descricao: str | None,
    motivo: str,
    avisos: Sequence[str],
) -> dict:
    with conn:
        conn.execute(
            "INSERT INTO cargas_fusion_precarga_fila "
            "(id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome, "
            "veiculo_codigo_erp, veiculo_descricao, status, motivo, avisos, tentativas) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDENTE', ?, ?, 1) "
            "ON CONFLICT (id_empresa, id_precarga) DO UPDATE SET "
            "codigo_erp = excluded.codigo_erp, motorista_codigo_erp = excluded.motorista_codigo_erp, "
            "motorista_nome = excluded.motorista_nome, "
            "veiculo_codigo_erp = excluded.veiculo_codigo_erp, "
            "veiculo_descricao = excluded.veiculo_descricao, status = 'PENDENTE', "
            "motivo = excluded.motivo, avisos = excluded.avisos, "
            "tentativas = cargas_fusion_precarga_fila.tentativas + 1, "
            "updated_at = CURRENT_TIMESTAMP",
            (
                id_empresa,
                id_precarga,
                codigo_erp,
                motorista_codigo_erp,
                motorista_nome,
                veiculo_codigo_erp,
                veiculo_descricao,
                motivo,
                json.dumps(list(avisos), ensure_ascii=False),
            ),
        )
    return buscar_item_fila_pre_carga(conn, id_empresa, id_precarga)


def _marcar_enviada_na_fila_pre_carga(
    conn: sqlite3.Connection,
    id_empresa: int,
    id_precarga: int,
    codigo_erp: str,
    motorista_codigo_erp: str | None,
    motorista_nome: str | None,
    veiculo_codigo_erp: str | None,
    veiculo_descricao: str | None,
) -> dict:
    with conn:
        conn.execute(
            "INSERT INTO cargas_fusion_precarga_fila "
            "(id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome, "
            "veiculo_codigo_erp, veiculo_descricao, status, motivo, avisos, tentativas, enviado_em) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'ENVIADA', NULL, '[]', 0, CURRENT_TIMESTAMP) "
            "ON CONFLICT (id_empresa, id_precarga) DO UPDATE SET "
            "codigo_erp = excluded.codigo_erp, motorista_codigo_erp = excluded.motorista_codigo_erp, "
            "motorista_nome = excluded.motorista_nome, "
            "veiculo_codigo_erp = excluded.veiculo_codigo_erp, "
            "veiculo_descricao = excluded.veiculo_descricao, status = 'ENVIADA', motivo = NULL, "
            "enviado_em = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP",
            (
                id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome,
                veiculo_codigo_erp, veiculo_descricao,
            ),
        )
    return buscar_item_fila_pre_carga(conn, id_empresa, id_precarga)


def processar_envio_pre_carga(
    conn: sqlite3.Connection,
    fusion_client: FusionClient | None,
    id_empresa: int,
    id_precarga: int,
    codigo_erp: str,
    motorista_codigo_erp: str | None,
    motorista_nome: str | None,
    veiculo_codigo_erp: str | None,
    veiculo_descricao: str | None,
    payload_carga: Mapping[str, object],
    payload_entregas_servico: Sequence[Mapping[str, object]],
    avisos: Sequence[str],
    *,
    clientes_por_idclifor: Mapping[int, Mapping[str, object] | None] | None = None,
    fusion_portal_client: FusionPortalClient | None = None,
    forcar: bool = False,
) -> dict:
    """Mesma lógica de `processar_envio`, pra pré-carga — ver lá para o
    porquê de cada decisão (trava contra reenvio, ordem
    `saveEntregaServico` antes de `saveCarga`, avisos bloqueantes sem
    chamada ao Fusion, sincronização de cliente via `sendClientes` antes
    do `saveEntregaServico`). `codigo_erp`/`motorista_codigo_erp`/
    `motorista_nome`/`veiculo_codigo_erp`/`veiculo_descricao` ficam
    gravados na fila mesmo numa falha, pra tela pré-preencher a escolha na
    próxima tentativa — nem motorista nem veículo têm mais `id` local: o
    `codigo_erp` já é o identificador (vem do Fusion), e nome/descrição são
    snapshot pra exibir sem precisar buscar de novo no Fusion."""
    if not forcar:
        item_existente = buscar_item_fila_pre_carga(conn, id_empresa, id_precarga)
        if item_existente is not None and item_existente["status"] == "ENVIADA":
            return {"status": "ja_enviada", "fila": item_existente}

    if avisos:
        motivo = "; ".join(avisos)
        fila = _registrar_falha_na_fila_pre_carga(
            conn, id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome,
            veiculo_codigo_erp, veiculo_descricao, motivo, avisos,
        )
        return {"status": "pendente", "motivo": motivo, "avisos": list(avisos), "fila": fila}

    if fusion_client is None:
        fila = _registrar_falha_na_fila_pre_carga(
            conn, id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome,
            veiculo_codigo_erp, veiculo_descricao, "Fusion não configurado.", (),
        )
        return {"status": "pendente", "motivo": "Fusion não configurado.", "avisos": [], "fila": fila}

    if clientes_por_idclifor:
        erro_sync = _sincronizar_clientes_pendentes(
            conn,
            fusion_client,
            clientes_por_idclifor,
            id_empresa,
            fusion_portal_client=fusion_portal_client,
        )
        if erro_sync:
            fila = _registrar_falha_na_fila_pre_carga(
                conn, id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome,
                veiculo_codigo_erp, veiculo_descricao, erro_sync, (),
            )
            return {"status": "pendente", "motivo": erro_sync, "avisos": [], "fila": fila}

    if payload_entregas_servico:
        try:
            resultado_entregas = fusion_client.save_entrega_servico(list(payload_entregas_servico))
        except FusionUnavailable:
            fila = _registrar_falha_na_fila_pre_carga(
                conn, id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome,
                veiculo_codigo_erp, veiculo_descricao, "Fusion indisponível.", (),
            )
            return {"status": "pendente", "motivo": "Fusion indisponível.", "avisos": [], "fila": fila}

        sucesso, motivo = _resultado_indica_sucesso(resultado_entregas)
        if not sucesso:
            fila = _registrar_falha_na_fila_pre_carga(
                conn, id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome,
                veiculo_codigo_erp, veiculo_descricao, motivo, (),
            )
            return {
                "status": "pendente",
                "motivo": motivo,
                "avisos": [],
                "resultado": resultado_entregas,
                "fila": fila,
            }

    try:
        resultado = fusion_client.save_carga([dict(payload_carga)])
    except FusionUnavailable:
        fila = _registrar_falha_na_fila_pre_carga(
            conn, id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome,
            veiculo_codigo_erp, veiculo_descricao, "Fusion indisponível.", (),
        )
        return {"status": "pendente", "motivo": "Fusion indisponível.", "avisos": [], "fila": fila}

    sucesso, motivo = _resultado_indica_sucesso(resultado)
    if not sucesso:
        fila = _registrar_falha_na_fila_pre_carga(
            conn, id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome,
            veiculo_codigo_erp, veiculo_descricao, motivo, (),
        )
        return {"status": "pendente", "motivo": motivo, "avisos": [], "resultado": resultado, "fila": fila}

    fila = _marcar_enviada_na_fila_pre_carga(
        conn, id_empresa, id_precarga, codigo_erp, motorista_codigo_erp, motorista_nome,
        veiculo_codigo_erp, veiculo_descricao,
    )
    return {"status": "enviada", "resultado": resultado, "fila": fila}
