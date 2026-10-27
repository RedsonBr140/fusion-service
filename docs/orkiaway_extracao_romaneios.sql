-- ============================================================================
-- OrkiaWay — Extração de Romaneios/Cargas do CISS (DB2)
-- Especificação: "Especificacao_Tecnica_OrkiaWay_CISS" v1.0, Outubro/2026
--
-- Granularidade exigida pelo conector: 1 linha por item de pedido. O
-- OrkiaWay agrupa em memória (1 Carga -> N Pedidos -> M Itens), então esta
-- consulta parte de PRODUTO_RETIRADO (item), não de RETIRADA_PRODUTOS
-- (cabeçalho da carga) — o inverso do que as consultas de
-- integrations/ciss.py fazem para a tela desta app (que paginam carga por
-- carga). Os aliases AS abaixo são os nomes exigidos pelo parser do
-- conector — não renomear.
--
-- Esta consulta reaproveita a mesma cadeia de JOINs e as mesmas regras de
-- negócio já descobertas e documentadas em CLAUDE.md para a integração
-- Fusion deste serviço (mesmo CISS, mesmo schema, mesmas armadilhas) —
-- apenas remapeada para o dicionário de campos que o OrkiaWay exige. Ver
-- integrations/ciss.py::get_itens_carga/get_cargas para a implementação
-- Python equivalente usada pela tool Cargas Fusion.
--
-- Parâmetros (placeholders '?'): janela incremental por RP.DTALTERACAO —
-- a mesma coluna que integrations/ciss.py usa para paginar cargas, porque é
-- a única das quatro datas candidatas da carga populada em toda a amostra
-- testada em produção (DTMOVIMENTO/DTCONFIRMACAO/DTHORASAIDA podem ficar
-- nulas). Equivalente ao "CURRENT DATE - 15 DAYS" sugerido no PDF — troque
-- os dois '?' por CURRENT TIMESTAMP - 15 DAYS e CURRENT TIMESTAMP se
-- preferir fixo em vez de parametrizado.
--
-- Esta consulta é só leitura (SELECT), compatível com a regra deste
-- projeto de que o CISS DB2 é somente leitura sem exceção.
-- ============================================================================

