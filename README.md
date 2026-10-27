# fusion-service

Serviço que busca pedidos no CISSPoder (DB2, somente leitura) e manda pra
o Fusion DMS — webservice SOAP e portal web. App standalone: frontend
próprio, backend próprio, banco próprio, deploy próprio. Login direto
(allow-list local + CISS) é a única porta de entrada.

## Escopo atual: pedido solto, não carga empacotada

**A formação de carga passou a acontecer dentro do próprio Fusion**
("Formação de carga", tela do portal), não mais neste serviço. O que
este serviço faz hoje, de ponta a ponta:

1. Lê pré-carga no CISS (`PRE_CARGA_PROD` e companhia).
2. Registra o pedido no Fusion via `saveEntregaServico` — **sozinho**,
   sem `saveCarga`, com `podeformarcarga: "S"` — disponível pra seleção
   manual na tela "Formação de carga" assim que chega.
3. Isso acontece de dois jeitos, que coexistem:
   - **Manual**: alguém abre a pré-carga na tela e clica "Enviar pedido
     solto" (`POST /api/pre-cargas/<id_empresa>/<id_precarga>/enviar-solta`).
   - **Automático**: um ciclo em background (`cargas_fusion/auto_envio.py`,
     `APScheduler`) varre pré-carga recente e manda sozinho, sem clique —
     só pra pré-carga de **pedido único** (multi-pedido cai fora do
     escopo automático, só notifica Discord e fica esperando alguém
     mandar manual).

Só vale pra pré-carga de um único pedido — uma pré-carga com mais de um
pedido não tem como virar uma única entrega solta sem reintroduzir o
problema que esse modo resolve (agrupamento sem objeto que amarre os
pedidos). **Isso não é uma lacuna a preencher — é permanente.** Enquanto
o fluxo for "pedido solto, sem `saveCarga`", não existe payload que
agrupe mais de um pedido numa única entrega; suportar multi-pedido de
verdade exigiria voltar a formar carga empacotada (o caminho legado
abaixo), não uma evolução deste modo. `montar_payload_pre_carga_solta`
sempre devolve aviso bloqueante pra isso, e o ciclo automático só
registra/notifica (`cargas_fusion_precarga_multi_pedido`,
`GET /api/metricas`) — nunca tenta enviar. `GET /api/pre-cargas` já expõe `permite_envio_solto` pra tela
decidir quando mostrar o botão.

### As flags antigas (`MOSTRAR_CARGAS_CISS`, `SOMENTE_PEDIDOS_SOLTOS`) são legado — e já desligam o caminho antigo por padrão

Este serviço nasceu formando a carga ele mesmo: lia `RETIRADA_PRODUTOS`
(carga real já fechada no CISS) e mandava `saveCarga` empacotado
(`cargas[]`, com `entrega[]`, motorista, veículo sugeridos). Isso **não é
mais o fluxo corrente** — virou um caminho morto, mantido só pelas duas
flags abaixo, ambas já com o default que o desliga:

- `MOSTRAR_CARGAS_CISS` (default `false`) — liga de volta as rotas de
  carga real (`/api/cargas*`), bloqueadas (403) enquanto for `false`.
- `SOMENTE_PEDIDOS_SOLTOS` (default `true`) — bloqueia qualquer envio
  empacotado num `saveCarga` (carga real **e** pré-carga no modo bundle
  `PC_...`), mesmo que `MOSTRAR_CARGAS_CISS` seja religada. Só
  `/pre-cargas/.../enviar-solta` fica disponível enquanto for `true`.

Religar as duas é regressão deliberada pra um caso específico, não o
caminho recomendado — **não religue nenhuma das duas sem saber por quê**.
O código das rotas/payload de carga empacotada continua no repo (não foi
apagado, pra não perder histórico/capacidade de voltar), mas é código
morto em qualquer deploy que use o default. `GET /api/config` expõe as
duas pro frontend decidir o que esconder na tela, sem vazar a `Config`
inteira (que carrega credencial).

## Stack

