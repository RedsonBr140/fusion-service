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

### Peso dos itens: `ESTOQUE_ANALITICO.PESOBRUTO` quase nunca tem valor

`PESOBRUTO` (o peso do movimento/venda, usado desde a spec original)
vinha `0` em **toda** linha de uma carga real testada (`1_22142248`, 10
itens) — amostra de 500k linhas de `ESTOQUE_ANALITICO` nesta base: só
~3.6% têm `PESOBRUTO` não-zero. Não é "produto sem peso cadastrado" como
a spec original assumiu — é a coluna certa estar vazia quase sempre.
`PRODUTO_GRADE.PESOBRUTO` (`PRODUTO_PESOBRUTO` no retorno de
`get_itens_carga`/`get_itens_pre_carga`) é o peso de catálogo, **por
unidade** da grade — precisa de `× QTDPRODUTO` pra virar peso de linha,
mesmo tratamento que `LARGURA`/`ALTURA`/`COMPRIMENTO` (mesma tabela) já
recebem no cálculo de cubagem. `_peso_item` em `cargas_fusion/service.py`
usa `PESOBRUTO` do movimento quando não é zero (é dado real de uma
transação, teria prioridade), senão cai pro catálogo × quantidade. O
frontend replica o mesmo cálculo (`pesoEfetivo` em
`BuscaCargasSection.tsx`) só pra exibir o peso coerente com o que
realmente vai no payload — nunca lê `PESOBRUTO` cru pra mostrar peso.

### Cliente da entrega e destino com nome do cliente

`get_itens_carga`/`get_itens_pre_carga` ganham `NOME_CLIENTE` via `LEFT
JOIN CLIENTE_FORNECEDOR` por `COALESCE(IDCLIFORENTREGA, IDCLIFOR)` — o
mesmo cliente que `_cliente_stub_para_fusion`/`sendClientes` já resolvem,
só que exposto por item pra tela mostrar de quem é cada pedido.

Quando todo pedido de uma carga é do mesmo cliente (caso comum — uma
única entrega, ou várias entregas da mesma obra), `_montar_payload`
anexa o nome ao `destino` do `saveCarga`: `"TRIUNFO/PE - JAF CONSTRUCAO E
CONSULTORIA EIRELI"`. Mais de um cliente na carga não tem nome único pra
anexar — o destino fica só cidade/UF, como antes. `RAZAO_CLIENTE`
(`CLIENTE_FORNECEDOR.NOME`) é a fonte do nome, mesma usada em
`_cliente_para_sendclientes`.

**Confirmado em produção: `t10_destino` trunca em exatamente 45
caracteres, sem erro.** Testado com uma carga de teste
(`TESTE_CLAUDE_DESTINO_CARGA_001`, id interno `813904`, inerte — tentei
cancelar, mesmo problema de schema não mapeado de `cancelaCarga` já
documentado abaixo): mandei 221 caracteres de `destino`,
`detalheCarga` devolveu só os 45 primeiros, sem aviso nem erro — Fusion
simplesmente descarta o resto. Isso significa que `"TRIUNFO/PE - JAF
CONSTRUCAO E CONSULTORIA EIRELI"` (48 caracteres) chegaria cortado como
`"TRIUNFO/PE - JAF CONSTRUCAO E CONSULTORIA EIR"` — perde o fim do nome
sem ninguém saber, a não ser abrindo a carga no Fusion. Esta tool não
faz truncamento nem priorização própria entre cidade e nome do cliente
hoje — fica para quem decidir qual das duas partes importa mais quando
não cabem as duas.

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

### Filtro por local de retirada com integração Fusion marcada

`LOCAL_RETIRADA.FLAGUTILIZAINTEGRACAOFUSION` — achado em produção: das
20523 cargas desde o corte de integração (ver acima), só ~10.6% vêm de um
local de retirada marcado `'T'` nessa coluna. Os outros ~89.4% são
locais nomeados `"FR CAIXA ..."` (frente de caixa — venda balcão, cliente
leva o produto na hora) — sem motorista, veículo ou entrega nenhuma,
nunca deveriam aparecer numa tela de roteirização. **Nenhuma carga desde
o corte mistura locais com flags diferentes entre seus itens — mas isso
não é garantido pelo schema, só pela prática atual** (ver filtro por item
abaixo).