SELECT
    -- ------------------------------------------------------------------
    -- A. Dados do Romaneio / Carga
    -- ------------------------------------------------------------------
    -- Mesmo código de carga usado em toda a tool Cargas Fusion
    -- (cargas_fusion/service.py::codigo_carga -> f"{id_empresa}_{id_planilha}").
    CAST(RP.IDEMPRESA AS VARCHAR(10)) || '_' || CAST(RP.IDPLANILHA AS VARCHAR(20))
                                            AS NUMERO_CARGA,
    RP.DTMOVIMENTO                         AS DATA_EMISSAO_ROMANEIO,
    RP.DTHORASAIDA                         AS DATA_SAIDA_ROMANEIO,
    RP.DTCONFIRMACAO                       AS DATA_FECHAMENTO_ROMANEIO,
    -- Sem coluna agregada por carga no CISS (RETIRADA_PRODUTOS não guarda
    -- peso/volume/valor/qtd totais) — o próprio PDF já prevê isso: "O
    -- Conector OrkiaWay realiza o agrupamento automático em memória". Os
    -- totais nascem da soma de QTD_VENDA_ITEM/PESO_BRUTO_ITEM/
    -- PRECO_VENDA_ITEM por NUMERO_CARGA no lado do conector, não aqui.
    CAST(NULL AS DECIMAL(12,3))            AS PESO_CARGA,
    CAST(NULL AS DECIMAL(12,3))            AS VOLUME_CARGA,
    CAST(NULL AS DECIMAL(14,2))            AS TOTAL_CARGA,
    CAST(NULL AS INT)                      AS QTD_ITENS_CARGA,

    -- ------------------------------------------------------------------
    -- B. Dados do Veículo e Motorista
    -- ------------------------------------------------------------------
    RP.PLACATRANSP                         AS PLACA,
    -- CF_MOT = CLIENTE_FORNECEDOR via RP.IDMOTORISTA, mesma junção de
    -- integrations/ciss.py::_JOIN_MOTORISTA. RP.NOMETRANSP é fallback para
    -- quando IDMOTORISTA não resolve (ex. carga antiga, fora do que este
    -- projeto trata como "fluxo Fusion" — ver corte de data no WHERE).
    COALESCE(CF_MOT.NOME, RP.NOMETRANSP)   AS MOTORISTA,
    COALESCE(CF_MOT.CNPJCPF, RP.CPFTRANSP) AS CPF_MOTORISTA,
    COALESCE(CF_MOT.FONECELULAR, CF_MOT.FONE1, CF_MOT.FONE2)
                                            AS TELEFONE_MOTORISTA,
    -- Sem fonte no CISS: este serviço não tem cadastro de veículo próprio
    -- nem tabela de veículo no CISS — ver CLAUDE.md "Motorista e veículo:
    -- lidos ao vivo do Fusion, sem cadastro local". Descrição/marca/
    -- indicador de frota própria/tara só existem no portal do Fusion
    -- (FusionPortalClient), não no CISS DB2. Se o OrkiaWay precisar
    -- destes campos de verdade, a fonte é o portal Fusion, não esta
    -- consulta.
    CAST(NULL AS VARCHAR(100))             AS DESCRICAO_VEICULO,
    CAST(NULL AS VARCHAR(50))              AS MARCA_VEICULO,
    CAST(NULL AS CHAR(1))                  AS PROPRIO,
    CAST(NULL AS DECIMAL(12,3))            AS PESO_TARA_VEICULO,

    -- ------------------------------------------------------------------
    -- C. Dados do Cliente / Destinatário
    -- ------------------------------------------------------------------
    -- CF_CLI resolvido pela mesma cadeia de integrations/ciss.py::
    -- get_itens_carga (via NOTAS_ENTRADA_SAIDA.IDCLIFORENTREGA, com
    -- fallback para NOTAS.IDCLIFOR) — é o único caminho de cliente que
    -- este schema expõe por item. Atenção: esse caminho passa por NOTAS,
    -- que só existe depois que o item foi efetivamente faturado — um item
    -- ainda sem nota fiscal emitida vem com os campos de cliente nulos
    -- apesar de OBRIGATÓRIO no dicionário do OrkiaWay. Descomente o filtro
    -- "AND N.IDCLIFOR IS NOT NULL" no WHERE se o conector não tolerar isso.
    CF_CLI.CNPJCPF                         AS CNPJ_CLIENTE,
    CF_CLI.NOME                            AS CLIENTE,
    CF_CLI.NOMEFANTASIA                    AS FANTASIA,
    CF_CLI.ENDERECO                        AS LOGRADOURO,
    CAST(CF_CLI.NUMERO AS VARCHAR(20))     AS NUMERO_LOGRADOURO,
    CF_CLI.BAIRRO                          AS BAIRRO,
    CID_CLI.DESCRCIDADE                    AS CIDADE,
    CID_CLI.UF                             AS UF,
    CAST(CF_CLI.IDCEP AS VARCHAR(10))      AS CEP,
    -- Sem coluna equivalente conhecida em CLIENTE_FORNECEDOR.
    CAST(NULL AS VARCHAR(150))             AS PONTO_REFERENCIA,
    CF_CLI.FONE1                           AS TEL1_CLIENTE,
    CF_CLI.EMAIL                           AS EMAIL_CLIENTE,

    -- ------------------------------------------------------------------
    -- D. Dados do Pedido e Itens Faturados
    -- ------------------------------------------------------------------
    -- IDORCAMENTO é o pedido real (D11 em CLAUDE.md) — nunca
    -- IDPLANILHADOCORI, que é só a pré-nota interna. Resolvido via
    -- ORCAMENTO_PRE_NOTA, mesma junção que integrations/ciss.py usa em
    -- get_carga_por_pedido/get_itens_carga. O JOIN é INNER (não LEFT): um
    -- item sem IDORCAMENTO (pedido sem orçamento vinculado) fica de fora
    -- da extração inteira, mesma regra de "Pedido sem orçamento vinculado
    -- não entra no envio" já documentada para a integração Fusion.
    CAST(OPN.IDORCAMENTO AS VARCHAR(20))   AS NUMPED,
    N.NUMNOTA                              AS NUMNOTA,
    CAST(PR.IDSUBPRODUTO AS VARCHAR(20))   AS CODPROD,
    P.DESCRCOMPRODUTO                      AS DESCRICAO,
    CAST(PR.QTDPRODUTO AS DECIMAL(12,3))   AS QTD_VENDA_ITEM,
    -- Preço unitário de catálogo (bruto) — mesma fonte/fallback que
    -- cargas_fusion/service.py::_preco_item usa: VALUNITBRUTO quando
    -- preenchido, senão VALTOTLIQUIDO / QTDPRODUTO (fallback confirmado
    -- necessário em ~0.45% das linhas de ESTOQUE_ANALITICO).
    CAST(
        CASE WHEN COALESCE(EA.VALUNITBRUTO, 0) <> 0
             THEN EA.VALUNITBRUTO
             ELSE EA.VALTOTLIQUIDO / NULLIF(PR.QTDPRODUTO, 0)
        END
    AS DECIMAL(14,2))                      AS PRECO_VENDA_ITEM,
    -- Grão desta consulta é o item, não o pedido — este valor é o
    -- subtotal líquido desta linha (EA.VALTOTLIQUIDO), não o total do
    -- pedido inteiro. Some por NUMPED no conector se precisar do total do
    -- pedido.
    CAST(EA.VALTOTLIQUIDO AS DECIMAL(14,2)) AS VALOR_TOTAL_PEDIDO,
    -- Mesmo cálculo de cargas_fusion/service.py::_peso_item: PESOBRUTO do
    -- movimento (ESTOQUE_ANALITICO) quando não é zero — é dado real da
    -- transação — senão cai para o peso de catálogo (PRODUTO_GRADE, por
    -- unidade) x quantidade. Achado em produção: PESOBRUTO do movimento
    -- vem zerado em ~96% das linhas, não é "produto sem peso cadastrado".
    CAST(
        CASE WHEN COALESCE(EA.PESOBRUTO, 0) <> 0
             THEN EA.PESOBRUTO
             ELSE PG.PESOBRUTO * PR.QTDPRODUTO
        END
    AS DECIMAL(12,3))                      AS PESO_LIQ_PEDIDO,
    -- Sem coluna de unidade de medida confirmada neste schema reduzido
    -- (PRODUTO/PRODUTO_GRADE, como usados pela tool Cargas Fusion, não
    -- expõem uma). Preencher se o DBA localizar a coluna certa.
    CAST(NULL AS VARCHAR(10))              AS UNIDADE_PRODUTO,
    -- Peso de catálogo por unidade (não multiplicado por quantidade,
    -- diferente de PESO_LIQ_PEDIDO acima) — é o que o dicionário pede
    -- para PESO_BRUTO_ITEM ("peso bruto unitário").
    CAST(PG.PESOBRUTO AS DECIMAL(12,3))    AS PESO_BRUTO_ITEM,
    -- EA.IDVENDEDOR só traz o código do vendedor — nenhuma tabela de
    -- vendedor (nome) é usada em nenhuma consulta existente deste
    -- projeto, não há de onde confirmar o join. Trocar por um JOIN real
    -- se o DBA souber a tabela de vendedores do ambiente.
    CAST(NULL AS VARCHAR(100))             AS VENDEDOR

