# fusion-service

Serviço que busca cargas no CISSPoder (DB2, somente leitura) e integra ao
Fusion DMS (webservice SOAP + portal web). Ver `README.md` para setup,
rotas e estrutura. Este arquivo é sobre o domínio: como o Fusion exige os
dados, e as regras de negócio que não são óbvias lendo só a assinatura das
funções.

## Regras que não se negociam

- **CISS DB2 é somente leitura**, sem exceção. ODBC/`pyodbc`, só `SELECT`,
  guardado em `integrations/ciss.py::validate_readonly_query`. O `saveCarga`
  do Fusion é escrita, mas não é escrita no CISS DB2 — é uma chamada SOAP
  para outro sistema. A regra de read-only nunca falou sobre o Fusion.
- **A allow-list local (`usuarios_locais`) é consultada antes de qualquer
  chamada ao CISS.** Usuário fora dela nunca gera tráfego pro CISS.
- **Nenhum log registra senha, token ou header `Authorization`** — nem do
  CISS, nem do Fusion. `integrations/fusion.py::_chamar` loga as demais
  partes da chamada e o corpo cru da resposta, mas filtra `login`/`senha`
  via `_partes_sem_credenciais`.
- **Sem ORM, sem SQLAlchemy, sem migrations automáticas.** `sqlite3`
  nativo, queries parametrizadas, coluna nova entra em `_migrar()` em
  `database.py`.
- **`integrations/fusion.py` é a única fronteira com o SOAP do Fusion.**
  Nada com formato de cliente SOAP, XML de envelope, atravessa esse
  arquivo em direção a `cargas_fusion/` ou ao frontend — a tool chama
  `fusion_client.save_carga(payload)` e recebe dict/lista já decodificados
  do JSON de retorno, nunca o envelope. Mesmo princípio de
  `integrations/ciss_auth.py` para o CISS: webservice de terceiro, formato
  de erro instável, a camada de negócio não devia saber que o transporte é
  SOAP.

## O webservice do Fusion

SOAP 1.1, estilo RPC/encoded (não document/literal), WSDL em
`int.fusiondms.com.br/clientws/clientws.wsdl`. A maioria das chamadas de
escrita tem a mesma forma — três parâmetros: `login`, `senha`,
`array_dados` (uma string JSON serializada dentro de um parâmetro SOAP
string, não um tipo estruturado). `integrations/fusion.py::_chamar` monta
o envelope genérico; `FusionClient._chamar_array` é o wrapper para esse
padrão. Limite documentado: até 100 elementos por `array_dados` — paginar
em lotes de 100 é responsabilidade de quem chama (a tool hoje nunca bate
nesse limite por carga individual).

Excessões ao padrão `login`/`senha`/`array_dados`:
- `consultarVeiculo` recebe `placaOuId` direto, sem `array_dados` — é
  consulta de um único veículo, não lote.
- `atualizaCarga`/`cancelaCarga` usam `password` (não `senha`) e `jsonData`
  (não `array_dados`), e `jsonData` precisa ser um array mesmo com um
  elemento só. Não implementados ainda — schema incompleto, ver "Métodos
  não implementados" abaixo.

Não existe `consultarMotorista` nem `consultarCliente` no WSDL — só
`consultarVeiculo`/`consultarTodosVeiculos`. Por isso motorista e cliente
não têm "ler antes de enviar": `send_motoristas`/`send_clientes` são
upsert cego por `codigo_erp`/`codigo_cliente`, nunca duplicam mas também
nunca preservam uma edição manual feita direto no Fusion.

### O catálogo de formatos de resposta — não é estável

`_resultado_indica_sucesso` (em `cargas_fusion/service.py`) precisa
reconhecer todos estes, confirmados contra produção:

| Formato | Significado |
|---|---|
| `{"success": "8814621", "errors": []}` | sucesso, `success` como string única (id interno) |
| `{"success": "0,809283", "errors": []}` | sucesso de `saveCarga` — `success` é `"<qtd de erros>,<id interno>"` |
| `{"success": ["TESTE_..."], "errors": []}` | sucesso de `saveEntregaServico` — `success` como **array** |
| `{"success": false, "errors": [...]}` | falha de `atualizaCarga` — `success` como **booleano** |
| `{"msg": "codmotorista () ou cpf () nao encontrado..."}` | rejeição direta, sem `success`/`errors` |
| `{"erro_detalhes": [...], "success": []}` | rejeição de `saveEntregaServico` (validação de praça), `success` vazio mas não é sucesso |
| `"O Array de Dados do saveCarga tem que ser um array"` | erro genérico de formato — nem chega a ser JSON |

`integrations/fusion.py::_decodificar_resparam` nunca levanta exceção
para resposta não-JSON — devolve o texto cru, e quem chama decide o que
significa. `consultar_veiculo` trata qualquer retorno que não seja `dict`
como "não encontrado" (`None`), não como erro.

### `dt_saida` — regra fixa, não derivada do CISS

`dt_saida` do `saveCarga` nunca vem de nenhuma coluna do CISS — é sempre
`<dia atual + 1> 07:00:00`, calculado no momento do envio
(`_dt_saida_padrao` em `cargas_fusion/service.py`). Confirmado em
produção: uma carga sem `dt_saida` não aparece em nenhuma consulta por
data (`t10_data_saida` fica `0000-00-00`) nem na tela do Fusion.

**`dt_saida` só "pega" na chamada de `saveCarga` que cria a carga pela
primeira vez.** Reenviar `saveCarga` para um `codigo_erp` que já existe
não atualiza o campo, mesmo com `dt_saida` diferente — atualizar isso
depois é o que `atualizaCarga` faria (não implementado, schema
incompleto). Consequência: se um envio falhar e for reenviado, `dt_saida`
precisa estar certo desde a primeira tentativa que de fato cria a carga.

### A arquitetura de duas chamadas: `saveEntregaServico` antes de `saveCarga`

`saveCarga.entrega[]` **não tem onde carregar item de produto** — a doc
do método não tem nenhum campo `itens[]` ali dentro. Quem registra o
pedido com os itens de verdade é `saveEntregaServico`. O fluxo correto,
confirmado de ponta a ponta contra produção:

1. `save_entrega_servico` registra o pedido (`itens[]`, um por linha de
   `PRODUTO_RETIRADO` agrupada por `IDPLANILHADOCORI`). Upsert por
   `(empresa_fat, pedido_orig)`.
2. `save_carga` referencia esse pedido já registrado — `entregas_carga[]`
   aponta pro `pedido_erp`, e o `entrega[]` do próprio `saveCarga` vira um
   **stub mínimo**, não o pedido completo.

`_montar_payload` em `cargas_fusion/service.py` monta os dois payloads
juntos (`payload_carga`, `entregas_servico`); `processar_envio` decide a
ordem de chamada — `saveEntregaServico` tem que rodar primeiro.

**Os nomes de campo não são os mesmos entre os dois métodos** para o mesmo
conceito — não dá pra reaproveitar um dict de filial/cliente entre eles:

| Conceito | `saveEntregaServico` | `saveCarga` (`entrega[]`/`cliente{}`) |
|---|---|---|
| Filial fat./log./digit. | `empresa_fat`/`empresa_log`/`empresa_digit` | `codigo_erp_filial_fat`/`_log`/`_digit` |
| Praça do cliente | `praca_cod_erp`/`praca_descricao` (nível pedido) | `cliente_cod_praca_erp`/`cliente_descricao_praca` (dentro de `cliente{}`) |

### O stub mínimo de `entrega[]` dentro do `saveCarga`

`entrega[]` precisa existir e não pode ser vazio — vazio ou ausente gera o
erro genérico `"O Array de Dados do saveCarga tem que ser um array"`, que
não aponta pra causa real. O mínimo que passa validação, achado por
eliminação:

```json
{
  "pedido_erp": "<mesmo código do stub de entregas_carga>",
  "codigo_erp_filial_fat": "<IDEMPRESA>",
  "codigo_erp_filial_log": "<IDEMPRESA>",
  "codigo_erp_filial_digit": "<IDEMPRESA>",
  "cliente": {
    "filial_padrao": "<IDEMPRESA>",
    "codigo_cliente": "<codigo do cliente>"
  }
}
```