`PRODUTO_RETIRADO.IDLOCALRETIRADA` é a chave pra carga real (a própria
`RETIRADA_PRODUTOS` não tem essa coluna, só o item). Pré-carga usa
`PRE_CARGA_PROD.IDPONTORETIRADA` — nome de coluna diferente, mas mesmo
espaço de id de `LOCAL_RETIRADA.IDLOCALRETIRADA` (confirmado comparando
amostras). `get_cargas`/`get_pre_cargas` filtram por padrão
(`apenas_fusion=True`, via `EXISTS` correlacionado — nunca `JOIN` direto,
que duplicaria linha por item) — `_FILTRO_FUSION_CARGA`/
`_FILTRO_FUSION_PRE_CARGA` em `integrations/ciss.py`. A tela expõe isso
como checkbox ("Só cargas com integração Fusion", marcado por padrão) —
desmarcar mostra tudo, pro caso de uma carga esperada não aparecer.

### Filtro por item: uma carga pode ter itens de locais diferentes

Achado em produção, histórico (`IDORCAMENTO` `50369`/`50508`, cargas
`1_6329109` e `8_6332886`): `PRODUTO_RETIRADO.IDLOCALRETIRADA` pode
variar **por item dentro da mesma carga, mesma NF** — ex. um item
retirado no balcão ("FR CAIXA", porque aquele produto já estava
disponível e o cliente levou na hora) junto de outros itens da mesma
venda que ficaram pendentes pra entrega de verdade, retirados depois sob
a mesma carga/retirada. Não achado desde o corte de integração (processo
atual parece não misturar mais), mas o schema não impede — é só prática
observada, não garantia.

Isso importa porque `_montar_payload` soma `VALTOTLIQUIDO`/peso de **todo
item que `get_itens_carga` devolver** pra aquele pedido — um item "FR
CAIXA" senderia valor/peso de algo que já foi entregue no balcão, nunca
carregado no caminhão desta carga, inflando o que o Fusion recebe como
"enviado". Por isso `get_itens_carga`/`get_itens_pre_carga` filtram por
item (`JOIN LOCAL_RETIRADA ... FLAGUTILIZAINTEGRACAOFUSION = 'T'`, não
`EXISTS` como o filtro de carga acima) — um item de local sem a
integração nunca aparece na lista de itens, nunca entra em
`entregas_servico`/`entrega[]`, nunca é somado em valor/peso/cubagem.

Importante: quando um mesmo pedido (`IDORCAMENTO`) é entregue em mais de
uma carga ao longo do tempo (redespacho, entrega parcial — confirmado em
produção, `IDORCAMENTO` `601424` em três cargas distintas), `valor`/`peso`
somados pra cada carga **já são só a fração daquele pedido que está
física nesta carga** — `get_itens_carga(id_empresa, id_planilha)` só
devolve as linhas de `PRODUTO_RETIRADO` daquela carga específica, nunca o
pedido inteiro. Isso é intencional: o que importa pro Fusion é quanto
está sendo enviado por este caminhão agora, não o valor total da venda
original.

### Aviso bloqueante vs. informativo — não travar a carga toda por um pedido já coberto

Pergunta que motivou isto: se uma pré-carga já foi enviada manualmente e
depois o CISS forma a carga de verdade a partir dela, a carga vai
aparecer pra reenviar e duplicar no Fusion? Resposta curta: o pedido não
duplica — `pedidos_cobertos_por_pre_carga`/`get_origem_pre_cargas_da_carga`
já resolviam isso achando o `IDPRECARGA` original via
`PRE_CARGA_PROD.IDPLANILHARETIRADA` e passando os `IDPLANILHADOCORI`
correspondentes como `pedidos_excluidos` pra `montar_payload_carga` — um
pedido excluído nunca entra em `entrega[]`/`entregas_servico`, então
nunca vira uma segunda chamada de `saveCarga`/`saveEntregaServico` pro
mesmo pedido.

