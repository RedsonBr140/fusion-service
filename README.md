# fusion-service

Integração com o Fusion DMS (SOAP + portal web) e as leituras de CISS DB2
que ela precisa — extraída do meta-natal para ser um serviço próprio, com
host, banco e deploy independentes. Nenhum código daqui depende do
meta-natal estar no ar; a relação entre os dois é só uma troca de token
opcional (ver "Autenticação").

A tool **Cargas Fusion** continua listada no Toolkit do meta-natal — isso
não mudou. O que mudou é que, ao entrar na página, o frontend passa a
falar direto com este serviço.

## Stack

Flask + `sqlite3` nativo (sem ORM, sem migrations automáticas — `schema.sql`
+ `_migrar()` em `database.py`, mesmo padrão do meta-natal). Python 3.14.

## Subindo localmente

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env   # preencher as credenciais, ver abaixo
.venv/bin/python -c "from app import create_app; create_app().run(port=5050)"
```

Testes:

```bash
.venv/bin/python -m pytest -q
```

## Variáveis de ambiente

Tudo documentado em `.env.example`. Os grupos que importam:

- `SECRET_KEY` — assina as sessões próprias deste serviço (12h de validade).
- `FUSION_TOKEN_SECRET` — **precisa ter o mesmo valor** do `.env` do
  meta-natal (lá, também `FUSION_TOKEN_SECRET`). É o segredo compartilhado
  que permite validar o token de troca que o meta-natal emite em
  `POST /api/tools/cargas-fusion/token`. Esse é o único acoplamento entre
  os dois projetos — um valor, não um import, não uma tabela.
- `CISS_DB_*` (DRIVER/HOST/NAME/USER/PASSWORD/PORT/TIMEOUT) — leitura do
  CISS DB2 via ODBC, somente `SELECT` (guarda em
  `integrations/ciss.py::validate_readonly_query`, aplicada
  independentemente da mesma guarda no meta-natal). Todas obrigatórias, ou
  nenhuma.
- `CISS_API_URL` / `CISS_CA_BUNDLE` — autenticação CISS (login direto,
  fallback).
- `FUSION_API_URL/LOGIN/SENHA` — webservice SOAP do Fusion (`saveCarga` e
  companhia). Todas ou nenhuma.
- `FUSION_PORTAL_URL/LOGIN/SENHA` — portal web do Fusion (busca de
  motorista, `fusion-py`). Todas ou nenhuma.
- `ALLOWED_ORIGINS` — origens do frontend liberadas por CORS (separadas
  por vírgula).
- `DATABASE_PATH`, `PORT`.

Qualquer grupo "todas ou nenhuma" ausente faz o recurso correspondente
rodar desligado (ex.: sem `FUSION_API_URL/LOGIN/SENHA`, envio ao Fusion
fica indisponível, mas o resto do serviço sobe normalmente) — mesma
convenção do meta-natal.

## Autenticação

Dois caminhos para chegar numa sessão deste serviço — nenhum dos dois
depende de cookie, só de `Authorization: Bearer <token>`:

**1. Troca de token (caminho rápido, com o meta-natal no ar)**

1. O frontend, já logado no meta-natal, pede
   `POST /api/tools/cargas-fusion/token` (lá, atrás do `login_required` +
   `tool_required("cargas_fusion")` de sempre) e recebe um token assinado
   de vida curta (~5 min, claims `ciss_code`/`name`/`exp`).
2. Troca esse token aqui: `POST /api/token/exchange` — valida a
   assinatura com `FUSION_TOKEN_SECRET`, confere o `ciss_code` contra a
   allow-list local (`usuarios_locais`) e, se ativo, devolve uma sessão
   própria deste serviço (~12h).

**2. Login direto (fallback, meta-natal fora do ar)**

`POST /api/login` com `{username, password}` — a allow-list local é
consultada **antes** de qualquer chamada ao CISS (mesma ordem do
meta-natal: usuário fora da allow-list nunca gera tráfego pro CISS).
Autentica contra o CISS normalmente e devolve a mesma sessão de 12h.

**Allow-list (`usuarios_locais`)** — mantida manualmente, de propósito não
sincronizada de `user_tools` do meta-natal (sincronizar recriaria a
dependência que esta separação existe para remover):

```
POST /api/usuarios   {username, ciss_code, name}   — concede ou reativa
GET  /api/usuarios                                  — lista quem tem acesso
```

Ambas atrás de `login_required` — qualquer pessoa já com acesso pode
conceder a outra. Grupo pequeno, cuidado manual mesmo.

## Rotas de negócio

Todas atrás de `Authorization: Bearer <token>`, mesma forma/payload de
quando viviam no meta-natal, só sem o prefixo `/tools/cargas-fusion`:

```
GET    /api/cargas
GET    /api/empresas
GET    /api/cargas/<id_empresa>/<id_planilha>
POST   /api/cargas/<id_empresa>/<id_planilha>/enviar
POST   /api/cargas/<id_empresa>/<id_planilha>/marcar-integrada