Sem o objeto `cliente` (mesmo que só com `filial_padrao`), o erro genérico
de filial acontece de novo — o `saveCarga` lê `cliente.filial_padrao` como
parte da checagem de filial, não só `codigo_erp_filial_*`.

### A quirk do "0" — strings numéricas nunca podem ser `"0"`

O Fusion (PHP) trata a string `"0"` como vazia (`empty("0")` é `true` em
PHP, mas `empty("0.000")` é `false`) — um campo numérico "preenchido" com
`"0"` ainda conta como "não informado" na validação deles. Por isso:

- `_numero_informado` formata peso/cubagem/preço sempre com `.3f`
  (`"0.000"`, nunca `"0"`), usado em todo campo numérico obrigatório que o
  CISS pode não ter (produto sem peso/dimensão cadastrado).
- Placeholders de praça/rota (`cliente_cod_praca_erp`, `rota_cod_erp`, ...)
  usam `"1"`, nunca `"0"` — confirmado em produção que `"0"` continua
  dando o erro de praça/rota "não cadastrada/não informada" mesmo
  preenchido.

### Praça e rota — sem fonte real no CISS

O Fusion rejeita uma entrega sem `praca_cod_erp`/`cliente_cod_praca_erp`
preenchido (erro: `"Praca do Cliente[...] nao cadastrada... e Praca do
Pedido[...] nao informada"`), mas a tool não rastreia praça/rota reais —
usa sempre o placeholder (`"1"`/`"."`). Se um cliente real precisar de
praça/rota de verdade no Fusion, falta achar de onde viria no CISS; hoje
não bloqueia porque qualquer valor "preenchido" satisfaz a validação.

### Sincronização de cadastro: veículo lê antes, motorista nunca lê

Estratégia (`sincronizar_veiculo`/`_sincronizar_pessoa` em
`cargas_fusion/service.py`): consultar o Fusion primeiro; se existir, usar
o dado de lá; se não existir, semear do CISS/cadastro local e enviar.

- **Veículo**: existe `consultar_veiculo` — o fluxo lê antes de decidir,
  sem risco de sobrescrever edição manual feita direto no Fusion.
- **Motorista**: não existe `consultarMotorista` — `send_motoristas` é
  sempre upsert cego. **Risco aceito, não bug**: se alguém edita a ficha
  de um motorista manualmente no Fusion e a tool sincroniza de novo com o
  dado do CISS, essa edição manual é perdida.
- **Cliente**: `_sincronizar_clientes_pendentes` tenta primeiro checar via
  portal web (`FusionPortalClient.cliente_cadastrado`, quando configurado)
  se o cliente já existe por fora; só cai pro `send_clientes` cego quando
  o portal não confirma ou não está configurado.

O shape de leitura do Fusion é **camelCase** (`consultarVeiculo`:
`codigoErp`, `anoModelo`, `pesoMaximoEntregas`, ...) e o shape de escrita é
majoritariamente **snake_case** (`sendVeiculos`/`saveCarga`:
`codigo_erp`, `peso_max_entregas`, mas `anoModelo`/`anoFabricacao`
continuam camelCase mesmo no envio) — os nomes não são a mesma string com
casing diferente, alguns mudam de fato
(`peso_max_entregas` ≠ `pesoMaximoEntregas`). `_veiculo_fusion_para_local`
é a tradução leitura → cadastro local; `veiculo_local_para_envio_fusion` é
cadastro local → envio. Não reaproveitar um dicionário entre as duas
direções.

`consultarVeiculo` também é sensível a formato de placa: uma consulta sem
hífen pode devolver `"Veiculo nao encontrado!"` (texto puro, não JSON)
onde a mesma placa com hífen devolve o JSON normal — não há regra geral
conhecida aqui, só o fato observado. Por isso qualquer resposta que não
seja `dict` é "não encontrado", nunca `FusionUnavailable`.

### Cliente: o cadastro precisa existir antes do pedido