O problema era outro, e só importa pro **envio automático** (pré-carga
continua manual): `_montar_payload` tratava **qualquer** aviso não vazio
como bloqueio total do envio, inclusive o aviso puramente informativo
`"Pedido {id} já enviado ao Fusion como pré-carga — não reenviado."` (e o
já existente de pedido sem orçamento). Duas consequências ruins:

1. Uma carga com cobertura **parcial** (um pedido já foi na pré-carga,
   outro é novo) nunca enviava nem o pedido genuinamente novo — o aviso
   do pedido já coberto travava a carga inteira.
2. Uma carga **100% coberta** por pré-carga ficava presa em `PENDENTE`
   pra sempre, incrementando `tentativas` a cada reprocessamento — nada
   de errado está acontecendo (tudo que havia pra mandar já foi mandado
   por outro canal), mas um sender automático reprocessaria isso
   indefinidamente como falha.

A correção: `_montar_payload` agora devolve um quarto valor,
`avisos_bloqueantes` (subconjunto de `avisos`), via um helper interno
`_aviso(texto, *, bloqueante=True)`. Só dois avisos são
`bloqueante=False`: pedido já coberto por pré-carga, e pedido sem
orçamento vinculado (D11, já era não bloqueante na intenção, nunca na
implementação). A checagem de motorista/veículo ausente também passou a
só rodar quando `entrega_stubs` não está vazio — sem nada pra anexar
motorista/veículo, a ausência deles não é um problema de verdade.

`processar_envio`/`processar_envio_pre_carga` ganharam um parâmetro
`avisos_bloqueantes` e trocaram o gate de `if avisos:` para `if
avisos_bloqueantes:` (a lista completa de `avisos` continua guardada na
fila/motivo pra quem for olhar o que aconteceu). Logo depois do gate, um
atalho novo: se `payload_carga["entrega"]` ficou vazio (nada pra mandar,
e nada bloqueante), marca `ENVIADA` direto — `_marcar_enviada_na_fila`/
`_marcar_enviada_na_fila_pre_carga` — sem chamar o Fusion nenhuma vez.

Esse atalho só rodava quando alguém chamava `processar_envio` de verdade
— ou seja, só depois de abrir a carga e clicar "enviar" manualmente. Uma
carga 100% coberta por pré-carga continuava aparecendo como pendente na
listagem (`GET /api/cargas`) até alguém fazer esse clique, mesmo não
havendo nada de fato pra decidir. `marcar_carga_coberta_automaticamente`
(`cargas_fusion/service.py`) resolve isso na própria listagem: pra cada
carga ainda não marcada `ENVIADA`, busca os itens de verdade e a origem
de pré-carga, monta o payload (com `clientes_por_idclifor={}` e
motorista/veículo `None` — seguro aqui porque essas checagens só rodam
pra um pedido que não foi pulado; se `entrega` ficar vazio, nenhum pedido
chegou lá) e, só se `entrega` vazio e nada bloqueante, chama
`processar_envio` (com `fusion_client=None`, também seguro: o atalho de
"nada pra enviar" roda antes de qualquer checagem que precisasse do
client de verdade) pra marcar `ENVIADA` e tirar a carga da lista sem
esperar ação manual. Nunca dispara envio real ao Fusion a partir da
listagem — só decide o caso em que não há nada genuíno pra enviar.

## Envio "solto" de pré-carga: formação de carga dentro do próprio Fusion

Motivado por um pedido de negócio, não um achado de produção: o gestor
quer formar a carga manualmente dentro do Fusion ("Formação de carga",
tela do portal) em vez de esperar o CISS formar a carga de verdade — e
depois replicar essa mesma formação no CISS. Isso é regressão na
automação (o agrupamento vira decisão manual duplicada, em dois
sistemas, sem chave de correlação entre eles — ver discussão que motivou
isto), mas o service precisa suportar o passo intermediário: deixar um
pedido disponível pra seleção naquela tela.