GET    /api/pre-cargas
GET    /api/pre-cargas/<id_empresa>/<id_precarga>
POST   /api/pre-cargas/<id_empresa>/<id_precarga>/enviar
GET    /api/pre-cargas/fila
DELETE /api/pre-cargas/fila/<id>

GET    /api/fila
DELETE /api/fila/<id>

GET    /api/veiculos
GET    /api/veiculos/candidatos
POST   /api/veiculos
PUT    /api/veiculos/<id>
POST   /api/veiculos/<id>/sincronizar

GET    /api/motoristas

GET    /api/ajudantes
GET    /api/ajudantes/candidatos
POST   /api/ajudantes
PUT    /api/ajudantes/<id>
POST   /api/ajudantes/<id>/sincronizar
```

## Estrutura

```
app.py                  create_app() — monta clients, CORS, blueprints
config.py                Config (dataclass) + loaders por grupo de env var
database.py / schema.sql get_db(), migração idempotente
integrations/
  ciss.py                 leitura CISS DB2, reduzida aos 12 métodos usados aqui
  ciss_auth.py            login CISS (authenticate()) — isola o libciss, alpha e instável
  fusion.py                webservice SOAP (saveCarga, sendVeiculos, ...)
  fusion_portal.py         portal web (busca/sugestão de motorista)
auth/
  tokens.py               esquema HMAC comum (token de troca e de sessão)
  service.py               login, troca, allow-list
  decorators.py             login_required via Bearer
  routes.py                 /login, /token/exchange, /me, /logout, /usuarios
cargas_fusion/
  service.py                lógica de negócio (sem dependência de CISS)
  routes.py                  rotas HTTP, usa os clients acima
scripts/
  migrar_de_meta_natal.py    migração única das 5 tabelas, roda no corte
```

## Migração única (corte do meta-natal)

```bash
.venv/bin/python scripts/migrar_de_meta_natal.py \
  /caminho/para/meta-natal/backend/data/toolkit.db \
  ./data/fusion_service.db
```

Idempotente (`INSERT OR IGNORE`) — seguro rodar de novo. Migra
`fusion_veiculos`, `fusion_ajudantes`, `fusion_clientes_sincronizados`,
`cargas_fusion_fila`, `cargas_fusion_precarga_fila`. Não toca
`usuarios_locais` — essa é só deste serviço, de propósito.

## Regras que não se negociam

Herdadas do meta-natal, válidas aqui de forma independente (não por
reaproveitar código, por reaproveitar o princípio):

- **CISS DB2 é somente leitura.** ODBC com `pyodbc`, só `SELECT`,
  guardado em `validate_readonly_query`.
- **A allow-list local é consultada antes do CISS**, nos dois caminhos de
  login (direto e troca). Usuário fora dela nunca gera tráfego pro CISS.
- **Nenhum log registra senha, token ou header `Authorization`.**
- **Sem ORM, sem SQLAlchemy, sem migrations.** `sqlite3` nativo,
  queries parametrizadas.