FROM PRODUTO_RETIRADO PR
-- Mesmo filtro de integrations/ciss.py::get_itens_carga: só item de local
-- de retirada com a integração Fusion marcada. Uma carga pode ter itens
-- de locais diferentes (ex. um item retirado no balcão + outros para
-- entrega de verdade) — o corte é por item, não por carga inteira.
JOIN LOCAL_RETIRADA LR_ITEM
  ON LR_ITEM.IDLOCALRETIRADA = PR.IDLOCALRETIRADA
 AND LR_ITEM.FLAGUTILIZAINTEGRACAOFUSION = 'T'
-- Cabeçalho da carga. OR com IDEMPRESABAIXAEST: entrega entre filiais —
-- o item pode estar gravado sob um IDEMPRESA diferente do cabeçalho
-- (achado em produção, carga 2_22144917 e ~3.5 mil casos na amostra
-- testada). Ver _FILTRO_FUSION_CARGA / get_itens_carga em
-- integrations/ciss.py para o histórico completo desta descoberta.
JOIN RETIRADA_PRODUTOS RP
  ON RP.IDPLANILHA = PR.IDPLANILHA
 AND (RP.IDEMPRESA = PR.IDEMPRESA OR RP.IDEMPRESA = PR.IDEMPRESABAIXAEST)