Monolito: Flask serve a API e, em produção, o próprio frontend buildado —
um processo, uma origem, sem nginx na frente. `sqlite3` nativo (sem ORM,
sem migrations automáticas — `schema.sql` + `_migrar()` em `database.py`).
Redis só pra um lock (`redis-py`, evita o ciclo automático disparar em
dobro nos 2 workers do gunicorn — sem PVC, perder o estado num restart
não tem consequência real). Frontend em React + Vite + TypeScript +
Tailwind. Python 3.14.

## Subindo localmente

**Backend:**

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # preencher as credenciais, ver abaixo
.venv/bin/python -c "from app import create_app; create_app().run(port=5050)"
```

**Frontend** (outro terminal, dev):

```bash
cd frontend
npm install
npm run dev   # :5173, com proxy de /api para :5050 (vite.config.ts)
```

**Produção:** `cd frontend && npm run build` gera `frontend/dist/`, que o
próprio Flask passa a servir (rota catch-all em `app.py`) — só precisa
rodar o backend depois disso, sem processo de frontend separado.

**Docker, pra testar o ciclo automático antes de produção** — em
particular o lock no Redis contra os 2 workers do gunicorn (a imagem já
roda `--workers 2`, igual produção). Ver os comentários no topo de
`docker-compose.dev.yml` pro passo a passo completo e as armadilhas já
confirmadas na prática (owner do bind mount do sqlite, Compose
carregando `.env` deste projeto sozinho). Resumo:

```bash
cp .env.development.example .env.development   # primeira vez só
mkdir -p data-dev && chmod 777 data-dev          # a imagem roda non-root
docker compose -f docker-compose.dev.yml up --build
docker compose -f docker-compose.dev.yml logs -f backend
```

Vazio, `.env.development` só prova o lock/scheduler sem tocar
CISS/Fusion de verdade. Pra um teste contra CISS DB2 real dentro do
Docker, falta mais uma peça: o clidriver proprietário da IBM, montado
como volume (`k8s/db2-driver.yaml` documenta o caminho/`odbcinst.ini`
esperado em produção) — sem ele, a conexão ODBC falha mesmo com
credenciais certas, só com "Conexão ODBC com o CISS DB2 falhou".

Testes do backend:

```bash
.venv/bin/python -m pytest -q
```

## Variáveis de ambiente

Tudo documentado em `.env.example`. Os grupos que importam:

- `SECRET_KEY` — assina as sessões deste serviço (validade ~12h).
- `CISS_DB_*` (DRIVER/HOST/NAME/USER/PASSWORD/PORT/TIMEOUT) — leitura do
  CISS DB2 via ODBC, somente `SELECT` (guarda em
  `integrations/ciss.py::validate_readonly_query`). Todas obrigatórias, ou
  nenhuma.
- `CISS_API_URL` / `CISS_CA_BUNDLE` — autenticação CISS (o login desta
  app).
- `FUSION_API_URL/LOGIN/SENHA` — webservice SOAP do Fusion (`saveCarga`,
  `saveEntregaServico`, `sendMotoristas`, `sendVeiculos`, `sendClientes`,
  `consultarVeiculo`, `consultarTodosVeiculos`). Todas ou nenhuma. No
  escopo atual, só `saveEntregaServico`/`sendMotoristas`/`sendClientes`
  entram em jogo de verdade — o resto é o caminho legado (ver acima).
- `FUSION_PORTAL_URL/LOGIN/SENHA` — portal web do Fusion (login humano via
  `fusion-py`): busca e sugestão de motorista e veículo, checagem se um
  cliente já está cadastrado. Todas ou nenhuma.
- `MOSTRAR_CARGAS_CISS` (default `false`) / `SOMENTE_PEDIDOS_SOLTOS`
  (default `true`) — flags do caminho legado, ver seção acima. Não
  religar sem saber por quê.
- `AUTO_ENVIO_PRE_CARGA_ATIVO` (default `false`), `REDIS_URL` (obrigatório
  se a flag acima for `true`), `AUTO_ENVIO_PRE_CARGA_INTERVALO_SEGUNDOS`
  (default `300`), `AUTO_ENVIO_PRE_CARGA_JANELA_HORAS` (default `24`),
  `AUTO_ENVIO_PRE_CARGA_CORTE` (ISO `AAAA-MM-DDTHH:MM:SS`, default fixo
  no código pra produção) — ciclo automático de envio de pedido solto,
  ver seção acima e `CLAUDE.md`.
- `DISCORD_WEBHOOK_URL` — opcional. Sem ele, falha do ciclo automático e
  pré-carga multi-pedido ignorada só vão pro log, nunca pro Discord.
- `DATABASE_PATH`, `PORT`.

Qualquer grupo "todas ou nenhuma" ausente faz o recurso correspondente
rodar desligado (ex.: sem `FUSION_API_URL/LOGIN/SENHA`, envio ao Fusion
fica indisponível, mas o resto do serviço sobe normalmente).

## Autenticação

Único caminho: `POST /api/login` com `{username, password}` — a allow-list
local (`usuarios_locais`) é consultada **antes** de qualquer chamada ao
CISS (usuário fora dela nunca gera tráfego pro CISS). Autentica contra o
CISS e devolve uma sessão de ~12h como `Authorization: Bearer <token>` —
sem cookie, token em `localStorage` no frontend, sobrevive a reload. No
carregamento, o frontend confere esse token contra `GET /api/me` antes de
assumir a sessão como válida (expirado ou usuário desativado na allow-list
cai de volta pro login, sem erro). `POST /api/logout` descarta o token
guardado.

**Allow-list (`usuarios_locais`)** — mantida manualmente, um grupo
pequeno:

```
POST /api/usuarios   {username, ciss_code, name}   — concede ou reativa
GET  /api/usuarios                                  — lista quem tem acesso
```

Ambas atrás de `login_required` — qualquer pessoa já com acesso pode
conceder a outra.

## Motorista, veículo e cliente — sem cadastro local

Motorista e veículo não têm cadastro local: os dois são lidos ao vivo do
portal web do Fusion (não existe `consultarMotorista` no SOAP; veículo
tem `consultarVeiculo` no SOAP, mas o portal cobre o mesmo caso sem
precisar de outro cliente, mesma simetria com motorista). Isso só importa
pro caminho legado de carga empacotada (`entrega[]` referencia motorista
e veículo) — o envio de pedido solto não anexa nenhum dos dois.

Cliente também não tem cadastro — é sincronizado via `sendClientes` sob
demanda (ou confirmado via portal, quando configurado), marcado em
`fusion_clientes_sincronizados` pra não reenviar o cadastro em todo
envio. Só ajudante tem cadastro local (`fusion_ajudantes`) — não há
papel "ajudante" no Fusion para ler de volta, e só é relevante no
caminho legado.

## Toda tentativa de envio fica registrada

`cargas_fusion_fila` (carga empacotada, legado) /
`cargas_fusion_precarga_fila` (pré-carga, bundle ou solta — ver coluna
`modo`) — nada se perde num log; falha é visível e reenviável depois que
o cadastro for corrigido. O ciclo automático lê/grava na mesma
`cargas_fusion_precarga_fila`, então uma pré-carga já enviada manualmente
nunca é reenviada por ele (e vice-versa).

## Rotas de negócio

Todas atrás de `Authorization: Bearer <token>`. As marcadas **(legado)**
só respondem com `MOSTRAR_CARGAS_CISS=true`/`SOMENTE_PEDIDOS_SOLTOS=false`
— 403 com o default de produção.

```
GET    /api/config                                                  flags pro frontend (mostrar_cargas_ciss, somente_pedidos_soltos)

