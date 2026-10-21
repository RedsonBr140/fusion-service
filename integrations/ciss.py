"""Única fronteira deste serviço com o pyodbc.

Cópia reduzida de meta-natal/backend/integrations/ciss.py — só os métodos
que a tool Cargas Fusion usa. Nada com formato de pyodbc atravessa este
arquivo em direção a cargas_fusion/ ou auth/. O CISS DB2 é somente leitura,
e é aqui que isso é feito valer, de forma independente da cópia do
meta-natal.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import CissDbConfig

logger = logging.getLogger(__name__)

# O servidor do CISS DB2 roda no horário de Brasília, não UTC — sem DST
# desde 2019, por isso um offset fixo (nunca precisa ajustar na virada).
_FUSO_CISS = timezone(timedelta(hours=-3))


def _normalizar_valor(valor: object) -> object:
    """`pyodbc` devolve `datetime`/`date` ingênuos (sem timezone). Sem
    marcar o fuso aqui, o provedor JSON do Flask serializa `datetime` cru
    como `"Wed, 21 Oct 2026 11:19:14 GMT"` (formato RFC 822, rotulado GMT
    mesmo sendo hora de Brasília) — o frontend lê isso como UTC de
    verdade e desconta 3h ao exibir no fuso local do navegador, mostrando
    uma carga confirmada às 11h como se fosse às 8h. Convertido pra ISO
    8601 com offset explícito (`-03:00`) na única fronteira com o
    `pyodbc`, pra nenhum datetime cru escapar pra `jsonify`. `date`
    (sem hora, ex. `DATA_CADASTRO_CLIENTE`) não tem esse risco — fuso não
    muda um dia — mas vira string aqui também, por consistência."""
    if isinstance(valor, datetime):
        return valor.replace(tzinfo=_FUSO_CISS).isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    return valor

# Defesa secundária. A primária é a permissão do usuário no DB2.
VERBOS_PROIBIDOS = frozenset(
    {
        "INSERT",
        "UPDATE",
        "DELETE",
        "MERGE",
        "TRUNCATE",
        "CREATE",
        "ALTER",
        "DROP",
        "GRANT",
        "REVOKE",
        "CALL",
        "EXEC",
        "EXECUTE",
        "SET",
    }
)

_INICIO_DE_LEITURA = re.compile(r"(SELECT|WITH)\b")
_PALAVRA = re.compile(r"[A-Z_][A-Z_0-9]*")

# Antes disto, a roteirização de cargas era feita pela integração nativa do
# próprio Fusion (CISSPoder ↔ Fusion sem passar por este código). Cargas de
# antes do corte pertencem ao fluxo antigo. DTALTERACAO é a única das
# quatro datas da carga populada em toda amostra testada.
CORTE_INTEGRACAO_PROPRIA = datetime(2026, 9, 10, 6, 0, 0)


def _escapar_like(termo: str) -> str:
    """Um '%' ou '_' digitado na busca não pode virar curinga do LIKE."""
    return termo.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class CissDbError(Exception):
    """Base das falhas de leitura do CISS DB2."""


class CissDbUnavailable(CissDbError):
    """Driver ausente, banco inalcançável, timeout, ou falha na consulta."""


class ReadOnlyViolation(CissDbError):
    """O SQL não é leitura pura. Erro de programação, não de usuário."""


def _limpar(sql: str) -> str:
    """Remove literais e comentários numa passada só.

    Passada única porque as três formas se aninham: um literal pode conter
    '--' e um comentário pode conter aspas. Remover em etapas separadas
    corrompe um dos dois casos, sempre.
    """
    saida: list[str] = []
    i = 0
    n = len(sql)

    while i < n:
        if sql[i] == "'":
            i += 1
            while i < n:
                if sql[i] == "'":
                    if i + 1 < n and sql[i + 1] == "'":
                        i += 2
                        continue
                    i += 1
                    break
                i += 1
            saida.append("''")
        elif sql.startswith("--", i):
            while i < n and sql[i] != "\n":
                i += 1
            saida.append(" ")
        elif sql.startswith("/*", i):
            fim = sql.find("*/", i + 2)
            i = n if fim == -1 else fim + 2
            saida.append(" ")
        else:
            saida.append(sql[i])
            i += 1

    return "".join(saida)


def validate_readonly_query(sql: str) -> None:
    """Levanta ReadOnlyViolation se o SQL não for leitura pura."""
    limpo = _limpar(sql).strip()

    if limpo.endswith(";"):
        limpo = limpo[:-1].rstrip()

    if ";" in limpo:
        raise ReadOnlyViolation("Comandos empilhados não são aceitos.")

    maiusculo = limpo.upper()

    if not _INICIO_DE_LEITURA.match(maiusculo):
        raise ReadOnlyViolation("A conexão com o CISS aceita apenas SELECT.")

    encontrados = VERBOS_PROIBIDOS & set(_PALAVRA.findall(maiusculo))
    if encontrados:
        raise ReadOnlyViolation(
            f"Comando de escrita rejeitado: {', '.join(sorted(encontrados))}"
        )


def _load_driver():
    """Importa o pyodbc na hora do uso, não na importação do módulo.

    O wheel do pyodbc exige libodbc.so.2 em tempo de import, e uma imagem
    slim pode não ter. Import no topo derrubaria a aplicação inteira —
    inclusive o login, que não depende de DB2.
    """
    try:
        import pyodbc
    except ImportError:
        logger.error("pyodbc indisponível: nenhum driver ODBC instalado neste ambiente")
        raise CissDbUnavailable from None

    return pyodbc


class CissClient:
    """Leitura do CISS DB2. Uma conexão por consulta, descartada no fim.

    Só os métodos de domínio usados pela tool Cargas Fusion — ver
    `_fetch_all`/`_fetch_one`, os tijolos privados que todo método público
    usa.
    """

    _COLUNAS_CARGA = (
        "RP.IDEMPRESA, RP.IDPLANILHA, RP.IDUSUARIO, RP.UF, RP.IDCIDADE, "
        "CID.DESCRCIDADE, RP.IDTRANSPORTADOR, RP.CPFTRANSP, RP.NOMETRANSP, "
        "RP.PLACATRANSP, RP.IDMOTORISTA, CF_MOT.NOME AS NOME_MOTORISTA, "
        "RP.DTMOVIMENTO, RP.DTCONFIRMACAO, "
        "RP.DTHORASAIDA, RP.DTALTERACAO, RP.FLAGCONFIRMADA"
    )

    # NOMETRANSP é a transportadora, não quem dirigiu — na prática é quase
    # sempre a mesma (IDTRANSPORTADOR=1294 em toda amostra vista), então
    # mostrar NOMETRANSP na listagem não diz nada útil pra distinguir uma
    # carga da outra. O nome do motorista (CLIENTE_FORNECEDOR.NOME via
    # IDMOTORISTA, mesmo IDCLIFOR que o resto do projeto já usa) é o dado
    # que importa aqui — junção direto no CISS, sem precisar do portal do
    # Fusion só pra listar.
    _JOIN_MOTORISTA = (
        "LEFT JOIN CLIENTE_FORNECEDOR CF_MOT ON CF_MOT.IDCLIFOR = RP.IDMOTORISTA "
    )

    _COLUNAS_PRE_CARGA = (
        "PC.IDPRECARGA, PC.IDEMPRESA, PC.DESCRPRECARGA, PC.DTMOVIMENTO, "
        "PC.DTALTERACAO, PC.FLAGCONFERIDA, PC.IDSTATUS, PC.IDUSUARIO"
    )

    def __init__(self, db: "CissDbConfig") -> None:
        self._db = db

    def ping(self) -> None:
        """Prova que a conexão responde. Levanta CissDbUnavailable se não.

        `SELECT 1` sozinho não é válido em DB2 — todo SELECT precisa de um
        FROM, e SYSIBM.SYSDUMMY1 é a tabela de uma linha que existe para isso.
        """
        self._fetch_one("SELECT 1 FROM SYSIBM.SYSDUMMY1")

    def get_empresas(self) -> list[dict[str, object]]:
        """Empresas cadastradas no CISS, para escolha na tela."""
        linhas = self._fetch_all(
            "SELECT IDEMPRESA, NOMEFANTASIA FROM EMPRESA ORDER BY NOMEFANTASIA"
        )
        return [
            {"id_empresa": linha["IDEMPRESA"], "nome": linha["NOMEFANTASIA"]}
            for linha in linhas
        ]

    def get_cargas(
        self,
        data_inicial: datetime,
        data_final: datetime,
        limite: int = 100,
        id_empresa: int | None = None,
    ) -> list[dict[str, object]]:
        """Cargas no período, nunca antes da migração para esta integração.

        `data_inicial` é elevado ao corte quando vier anterior a ele.
        `id_empresa` filtra para uma única empresa quando informado —
        omitido, traz todas.
        """
        inicio_efetivo = max(data_inicial, CORTE_INTEGRACAO_PROPRIA)
        filtro_empresa = "AND RP.IDEMPRESA = ? " if id_empresa is not None else ""
        params: list[object] = [inicio_efetivo, data_final]
        if id_empresa is not None:
            params.append(id_empresa)
        params.append(limite)
        return self._fetch_all(
            f"SELECT {self._COLUNAS_CARGA} "
            "FROM RETIRADA_PRODUTOS RP "
            "LEFT JOIN CIDADES_IBGE CID ON CID.IDCIDADE = RP.IDCIDADE "
            f"{self._JOIN_MOTORISTA}"
            "WHERE RP.DTALTERACAO BETWEEN ? AND ? "
            f"{filtro_empresa}"
            "ORDER BY RP.DTALTERACAO DESC "
            "FETCH FIRST ? ROWS ONLY",
            tuple(params),
        )

    def get_cargas_com_pedido_resolvido(
        self, cargas: Sequence[tuple[int, int]]
    ) -> set[tuple[int, int]]:
        """`(IDEMPRESA, IDPLANILHA)` das cargas com pelo menos um item com
        `IDORCAMENTO` resolvido via `ORCAMENTO_PRE_NOTA`.

        `IDPLANILHA` sozinho não é chave — filtro por par via `OR` de pares
        parametrizados, não `IN` de tupla composta (não portável entre
        drivers ODBC).
        """
        if not cargas:
            return set()

        condicoes = " OR ".join(["(PR.IDEMPRESA = ? AND PR.IDPLANILHA = ?)"] * len(cargas))
        params = [valor for id_empresa, id_planilha in cargas for valor in (id_empresa, id_planilha)]
        linhas = self._fetch_all(
            "SELECT DISTINCT PR.IDEMPRESA, PR.IDPLANILHA "
            "FROM PRODUTO_RETIRADO PR "
            "JOIN ORCAMENTO_PRE_NOTA OPN "
            "  ON OPN.IDEMPRESAPRENOTA = PR.IDEMPRESA AND OPN.IDPLANILHAPRENOTA = PR.IDPLANILHADOCORI "
            f"WHERE {condicoes} "
            "FOR READ ONLY",
            tuple(params),
        )
        return {(linha["IDEMPRESA"], linha["IDPLANILHA"]) for linha in linhas}

    def get_carga_detalhe(self, id_empresa: int, id_planilha: int) -> dict[str, object] | None:
        """A carga inteira, inclusive a observação (CLOB truncado — ver
        `_fetch_all`: `pyodbc` não lê CLOB direto, tipo ODBC -99)."""
        return self._fetch_one(
            f"SELECT {self._COLUNAS_CARGA}, "
            "CAST(RP.OBSERVACAO AS VARCHAR(300)) AS OBSERVACAO "
            "FROM RETIRADA_PRODUTOS RP "
            "LEFT JOIN CIDADES_IBGE CID ON CID.IDCIDADE = RP.IDCIDADE "
            f"{self._JOIN_MOTORISTA}"
            "WHERE RP.IDEMPRESA = ? AND RP.IDPLANILHA = ?",
            (id_empresa, id_planilha),
        )

    def get_itens_carga(self, id_empresa: int, id_planilha: int) -> list[dict[str, object]]:
        """Os itens da carga, com o pedido de origem, a NF real, o valor/
        peso da linha, e a descrição/dimensões do produto. Ver o arquivo
        original (meta-natal/backend/integrations/ciss.py) para o histórico
        completo das descobertas por trás desta cadeia de JOINs — resumo:
        NF real via PRODUTO_RETIRADO_NOTAS, pedido real via
        ORCAMENTO_PRE_NOTA, nenhum dos dois é IDPLANILHADOCORI direto.
        """
        return self._fetch_all(
            "SELECT "
            "PR.NUMSEQUENCIA, "
            "PR.IDPLANILHADOCORI, PR.NUMSEQUENCIAORIGEM, PR.IDSUBPRODUTO, "
            "CAST(PR.QTDPRODUTO AS DOUBLE) AS QTDPRODUTO, "
            "OPN.IDORCAMENTO, "
            "N.NUMNOTA, N.SERIENOTA, N.IDCLIFOR, "
            "NES.IDOPERACAO, NES.IDCLIFORENTREGA, "
            "CF_CLI.NOME AS NOME_CLIENTE, "
            "CAST(EA.VALTOTLIQUIDO AS DOUBLE) AS VALTOTLIQUIDO, "
            "CAST(EA.PESOBRUTO AS DOUBLE) AS PESOBRUTO, "
            "EA.IDVENDEDOR, "
            "P.DESCRCOMPRODUTO, "
            "CAST(PG.LARGURA AS DOUBLE) AS LARGURA, "
            "CAST(PG.ALTURA AS DOUBLE) AS ALTURA, "
            "CAST(PG.COMPRIMENTO AS DOUBLE) AS COMPRIMENTO, "
            "CAST(PG.PESOBRUTO AS DOUBLE) AS PRODUTO_PESOBRUTO "
            "FROM PRODUTO_RETIRADO PR "
            "LEFT JOIN PRODUTO_RETIRADO_NOTAS PRN "
            "  ON PRN.IDEMPRESA = PR.IDEMPRESA AND PRN.IDPLANILHA = PR.IDPLANILHA "
            " AND PRN.IDPRODUTO = PR.IDPRODUTO AND PRN.IDSUBPRODUTO = PR.IDSUBPRODUTO "
            " AND PRN.NUMSEQUENCIA = PR.NUMSEQUENCIA "
            "LEFT JOIN ORCAMENTO_PRE_NOTA OPN "
            "  ON OPN.IDEMPRESAPRENOTA = PR.IDEMPRESA AND OPN.IDPLANILHAPRENOTA = PR.IDPLANILHADOCORI "
            "LEFT JOIN NOTAS N "
            "  ON N.IDPLANILHA = PRN.IDPLANILHANOTA AND N.IDEMPRESA = PRN.IDEMPRESANOTA "
            "LEFT JOIN NOTAS_ENTRADA_SAIDA NES "
            "  ON NES.IDPLANILHA = PRN.IDPLANILHANOTA AND NES.IDEMPRESA = PRN.IDEMPRESANOTA "
            "LEFT JOIN ESTOQUE_ANALITICO EA "
            "  ON EA.IDEMPRESA = PRN.IDEMPRESANOTA AND EA.IDPLANILHA = PRN.IDPLANILHANOTA "
            " AND EA.NUMSEQUENCIA = PRN.NUMSEQUENCIANOTA "
            "LEFT JOIN PRODUTO_GRADE PG ON PG.IDSUBPRODUTO = PR.IDSUBPRODUTO "
            "LEFT JOIN PRODUTO P ON P.IDPRODUTO = PG.IDPRODUTO "
            "LEFT JOIN CLIENTE_FORNECEDOR CF_CLI "
            "  ON CF_CLI.IDCLIFOR = COALESCE(NES.IDCLIFORENTREGA, N.IDCLIFOR) "
            "WHERE PR.IDPLANILHA = ? AND PR.IDEMPRESA = ? "
            "ORDER BY PR.NUMSEQUENCIA",
            (id_planilha, id_empresa),
        )

    def get_pre_cargas(
        self,
        data_inicial: datetime,
        data_final: datetime,
        limite: int = 100,
        id_empresa: int | None = None,
    ) -> list[dict[str, object]]:
        """Pré-cargas no período. Sem corte de data equivalente ao de
        `get_cargas` — a integração nativa do Fusion nunca lidou com
        pré-carga."""
        filtro_empresa = "AND PC.IDEMPRESA = ? " if id_empresa is not None else ""
        params: list[object] = [data_inicial, data_final]
        if id_empresa is not None:
            params.append(id_empresa)
        params.append(limite)
        return self._fetch_all(
            f"SELECT {self._COLUNAS_PRE_CARGA} "
            "FROM PRE_CARGA PC "
            "WHERE PC.DTMOVIMENTO BETWEEN ? AND ? "
            f"{filtro_empresa}"
            "ORDER BY PC.DTMOVIMENTO DESC "
            "FETCH FIRST ? ROWS ONLY",
            tuple(params),
        )

    def get_pre_carga_detalhe(self, id_empresa: int, id_precarga: int) -> dict[str, object] | None:
        return self._fetch_one(
            f"SELECT {self._COLUNAS_PRE_CARGA} "
            "FROM PRE_CARGA PC "
            "WHERE PC.IDEMPRESA = ? AND PC.IDPRECARGA = ?",
            (id_empresa, id_precarga),
        )

    def get_itens_pre_carga(self, id_precarga: int) -> list[dict[str, object]]:
        """Os itens da pré-carga, no mesmo formato de `get_itens_carga` —
        sem carga formada ainda, pedido/cliente/valor/peso são lidos direto
        pela chave da venda (`IDEMPRESAVENDA`/`IDPLANILHAVENDA`).
        `NUMSEQUENCIA` sintetizado com `ROW_NUMBER()` (não existe em
        `PRE_CARGA_PROD`)."""
        return self._fetch_all(
            "SELECT "
            "ROW_NUMBER() OVER (ORDER BY PCP.IDPLANILHAVENDA, PCP.IDPRODUTO, "
            "PCP.IDSUBPRODUTO, PCP.NUMSEQUENCIAVENDA) AS NUMSEQUENCIA, "
            "PCP.IDPLANILHAVENDA AS IDPLANILHADOCORI, "
            "PCP.NUMSEQUENCIAVENDA AS NUMSEQUENCIAORIGEM, "
            "PCP.IDSUBPRODUTO, "
            "CAST(PCP.QTDPRODUTO AS DOUBLE) AS QTDPRODUTO, "
            "OPN.IDORCAMENTO, "
            "NES.IDCLIFORENTREGA, N.IDCLIFOR, "
            "NES.IDOPERACAO, "
            "CF_CLI.NOME AS NOME_CLIENTE, "
            "CAST(EA.VALTOTLIQUIDO AS DOUBLE) AS VALTOTLIQUIDO, "
            "CAST(EA.PESOBRUTO AS DOUBLE) AS PESOBRUTO, "
            "EA.IDVENDEDOR, "
            "P.DESCRCOMPRODUTO, "
            "CAST(PG.LARGURA AS DOUBLE) AS LARGURA, "
            "CAST(PG.ALTURA AS DOUBLE) AS ALTURA, "
            "CAST(PG.COMPRIMENTO AS DOUBLE) AS COMPRIMENTO, "
            "CAST(PG.PESOBRUTO AS DOUBLE) AS PRODUTO_PESOBRUTO "
            "FROM PRE_CARGA_PROD PCP "
            "LEFT JOIN ORCAMENTO_PRE_NOTA OPN "
            "  ON OPN.IDEMPRESAPRENOTA = PCP.IDEMPRESAVENDA AND OPN.IDPLANILHAPRENOTA = PCP.IDPLANILHAVENDA "
            "LEFT JOIN NOTAS N ON N.IDEMPRESA = PCP.IDEMPRESAVENDA AND N.IDPLANILHA = PCP.IDPLANILHAVENDA "
            "LEFT JOIN NOTAS_ENTRADA_SAIDA NES "
            "  ON NES.IDEMPRESA = PCP.IDEMPRESAVENDA AND NES.IDPLANILHA = PCP.IDPLANILHAVENDA "
            "LEFT JOIN ESTOQUE_ANALITICO EA "
            "  ON EA.IDEMPRESA = PCP.IDEMPRESAVENDA AND EA.IDPLANILHA = PCP.IDPLANILHAVENDA "
            " AND EA.NUMSEQUENCIA = PCP.NUMSEQUENCIAVENDA "
            "LEFT JOIN PRODUTO_GRADE PG ON PG.IDSUBPRODUTO = PCP.IDSUBPRODUTO "
            "LEFT JOIN PRODUTO P ON P.IDPRODUTO = PG.IDPRODUTO "
            "LEFT JOIN CLIENTE_FORNECEDOR CF_CLI "
            "  ON CF_CLI.IDCLIFOR = COALESCE(NES.IDCLIFORENTREGA, N.IDCLIFOR) "
            "WHERE PCP.IDPRECARGA = ? "
            "ORDER BY NUMSEQUENCIA",
            (id_precarga,),
        )

    def get_origem_pre_cargas_da_carga(self, id_empresa: int, id_planilha: int) -> dict[int, set[int]]:
        """Para cada pedido (`IDPLANILHADOCORI`) desta carga já formada, as
        pré-cargas de onde seus itens vieram — via
        `PRE_CARGA_PROD.IDPLANILHARETIRADA`. `{IDPLANILHADOCORI:
        {IDPRECARGA, ...}}`."""
        linhas = self._fetch_all(
            "SELECT DISTINCT PR.IDPLANILHADOCORI, PCP.IDPRECARGA "
            "FROM PRODUTO_RETIRADO PR "
            "JOIN PRE_CARGA_PROD PCP "
            "  ON PCP.IDEMPRESAVENDA = PR.IDEMPRESA AND PCP.IDPLANILHAVENDA = PR.IDPLANILHADOCORI "
            " AND PCP.IDPLANILHARETIRADA = PR.IDPLANILHA "
            "WHERE PR.IDEMPRESA = ? AND PR.IDPLANILHA = ?",
            (id_empresa, id_planilha),
        )
        origem: dict[int, set[int]] = {}
        for linha in linhas:
            origem.setdefault(linha["IDPLANILHADOCORI"], set()).add(linha["IDPRECARGA"])
        return origem

    def get_cliente_fusion(self, id_clifor: int) -> dict[str, object] | None:
        """Um cliente pelo `IDCLIFOR` exato, nas colunas que o `cliente{}`
        do `saveCarga` espera.

        `DATA_ULTIMA_COMPRA`: achado em produção (cliente `71451`, carga
        `2_22142332`) — `sendClientes` rejeita o lote inteiro sem esse
        campo (`"campos obrigatorios: data_ult_compra"`), não documentado
        antes. Sem coluna própria em `CLIENTE_FORNECEDOR`; derivado do
        `MAX(DTMOVIMENTO)` em `DBA.NOTAS` para este `IDCLIFOR` — mesma
        tabela que `get_indices_financeiros`/`get_movimentacoes_promocao`
        já usam. Cai para `DTCADASTRO` quando o cliente ainda não tem
        nenhuma nota (nunca deveria acontecer pra quem está comprando
        agora, mas sem isso o COALESCE quebraria em NULL)."""
        return self._fetch_one(
            "SELECT "
            "CF.IDCLIFOR AS CODIGO_CLIENTE, CF.NOME AS RAZAO_CLIENTE, "
            "CF.NOMEFANTASIA AS DESCR_CLIENTE, CF.CNPJCPF AS CNPJ_CLIENTE, "
            "CF.ENDERECO AS END_CLIENTE, "
            "CAST(COALESCE(CF.NUMERO, '.') AS CHAR(10)) AS NUM_END_CLIENTE, "
            "CF.BAIRRO AS BAIRRO_CLIENTE, CID.DESCRCIDADE AS CIDADE_CLIENTE, "
            "CID.UF AS UF_CLIENTE, "
            "CAST(COALESCE(CF.IDCEP, '0') AS CHAR(10)) AS CEP_CLIENTE, "
            "CF.EMAIL AS EMAIL1_CLIENTE, "
            "CAST(COALESCE(CF.FONE1, '0') AS CHAR(20)) AS TEL1_CLIENTE, "
            "CAST(COALESCE(CF.FONE2, '0') AS CHAR(20)) AS TEL2_CLIENTE, "
            "CAST(COALESCE(CF.FONECELULAR, '0') AS CHAR(20)) AS TEL3_CLIENTE, "
            "DATE(COALESCE(CF.DTCADASTRO, '2000-01-01')) AS DATA_CADASTRO_CLIENTE, "
            "DATE(COALESCE("
            "  (SELECT MAX(N.DTMOVIMENTO) FROM DBA.NOTAS N WHERE N.IDCLIFOR = CF.IDCLIFOR), "
            "  CF.DTCADASTRO, '2000-01-01'"
            ")) AS DATA_ULTIMA_COMPRA, "
            "CASE "
            "  WHEN CF.FLAGCALCULASUBSTITUICAO = 'F' THEN "
            "    CASE WHEN CF.FLAGCALCULASUBSTITUICAOFORAUF = 'F' THEN 'N' ELSE 'S' END "
            "  ELSE 'S' "
            "END AS RETEM_ICMS_CLIENTE "
            "FROM DBA.CLIENTE_FORNECEDOR CF "
            "LEFT JOIN DBA.CIDADES_IBGE CID ON CID.IDCIDADE = CF.IDCIDADE "
            "WHERE CF.IDCLIFOR = ? AND CF.FLAGINATIVO = 'F'",
            (id_clifor,),
        )

    def get_motoristas_candidatos(self, termo: str, limite: int = 50) -> list[dict[str, object]]:
        """Candidatos a motorista no CISS, para o cadastro local de
        ajudante (mesma origem de pessoa física do CISS)."""
        padrao = f"%{_escapar_like(termo)}%"
        return self._fetch_all(
            "SELECT CF.IDCLIFOR, CF.NOME, CF.CNPJCPF, "
            "COALESCE(CF.FONE1, CF.FONE2) AS TELEFONE, CF.ENDERECO, CID.UF, "
            "CID.DESCRCIDADE, CAST(COALESCE(CF.IDCEP, '0') AS CHAR(9)) AS CEP "
            "FROM DBA.CLIENTE_FORNECEDOR CF "
            "LEFT JOIN DBA.CIDADES_IBGE CID ON CID.IDCIDADE = CF.IDCIDADE "
            "WHERE CF.FLAGINATIVO = 'F' AND ("
            "UPPER(CF.NOME) LIKE UPPER(?) ESCAPE '\\' "
            "OR CAST(CF.IDCLIFOR AS VARCHAR(20)) LIKE ? ESCAPE '\\'"
            ") FETCH FIRST ? ROWS ONLY",
            (padrao, padrao, limite),
        )

    def _fetch_all(
        self, sql: str, params: Sequence[object] = (), timeout: int | None = None
    ) -> list[dict[str, object]]:
        validate_readonly_query(sql)

        driver = _load_driver()
        conexao = self._connect(driver)
        cursor = None

        try:
            if timeout is not None:
                conexao.timeout = timeout
            cursor = conexao.cursor()
            cursor.execute(sql, params)
            if cursor.description is None:
                return []
            colunas = [coluna[0] for coluna in cursor.description]
            return [
                {coluna: _normalizar_valor(valor) for coluna, valor in zip(colunas, linha)}
                for linha in cursor.fetchall()
            ]
        except driver.Error:
            logger.warning(
                "Consulta ao CISS DB2 falhou (%s em %s)", self._db.database, self._db.host
            )
            raise CissDbUnavailable from None
        finally:
            if cursor is not None:
                cursor.close()
            conexao.close()

    def _fetch_one(
        self, sql: str, params: Sequence[object] = ()
    ) -> dict[str, object] | None:
        linhas = self._fetch_all(sql, params)
        return linhas[0] if linhas else None

    def _connect(self, driver):
        try:
            return driver.connect(
                self._db.connection_string(), timeout=self._db.timeout
            )
        except driver.Error:
            logger.warning(
                "Conexão ODBC com o CISS DB2 falhou (%s em %s)",
                self._db.database,
                self._db.host,
            )
            raise CissDbUnavailable from None