LEFT JOIN CLIENTE_FORNECEDOR CF_MOT ON CF_MOT.IDCLIFOR = RP.IDMOTORISTA
-- Pedido real (ver nota de NUMPED acima) — INNER: exclui item sem
-- orçamento vinculado.
JOIN ORCAMENTO_PRE_NOTA OPN
  ON OPN.IDEMPRESAPRENOTA = PR.IDEMPRESA AND OPN.IDPLANILHAPRENOTA = PR.IDPLANILHADOCORI
-- Cadeia até a NF real e o valor/peso de movimento — idêntica a
-- get_itens_carga.
LEFT JOIN PRODUTO_RETIRADO_NOTAS PRN
  ON PRN.IDEMPRESA = PR.IDEMPRESA AND PRN.IDPLANILHA = PR.IDPLANILHA
 AND PRN.IDPRODUTO = PR.IDPRODUTO AND PRN.IDSUBPRODUTO = PR.IDSUBPRODUTO
 AND PRN.NUMSEQUENCIA = PR.NUMSEQUENCIA
LEFT JOIN NOTAS N
  ON N.IDPLANILHA = PRN.IDPLANILHANOTA AND N.IDEMPRESA = PRN.IDEMPRESANOTA
LEFT JOIN NOTAS_ENTRADA_SAIDA NES
  ON NES.IDPLANILHA = PRN.IDPLANILHANOTA AND NES.IDEMPRESA = PRN.IDEMPRESANOTA
LEFT JOIN ESTOQUE_ANALITICO EA
  ON EA.IDEMPRESA = PRN.IDEMPRESANOTA AND EA.IDPLANILHA = PRN.IDPLANILHANOTA
 AND EA.NUMSEQUENCIA = PRN.NUMSEQUENCIANOTA
LEFT JOIN PRODUTO_GRADE PG ON PG.IDSUBPRODUTO = PR.IDSUBPRODUTO
LEFT JOIN PRODUTO P ON P.IDPRODUTO = PG.IDPRODUTO
LEFT JOIN CLIENTE_FORNECEDOR CF_CLI
  ON CF_CLI.IDCLIFOR = COALESCE(NES.IDCLIFORENTREGA, N.IDCLIFOR)
LEFT JOIN CIDADES_IBGE CID_CLI ON CID_CLI.IDCIDADE = CF_CLI.IDCIDADE

WHERE RP.DTALTERACAO >= ?   -- início da janela incremental (ex.: CURRENT TIMESTAMP - 15 DAYS)
  AND RP.DTALTERACAO <  ?   -- fim da janela incremental (ex.: CURRENT TIMESTAMP)
  -- Descomente para nunca trazer item sem cliente resolvido (ver nota em
  -- C. Dados do Cliente acima):
  -- AND N.IDCLIFOR IS NOT NULL

ORDER BY RP.IDPLANILHA DESC, PR.NUMSEQUENCIA ASC;

-- ============================================================================
-- Recomendações (equivalentes às do PDF, já aplicadas/válidas neste schema)
--
-- Desempenho: RETIRADA_PRODUTOS.DTALTERACAO já é a coluna de corte usada
-- por integrations/ciss.py::get_cargas — garanta índice nela. PRODUTO_RETIRADO
-- é a tabela de maior volume aqui (grão de item); IDPLANILHA/IDEMPRESA já
-- deveriam ter índice por serem chave de junção nativa do CISS.
--
-- Segurança: mesmo princípio de leitura deste projeto — o usuário do
-- OrkiaWay só precisa de GRANT SELECT nas tabelas acima (ou na VIEW, se o
-- DBA preferir materializar esta consulta como view).
-- ============================================================================