O modo padrão de pré-carga (`montar_payload_pre_carga`/
`processar_envio_pre_carga`) já empacota a pré-carga inteira num
`cargas[]` próprio (`codigo_erp = "PC_{id_empresa}_{id_precarga}"`, ver
`codigo_pre_carga`) — ou seja, o pedido já chega ao Fusion **dentro** de
uma carga, não disponível pra formação manual. O modo novo
(`montar_payload_pre_carga_solta`/`processar_envio_pre_carga_solta`)
manda só `saveEntregaServico`, nunca `saveCarga` — sem `cargas[]`, sem
`entrega[]` stub, sem motorista/veículo (não há carga nenhuma sendo
formada por esta tool nesse modo).

**Confirmado em produção**: `podeformarcarga` é o campo que controla se
a entrega aparece como agrupável em "Formação de carga" — `"S"` nesse
modo novo, contra `"N"` no modo padrão (onde a tool já formou a carga e
não quer que o motor de roteirização do Fusion reaproveite a entrega).

Só vale pra pré-carga de **um único pedido** — uma pré-carga com mais de
um pedido não tem como virar uma única entrega solta sem reintroduzir o
mesmo problema que o modo padrão resolve (agrupamento sem um objeto que
amarre os pedidos); `montar_payload_pre_carga_solta` devolve aviso
bloqueante nesse caso, sem chamar o Fusion. `contar_pedidos` expõe a
mesma contagem (`_agrupar_itens_por_pedido`) pro endpoint decidir
`permite_envio_solto` no preview, e o frontend só mostra o botão
("Enviar pedido solto") quando isso é `true`.

Os dois modos coexistem na mesma linha de fila
(`cargas_fusion_precarga_fila`, chave `(id_empresa, id_precarga)`) — uma
coluna nova, `modo` (`'BUNDLE'`/`'SOLTA'`), distingue qual foi usado.
Não há transição automática entre os dois: se uma pré-carga já foi
enviada num modo, reenviar no outro modo é `forcar=True` explícito, e
sobrescreve a linha (inclusive o `modo` gravado) — a tool não tenta
"desfazer" o que já foi mandado ao Fusion no modo anterior (não daria,
`cancelaCarga` não está implementado).

## Entrega entre filiais: item pode estar sob um `IDEMPRESA` diferente do cabeçalho da carga

