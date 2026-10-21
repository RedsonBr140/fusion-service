# fusion-service

Cargas Fusion — busca de carga no CISSPoder e integração ao Fusion DMS
(webservice SOAP + portal web). Era uma tool do Toolkit (meta-natal); hoje
é um app standalone: seu próprio frontend, backend, banco e deploy, sem
nenhum acoplamento com o meta-natal — nem token compartilhado, nem CORS,
nem link de volta. Login direto (allow-list local + CISS) é a única porta
de entrada.

## Stack

Monolito: Flask serve a API e, em produção, o próprio frontend buildado —
um processo, uma origem, sem nginx na frente. `sqlite3` nativo (sem ORM,
sem migrations automáticas — `schema.sql` + `_migrar()` em `database.py`).
Frontend em React + Vite + TypeScript + Tailwind. Python 3.14.

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
- `FUSION_API_URL/LOGIN/SENHA` — webservice SOAP do Fusion (`saveCarga` e
  companhia). Todas ou nenhuma.
- `FUSION_PORTAL_URL/LOGIN/SENHA` — portal web do Fusion (busca de
  motorista, `fusion-py`). Todas ou nenhuma.
- `DATABASE_PATH`, `PORT`.

Qualquer grupo "todas ou nenhuma" ausente faz o recurso correspondente
rodar desligado (ex.: sem `FUSION_API_URL/LOGIN/SENHA`, envio ao Fusion
fica indisponível, mas o resto do serviço sobe normalmente).

## Autenticação

Único caminho: `POST /api/login` com `{username, password}` — a allow-list
local (`usuarios_locais`) é consultada **antes** de qualquer chamada ao
CISS (usuário fora dela nunca gera tráfego pro CISS). Autentica contra o
CISS e devolve uma sessão de ~12h como `Authorization: Bearer <token>` —
sem cookie, token guardado em memória no frontend (perdido ao recarregar a
aba, por isso é preciso logar de novo).

**Allow-list (`usuarios_locais`)** — mantida manualmente, um grupo
pequeno:

```
POST /api/usuarios   {username, ciss_code, name}   — concede ou reativa
GET  /api/usuarios                                  — lista quem tem acesso
```

Ambas atrás de `login_required` — qualquer pessoa já com acesso pode
conceder a outra.

## Rotas de negócio

Todas atrás de `Authorization: Bearer <token>`:

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
app.py                  create_app() — monta clients, blueprints, serve frontend/dist/ em produção
config.py                Config (dataclass) + loaders por grupo de env var
database.py / schema.sql get_db(), migração idempotente
integrations/
  ciss.py                 leitura CISS DB2, reduzida aos 12 métodos usados aqui
  ciss_auth.py            login CISS (authenticate()) — isola o libciss, alpha e instável
  fusion.py                webservice SOAP (saveCarga, sendVeiculos, ...)
  fusion_portal.py         portal web (busca/sugestão de motorista)
auth/
  tokens.py               esquema de token de sessão (HMAC, sem estado)
  service.py               login, allow-list
  decorators.py             login_required via Bearer
  routes.py                 /login, /me, /logout, /usuarios
cargas_fusion/
  service.py                lógica de negócio (sem dependência de CISS)
  routes.py                  rotas HTTP, usa os clients acima
scripts/
  migrar_de_meta_natal.py    migração única das 5 tabelas, roda no corte
frontend/
  src/api.ts                 toda chamada HTTP, token em memória
  src/types.ts                tipos portados de meta-natal/frontend/src/types/index.ts (commit 0cea91e)
  src/components/ui/          primitivas (button, input, label, card, dialog) — mesmo visual do Toolkit
  src/tools/cargas_fusion/    as telas e hooks da tool, como estavam no meta-natal
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
- **A allow-list local é consultada antes do CISS.** Usuário fora dela
  nunca gera tráfego pro CISS.
- **Nenhum log registra senha, token ou header `Authorization`.**
- **Sem ORM, sem SQLAlchemy, sem migrations.** `sqlite3` nativo,
  queries parametrizadas.