O `cliente{}` embutido no stub de `entrega[]`/`saveCarga` **não cria** o
cadastro no Fusion, só referencia um já existente — sem `sendClientes`
antes, a entrega é rejeitada com `"Cliente ... nao foi localizado"`.
`fusion_clientes_sincronizados` marca quem já foi empurrado, pra não
reenviar o cadastro em todo envio. O shape de `sendClientes` não é o mesmo
do `cliente{}` do stub — ver `_cliente_para_sendclientes` vs.
`_cliente_stub_para_fusion` (a exceção de nome é `cnpj_cpf_cliente` vs.
`CNPJ_CLIENTE`).

### Pedido sem orçamento vinculado não entra no envio

Cada "entrega" dentro de uma carga é um pedido inteiro (agrupado por
`IDPLANILHADOCORI`), não uma linha de produto. O `pedido_erp` usa
`IDORCAMENTO` (o orçamento real, resolvido via `ORCAMENTO_PRE_NOTA`) como
número de pedido, não `IDPLANILHADOCORI` (que é a pré-nota interna, não o
pedido nem a NF real que o cliente recebe). Quando `IDORCAMENTO` vem
`NULL` — pedido sem orçamento vinculado — a entrega fica de fora do envio
inteiro (nem `saveCarga`, nem `saveEntregaServico`), com aviso não
bloqueante; o resto da carga segue normalmente.

Convenção de código, fixada: `pedido_erp` = `f"{IDORCAMENTO}_{sufixo}"`,
onde o sufixo é `id_planilha` da carga (carga real) ou um código próprio
de pré-carga — resolve a ambiguidade de um mesmo pedido reaparecer em mais
de uma carga ao longo do tempo (redespacho). O mesmo código precisa ser
usado em `saveEntregaServico` e no stub de `saveCarga` para os três lados
baterem (`codigo_pedido`/`codigo_pedido_generico` em
`cargas_fusion/service.py`).

### Ajudante: não existe vínculo carga↔ajudante no CISS

`codajudante1..4` do `saveCarga` fica sempre vazio do lado desta tool — o
CISS genuinamente não rastreia esse papel (nenhuma tabela liga
`RETIRADA_PRODUTOS` a um ajudante). Não é lacuna de levantamento, é fato
do schema. Ajudante só existe como cadastro local
(`fusion_ajudantes`) + `send_motoristas([...], tipo="Ajudante")` — se
atribuição de ajudante importar de verdade, provavelmente é feita dentro
do próprio Fusion, fora do escopo deste serviço.

### Corte de data da busca de cargas

A busca de cargas (`get_cargas`) ignora tudo antes de
`2026-09-10 06:00:00`, filtrando por `DTALTERACAO` (a única das quatro
datas candidatas populada em toda a amostra testada, inclusive cargas
antigas). Cargas antes desse corte pertenciam à integração nativa do
próprio Fusion (CISSPoder ↔ Fusion sem passar por este serviço) — não são
do fluxo que este serviço gerencia, e têm dados inconsistentes
(`IDMOTORISTA` às vezes aponta pra um cadastro que não é de motorista;
`PLACATRANSP` em formato inconsistente) justamente por serem de outro
sistema, não por serem "dado ruim".

## Métodos do WSDL não implementados

`atualizaCarga`/`cancelaCarga` (atualizar `dt_saida`/motorista/veículo ou
cancelar uma carga já enviada) — schema de `jsonData` não mapeado por
completo. Testes em produção devolveram listas de campo esperado
incompletas ou inconsistentes com o nome usado em `saveCarga`
(`cod_motorista`/`cod_veiculo`, não `codmotorista`/`codveiculo`). Não
bloqueia o envio inicial; só entra em cena se "editar/cancelar carga já
enviada" virar escopo.

## Pendências de dado conhecidas

- `preco`/`subtotal` dos itens de `saveEntregaServico`: `subtotal` vem de
  `VALTOTLIQUIDO`, `preco` é derivado (`VALTOTLIQUIDO / QTDPRODUTO`) — sem
  fonte unitária confirmada independente desse cálculo.
- Praça/rota reais (ver acima) — placeholder funciona, sem fonte no CISS.
- Campos de filial do stub de `saveCarga` só testados com filial de teste;
  não testado se um cliente real (CPF/CNPJ, endereço) exige mais campos no
  `cliente{}` do que o mínimo documentado acima.