Achado em produção (carga `2_22144917`, reportada pelo usuário como "não
aparece na integração"): o cabeçalho da carga (`RETIRADA_PRODUTOS`) existe
sob `IDEMPRESA=2`, mas o único item (`PRODUTO_RETIRADO`) daquele
`IDPLANILHA` está gravado sob `IDEMPRESA=1` — sem nenhuma linha de
`PRODUTO_RETIRADO` sob `IDEMPRESA=2` pra essa carga. **Confirmado com o
usuário que isso é esperado** — entrega entre filiais, não erro de
cadastro: o item físico foi baixado do estoque de uma filial, mas a
viagem/carga pertence a outra. Não é raro — amostra desde o corte de
integração encontrou milhares de cargas nesse padrão (ex. cabeçalho
empresa 9 / item empresa 2: 1539 casos; cabeçalho 6 / item 1: 479 casos).

Toda consulta que faz `PRODUTO_RETIRADO.IDEMPRESA = <empresa do
cabeçalho>` escondia essas cargas inteiras (zero itens resolvidos) da
integração — `get_itens_carga`, `_FILTRO_FUSION_CARGA` (usado por
`get_cargas`), `get_cargas_com_pedido_resolvido`,
`get_origem_pre_cargas_da_carga`, `get_carga_por_pedido` e
`get_carga_por_nota`, todos em `integrations/ciss.py`.

`PRODUTO_RETIRADO.IDEMPRESABAIXAEST` (a empresa de onde o estoque foi
baixado) é a coluna que de fato liga o item ao cabeçalho nesses casos —
bate com `IDEMPRESA` do cabeçalho exatamente na carga `2_22144917`
(timestamps de `DTMOVIMENTO` do cabeçalho e do item batem a poucos
milissegundos), e em 71% de uma amostra de 3467 cargas cross-empresa
desde o corte de integração. Os outros 29% não foram investigados a
fundo — o fix aplicado (`PR.IDEMPRESA = ? OR PR.IDEMPRESABAIXAEST = ?`
em todo lugar que antes só tinha `PR.IDEMPRESA = ?`) cobre a maioria sem
piorar nada: nunca remove uma condição, só adiciona uma alternativa.

`get_carga_por_pedido`/`get_carga_por_nota` devolvem sempre o `IDEMPRESA`
do **cabeçalho** (`RP.IDEMPRESA`, resolvido via o mesmo `OR`), nunca o do
item (`PR.IDEMPRESA`) — devolver o do item quebraria a navegação
("busca por documento" apontaria pra um par `(IDEMPRESA, IDPLANILHA)`
sem cabeçalho nenhum em `RETIRADA_PRODUTOS`, 404 na tela de carga).

**Dívida técnica conhecida**: os ~29% de cargas cross-empresa onde nem
`IDEMPRESA` nem `IDEMPRESABAIXAEST` do item batem com o cabeçalho
continuam invisíveis. Não investigado qual é a ligação certa nesses
casos — pode ser um terceiro campo, pode ser timing (o item sendo
anexado à carga depois que a venda já mudou de contexto), ou pode ser
genuinamente outro padrão de negócio. Revisitar se mais relatos de
"carga não aparece" surgirem.

### O `OR IDEMPRESABAIXAEST` duplicava pedido quando `IDPLANILHA` colide entre empresas

Achado em produção, reportado pelo usuário: cargas `9_22160293` e
`2_22160293` mostravam o mesmo pedido (616672) duplicado. Confirmado por
query — os dois cabeçalhos são reais e independentes, criados a ~0.7s de
distância (`08:32:10.035` e `08:32:10.757`), que por coincidência de
sequência (per-empresa) acabaram com o mesmo número de `IDPLANILHA`. O
item de 616672 tem `IDEMPRESA=2, IDEMPRESABAIXAEST=9` — um match direto e
inequívoco com o cabeçalho `(2, 22160293)`, sem ambiguidade nenhuma. O
`OR IDEMPRESABAIXAEST` (pensado só pro caso de item órfão na própria
empresa, ver "Entrega entre filiais" acima) vazava esse mesmo item pro
cabeçalho `(9, 22160293)` também — uma carga completamente diferente que
só por acaso reusou o mesmo número.

Consequência real: como `pedido_erp` usa `id_planilha` da carga como
sufixo (`codigo_pedido`/`codigo_pedido_generico` em
`cargas_fusion/service.py`), os dois cabeçalhos gerariam o mesmo
`pedido_erp` (`"616672_22160293"`) mas `empresa_fat` diferente (`"9"` vs
`"2"`) — o upsert do Fusion por `(empresa_fat, pedido_orig)` não dedupia
isso, então o pedido seria enviado duas vezes como dois pedidos
distintos.

Correção: todo `OR PR.IDEMPRESA(BAIXAEST)? = ?` em `integrations/ciss.py`
(`_FILTRO_FUSION_CARGA`, `get_cargas_com_pedido_resolvido`,
`get_carga_por_pedido`, `get_carga_por_nota`, `get_itens_carga`,
`get_origem_pre_cargas_da_carga`) ganhou um `NOT EXISTS` — o fallback por
`IDEMPRESABAIXAEST` só vale quando o item **não tem** cabeçalho próprio
(`RETIRADA_PRODUTOS` por `PR.IDEMPRESA`/`PR.IDPLANILHA`) — nunca quando já
existe um match direto, mesmo que outro cabeçalho de outra empresa também
bata por `IDEMPRESABAIXAEST`. Não muda o caso original de entrega entre
filiais (`2_22144917`): lá o item genuinamente não tinha cabeçalho na
própria empresa, então o `NOT EXISTS` continua permitindo o fallback.

## Métodos do WSDL não implementados

`atualizaCarga`/`cancelaCarga` (atualizar `dt_saida`/motorista/veículo ou
cancelar uma carga já enviada) — schema de `jsonData` não mapeado por
completo. Testes em produção devolveram listas de campo esperado
incompletas ou inconsistentes com o nome usado em `saveCarga`
(`cod_motorista`/`cod_veiculo`, não `codmotorista`/`codveiculo`). Não
bloqueia o envio inicial; só entra em cena se "editar/cancelar carga já
enviada" virar escopo.

## Fuso horário nas respostas da API — duas quirks opostas

Datas que chegam ao frontend vêm de duas fontes com problemas opostos,
ambos corrigidos na fronteira de leitura, nunca no frontend:

- **CISS (`integrations/ciss.py`)**: `pyodbc` devolve `datetime`/`date`
  ingênuos (sem timezone), mas o servidor roda no horário de Brasília
  (`-03:00`, sem DST desde 2019), não UTC. Sem tratar isso,
  `jsonify`/Flask serializa `datetime` cru como `"Wed, 21 Oct 2026
  11:19:14 GMT"` (RFC 822, rotulado GMT mesmo sendo hora local) — o
  frontend lê isso como UTC de verdade e desconta 3h ao exibir no fuso
  do navegador, mostrando uma carga confirmada às 11h como se fosse às
  8h. `_normalizar_valor` em `_fetch_all` converte pra ISO 8601 com
  `-03:00` explícito antes de qualquer `datetime` cru escapar pra
  `jsonify`.
- **sqlite3 (`cargas_fusion/service.py`)**: o inverso. `CURRENT_TIMESTAMP`
  é sempre UTC, mas a coluna é `TEXT` — vem como string ingênua
  (`"2026-10-21 15:16:59"`, sem `T` nem `Z`). Sem marcação, o frontend
  lê como hora local e não desconta nada, mostrando `enviado_em` 3h
  **depois** do horário de Brasília real (o problema oposto ao do CISS).
  `_sqlite_utc_iso` normaliza pra `"...T...Z"` em `_linha_fila`/
  `_linha_fila_pre_carga` antes de devolver.

Qualquer campo novo de data/hora que entrar na API — de uma dessas duas
fontes ou de outra — precisa do mesmo tratamento: nunca deixar uma string
de timestamp ambígua (sem offset nem `Z`) chegar ao frontend, porque
`new Date(...)` do JavaScript interpreta ausência de fuso como "hora
local do navegador", e cada fonte deste projeto mente sobre isso de um
jeito diferente.

## Busca de carga por documento (código, pedido ou NF)

`GET /api/cargas/busca-documento?termo=...` acha a carga a partir de
qualquer um dos três jeitos de identificar ela, sem precisar saber o
período/empresa pra buscar na lista: o código (`"1_22144395"`), o pedido
real — `IDORCAMENTO`, não `IDPLANILHADOCORI` (D11) — sozinho (`"616139"`),
ou a NF real no formato `"numnota-serie"` (`"166105-2"`).
`cargas_fusion/service.py::localizar_carga_por_documento` escolhe o
formato pela marca que o distingue (`_`, `-`, só dígitos) — sem
ambiguidade real entre os três. `integrations/ciss.py::get_carga_por_pedido`/
`get_carga_por_nota` reusam a mesma cadeia de junção que
`get_itens_carga` já usa pra resolver pedido/NF reais (D11) — nunca
`IDPLANILHADOCORI` direto. Esta busca **não aplica** o filtro de
`FLAGUTILIZAINTEGRACAOFUSION` (acima) — acha a carga mesmo que o local de
retirada não tenha a integração marcada, porque quem está buscando já
sabe o documento específico que quer.

## `CREATE TABLE IF NOT EXISTS` não migra coluna renomeada

Achado em produção: a migração do veículo pro portal do Fusion (sem
cadastro local) trocou `cargas_fusion_precarga_fila.veiculo_id` por
`veiculo_codigo_erp`/`veiculo_descricao` em `schema.sql`, mas um banco já
existente (criado antes dessa mudança) nunca ganhava as colunas novas —
`CREATE TABLE IF NOT EXISTS` é no-op numa tabela que já existe, só
`_migrar()` altera schema de banco existente. Todo `INSERT`/`UPDATE` de
`processar_envio_pre_carga` já falava `veiculo_codigo_erp`, então
quebrava com `"no such column"` nesse banco. `_migrar()` agora adiciona
as duas colunas quando faltam (`veiculo_id` fica, sem uso — sqlite3 não
tem `DROP COLUMN` barato, e não há dado nela que valha migrar). Qualquer
coluna renomeada (não só nova) em `schema.sql` precisa do mesmo cuidado.

## Preço dos itens: `preco` (unitário) vs. `subtotal` (líquido da linha)

Resolvida a pendência que existia aqui (`preco` derivado de
`VALTOTLIQUIDO / QTDPRODUTO`, sem fonte unitária confirmada): existe uma
coluna de preço unitário de verdade em `ESTOQUE_ANALITICO` —
`VALUNITBRUTO`, confirmada contra o catálogo do DB2 e contra produção
(carga `1_22142248`). `_preco_item` (`cargas_fusion/service.py`) usa essa
coluna pra `preco`; `subtotal` continua vindo de `VALTOTLIQUIDO`, como
antes.

**Os dois vêm de bases diferentes — bruto vs. líquido — de propósito.**
`VALUNITBRUTO` é o preço de catálogo (antes do desconto da venda);
`VALTOTLIQUIDO` é o valor líquido de fato cobrado na linha. Confirmado em
produção que a diferença é sistemática (desconto de ~10% em toda a
amostra testada, não ruído) — `preco × qtd` não bater com `subtotal`
quando há desconto é esperado, do mesmo jeito que numa nota fiscal
comum. Fallback pro cálculo antigo (`VALTOTLIQUIDO / QTDPRODUTO`) quando
`VALUNITBRUTO` vem `NULL`/zero — confirmado que isso acontece numa fração
pequena das linhas (~0.45% de uma amostra de 10.7M linhas de
`ESTOQUE_ANALITICO`, mesma taxa de ausência de `VALTOTLIQUIDO`).

## `obs` de `saveEntregaServico` — observação do pedido, não da carga

O campo "Observação" que o Fusion mostra na tela de entrega (pedido),
diferente do "obs" da carga (`saveCarga`, já populado por
`RETIRADA_PRODUTOS.OBSERVACAO`), ficava sempre vazio — `entregas_servico`
nunca mandava nada parecido. Testado isoladamente contra produção
(pedido descartável, filial e cliente de teste): mandando `"obs"` e
`"observacao"` juntos num mesmo item, só `"obs"` apareceu na tela —
`"observacao"` não tem efeito nenhum (chave ignorada, a chamada não
rejeita chaves desconhecidas).

Fonte confirmada contra o catálogo do DB2: `ORCAMENTO.OBSERVACAO` (a nota
digitada no CISS na hora da venda, no nível do pedido/orçamento, não da
carga) — `get_itens_carga`/`get_itens_pre_carga` ganharam um
`LEFT JOIN ORCAMENTO ORC ON ORC.IDORCAMENTO = OPN.IDORCAMENTO` (mesmo
`IDORCAMENTO` que já resolviam via `ORCAMENTO_PRE_NOTA`), expondo
`ORCAMENTO_OBSERVACAO`; `_montar_payload` manda isso como `"obs"` de cada
item de `entregas_servico`. Pedido sem observação cadastrada manda `""`,
nunca falta o campo.

## Pendências de dado conhecidas

- Praça/rota reais (ver acima) — placeholder funciona, sem fonte no CISS.
- Campos de filial do stub de `saveCarga` só testados com filial de teste;
  não testado se um cliente real (CPF/CNPJ, endereço) exige mais campos no
  `cliente{}` do que o mínimo documentado acima.
