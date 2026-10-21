-- Schema do fusion-service. sqlite3 nativo, sem ORM, sem migrations
-- automáticas — colunas novas entram em `_migrar()` em database.py.

-- Allow-list de quem pode usar este serviço, mantida manualmente pela TI.
-- Não é sincronizada de `user_tools` do meta-natal: sincronizar recriaria
-- a dependência que a separação deste serviço existe para remover.
CREATE TABLE IF NOT EXISTS usuarios_locais (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT NOT NULL UNIQUE,
    ciss_code   TEXT UNIQUE,
    name        TEXT NOT NULL,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Motorista e veículo não têm tabela própria: lidos ao vivo do portal web
-- do Fusion (integrations/fusion_portal.py), sem cadastro local — só o
-- codigo_erp escolhido fica gravado nas filas abaixo, como snapshot.

CREATE TABLE IF NOT EXISTS fusion_ajudantes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    idclifor_ciss  INTEGER UNIQUE,
    codigo_erp     TEXT NOT NULL UNIQUE,
    nome           TEXT NOT NULL,
    ativo          INTEGER NOT NULL DEFAULT 1,
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cargas_fusion_fila (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    id_empresa            INTEGER NOT NULL,
    id_planilha           INTEGER NOT NULL,
    status                TEXT NOT NULL DEFAULT 'PENDENTE' CHECK (status IN ('PENDENTE', 'ENVIADA')),
    motivo                TEXT,
    avisos                TEXT NOT NULL DEFAULT '[]',
    tentativas            INTEGER NOT NULL DEFAULT 0,
    enviado_em            TEXT,
    integrada_manualmente INTEGER NOT NULL DEFAULT 0,
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id_empresa, id_planilha)
);

CREATE TABLE IF NOT EXISTS fusion_clientes_sincronizados (
    idclifor_ciss INTEGER PRIMARY KEY,
    created_at    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS cargas_fusion_precarga_fila (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    id_empresa            INTEGER NOT NULL,
    id_precarga           INTEGER NOT NULL,
    codigo_erp            TEXT NOT NULL,
    motorista_codigo_erp  TEXT,
    motorista_nome        TEXT,
    veiculo_codigo_erp    TEXT,
    veiculo_descricao     TEXT,
    status                TEXT NOT NULL DEFAULT 'PENDENTE' CHECK (status IN ('PENDENTE', 'ENVIADA')),
    motivo                TEXT,
    avisos                TEXT NOT NULL DEFAULT '[]',
    tentativas            INTEGER NOT NULL DEFAULT 0,
    enviado_em            TEXT,
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id_empresa, id_precarga)
);
