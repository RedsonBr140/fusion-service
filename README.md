# fusion-service

Serviço que busca cargas no CISSPoder (DB2) e manda para o Fusion DMS —
webservice SOAP e portal web. App standalone: frontend próprio, backend
próprio, banco próprio, deploy próprio. Login direto (allow-list local +
CISS) é a única porta de entrada.

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
- `FUSION_API_URL/LOGIN/SENHA` — webservice SOAP do Fusion (`saveCarga`,
  `saveEntregaServico`, `sendMotoristas`, `sendVeiculos`, `sendClientes`,
  `consultarVeiculo`, `consultarTodosVeiculos`). Todas ou nenhuma.
- `FUSION_PORTAL_URL/LOGIN/SENHA` — portal web do Fusion (login humano via
  `fusion-py`): busca e sugestão de motorista e veículo, checagem se um
  cliente já está cadastrado. Todas ou nenhuma.
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

## O que o serviço faz

Busca uma carga (ou pré-carga) no CISS, monta o payload que o Fusion
exige, mostra pra revisão (motorista/veículo sugeridos, avisos do que
falta) e envia. Duas chamadas SOAP compõem um envio, nessa ordem:
`saveEntregaServico` registra o pedido com os itens de produto, e só
depois `saveCarga` referencia esse pedido e forma a carga — o `saveCarga`
em si não tem onde carregar item de produto. Ver `CLAUDE.md` para o
mapeamento campo a campo e as decisões de desenho que não são óbvias
lendo só o código.

Toda tentativa de envio (sucesso ou falha) fica registrada em
`cargas_fusion_fila` / `cargas_fusion_precarga_fila` — nada se perde num
log; falha é visível e reenviável depois que o cadastro for corrigido.

Motorista e veículo não têm cadastro local: os dois são lidos ao vivo do
portal web do Fusion (não existe `consultarMotorista` no SOAP; veículo
tem `consultarVeiculo` no SOAP, mas usa o portal pela mesma simetria).
Cliente também não tem cadastro — é sincronizado via `sendClientes` sob
demanda, marcado em `fusion_clientes_sincronizados` pra não reenviar o
cadastro em todo envio. Só ajudante tem cadastro local (`fusion_ajudantes`)
— não há papel "ajudante" no Fusion para ler de volta.

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
  ciss.py                 leitura CISS DB2, reduzida aos métodos usados aqui
  ciss_auth.py            login CISS (authenticate()) — isola o libciss, alpha e instável
  fusion.py                webservice SOAP do Fusion (saveCarga, saveEntregaServico, sendMotoristas, ...)
  fusion_portal.py         portal web do Fusion (busca/sugestão de motorista, checagem de cliente)
auth/
  tokens.py               esquema de token de sessão (HMAC, sem estado)
  service.py               login, allow-list
  decorators.py             login_required via Bearer
  routes.py                 /login, /me, /logout, /usuarios
cargas_fusion/
  service.py                lógica de negócio: cadastro local, montagem de payload, fila de envio
  routes.py                  rotas HTTP, usa os clients acima
frontend/
  src/api.ts                 toda chamada HTTP, token em localStorage
  src/types.ts                tipos da API
  src/components/ui/          primitivas (button, input, label, card, dialog)
  src/tools/cargas_fusion/    telas e hooks
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