GET    /api/cargas                                                   (legado)
GET    /api/cargas/busca-documento                                   (legado)
GET    /api/empresas                                                 (legado)
GET    /api/cargas/<id_empresa>/<id_planilha>                        (legado)
POST   /api/cargas/<id_empresa>/<id_planilha>/enviar                 (legado, saveCarga empacotado)
POST   /api/cargas/<id_empresa>/<id_planilha>/marcar-integrada       (legado)

GET    /api/pre-cargas
GET    /api/pre-cargas/<id_empresa>/<id_precarga>
POST   /api/pre-cargas/<id_empresa>/<id_precarga>/enviar             (legado, modo bundle PC_...)
POST   /api/pre-cargas/<id_empresa>/<id_precarga>/enviar-solta       fluxo atual — saveEntregaServico solto
GET    /api/pre-cargas/fila
DELETE /api/pre-cargas/fila/<id>

GET    /api/pre-cargas/envio-automatico                              estado do ciclo (pausado?, última execução)
POST   /api/pre-cargas/envio-automatico/pausar
POST   /api/pre-cargas/envio-automatico/retomar
POST   /api/pre-cargas/envio-automatico/executar-agora               tick manual, mesmo lock do scheduler — 409 se AUTO_ENVIO_PRE_CARGA_ATIVO=false
GET    /api/pre-cargas/envio-automatico/execucoes                    histórico de ciclos (duração por etapa, pré-cargas mais lentas)

