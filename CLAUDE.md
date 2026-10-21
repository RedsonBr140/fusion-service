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

### Motorista e veículo: lidos ao vivo do Fusion, sem cadastro local

Nenhum dos dois tem tabela própria. Cada um expõe o mesmo trio de funções
em `cargas_fusion/service.py` — `*_sugerido` (casa automaticamente com o
dado da carga: `IDMOTORISTA`/`PLACATRANSP`), `*_por_codigo_erp` (escolha
manual na tela) e `buscar_*_fusion` (busca por termo, picker) — todos
delegando para `integrations/fusion_portal.py`
(`FusionPortalClient.motorista_por_codigo_erp`/`buscar_motoristas` e
`veiculo_por_placa`/`veiculo_por_codigo_erp`/`buscar_veiculos`), que fala
com o **portal web**, não o SOAP.

- **Motorista**: não existe `consultarMotorista` no WSDL — por isso o
  portal é a única fonte de leitura, e `send_motoristas` (quando algo
  precisa empurrar, ex. ajudante) continua sendo upsert cego.
- **Veículo**: o SOAP tem `consultarVeiculo`/`consultarTodosVeiculos`
  (ver acima), mas o portal (`fusion-py`, `VeiculosService.buscar` contra
  `get_transportes.php`) cobre o mesmo caso sem precisar de outro cliente
  — por isso a tool usa só o portal aqui também, pela mesma simetria com
  motorista. Sem campo de filtro confirmado na grade do portal
  (`buscar_veiculos` lista tudo com `campo`/`valor` vazios): toda busca
  por placa/termo em `FusionPortalClient` lista e filtra em Python, não
  no servidor.
- **Cliente**: `_sincronizar_clientes_pendentes` tenta primeiro checar via
  portal web (`FusionPortalClient.cliente_cadastrado`, quando configurado)
  se o cliente já existe por fora; só cai pro `send_clientes` cego quando
  o portal não confirma ou não está configurado.

A grade do portal para veículo (`get_transportes.php`) não traz
`peso_max_entregas`/`volume_max_entregas`/`qtd_pallets_veiculo` — nunca
houve fonte melhor que placeholder pra esses campos (nem quando vinham do
extinto cadastro local, semeado do CISS). Mandar esses três como número
`0` cru (em vez da string `"0.000"`) é a mesma quirk do PHP `empty()`
tratando zero numérico como "não informado" (ver a quirk do "0" acima) —
sem fonte real nunca houve valor não-zero pra eles de qualquer forma, mas
`veiculo_local_para_envio_fusion` passa os três por `_numero_informado`
por precaução, nunca `.get(...) or 0` cru. **Isso não é a causa do erro
de aspas abaixo** — foi corrigido na mesma sessão, mas bisecionado contra
produção e descartado como explicação do `"Error ao no array_dados
informado..."` (ver `_sem_aspas_duplas`).

### Aspas duplas em texto livre quebram `array_dados` inteiro — não é por campo

Confirmado em produção, bisecionado item a item contra uma carga real
(`1_22142248`, `saveEntregaServico`): uma descrição de produto com aspa
dupla literal (`IDSUBPRODUTO 139919`, `'1" TIPO CUNHA'`) faz o `array_dados`
inteiro ser rejeitado com `"Error ao no array_dados informado, por favor
verifique as informacoes e o formato passados"` — mesmo a aspa estando
corretamente escapada no JSON (`\"`) antes de entrar no XML. O parser do
lado do Fusion não aponta qual campo, nem qual item do lote — a mensagem é
genérica para qualquer aspa dupla em qualquer string, em qualquer lugar do
payload (não só `descricao` — nome de cliente, endereço, qualquer texto
livre vindo do CISS pode trazer uma).

Removendo a aspa desse item isoladamente, a mesma chamada passa. Por isso
a correção vive em `integrations/fusion.py::_sem_aspas_duplas` — percorre
`array_dados` recursivamente (dict/list/str) trocando `"` por `'` em toda
string, chamada de dentro de `FusionClient._chamar_array` antes do
`json.dumps`. Corrigir só no call site da `descricao` do produto resolveria
este caso, mas não o próximo campo de texto livre que trouxer uma aspa —
por isso a sanitização é na fronteira de transporte, não no monta-payload.

O SOAP (`consultar_veiculo`/`send_veiculos` em `integrations/fusion.py`)
segue existindo como parte do contrato do `FusionClient` — mapeado no
WSDL, pode servir outro caso de uso — mas `cargas_fusion/service.py` não
chama nenhum dos dois hoje; o fluxo de veículo passou a ser só portal.

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