GET    /api/metricas                                                 enviadas/pendentes/falhas por dia, multi-pedido ignorado

GET    /api/fila                                                     (legado)
DELETE /api/fila/<id>                                                 (legado)

GET    /api/veiculos
GET    /api/motoristas

GET    /api/ajudantes
GET    /api/ajudantes/candidatos
POST   /api/ajudantes
PUT    /api/ajudantes/<id>
POST   /api/ajudantes/<id>/sincronizar
```

## Estrutura

```
app.py                  create_app() — monta clients, blueprints, agenda o ciclo automático, serve frontend/dist/ em produção
config.py                Config (dataclass) + loaders por grupo de env var, incl. flags legado e do ciclo automático
database.py / schema.sql get_db(), migração idempotente
docker-compose.dev.yml   infra de dev pra testar o lock Redis do ciclo automático (ver comentários no arquivo)
k8s/                      manifests de produção, incl. redis.yaml (lock) e db2-driver.yaml (clidriver IBM)
integrations/
  ciss.py                 leitura CISS DB2, reduzida aos métodos usados aqui
  ciss_auth.py            login CISS (authenticate()) — isola o libciss, alpha e instável
  fusion.py                webservice SOAP do Fusion (saveCarga, saveEntregaServico, sendMotoristas, ...)
  fusion_portal.py         portal web do Fusion (busca/sugestão de motorista, checagem de cliente)
  discord.py               notificação de falha do ciclo automático / pré-carga multi-pedido
auth/
  tokens.py               esquema de token de sessão (HMAC, sem estado)
  service.py               login, allow-list
  decorators.py             login_required via Bearer
  routes.py                 /login, /me, /logout, /usuarios
cargas_fusion/
  service.py                lógica de negócio: cadastro local, montagem de payload, fila de envio
  auto_envio.py              ciclo automático de envio de pedido solto (lock Redis, janela, corte)
  routes.py                  rotas HTTP, usa os clients acima
frontend/
  src/api.ts                 toda chamada HTTP, token em localStorage
  src/types.ts                tipos da API
  src/components/ui/          primitivas (button, input, label, card, dialog)
  src/tools/cargas_fusion/    telas e hooks — EnvioAutomaticoStatus.tsx (status/pausa do ciclo),
                               HistoricoPreCargaSection.tsx (fila de pré-carga enviada)
```

## Regras que não se negociam

- **CISS DB2 é somente leitura.** ODBC com `pyodbc`, só `SELECT`,
  guardado em `validate_readonly_query`. A exceção é o Fusion: `saveCarga`
  e companhia são escrita, mas é escrita em outro sistema via SOAP, não no
  CISS DB2 — a regra de read-only nunca falou sobre o Fusion.
- **A allow-list local é consultada antes do CISS.** Usuário fora dela
  nunca gera tráfego pro CISS.
- **Nenhum log registra senha, token ou header `Authorization`** — nem do
  CISS, nem do Fusion.
- **Sem ORM, sem SQLAlchemy, sem migrations.** `sqlite3` nativo,
  queries parametrizadas.
- **Nada com formato de SOAP/XML atravessa `integrations/fusion.py` em
  direção ao resto do projeto.** A fronteira devolve dict/lista já
  decodificados do JSON de retorno, nunca o envelope.
- **O ciclo automático nunca roda sem `AUTO_ENVIO_PRE_CARGA_ATIVO=true`
  explícito, e nunca sem `REDIS_URL`** quando ativo — nenhum ambiente
  começa a mandar nada pro Fusion sozinho sem alguém ligar deliberadamente.

Ver `CLAUDE.md` para o mapeamento campo a campo entre CISS e Fusion, os
formatos de resposta do Fusion e as decisões de desenho que não são
óbvias lendo só o código.
