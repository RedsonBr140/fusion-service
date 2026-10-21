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

-- As cinco tabelas de negócio a seguir são cópias exatas (mesmas colunas e
-- constraints) das que existiam em meta-natal/backend/schema.sql antes da
-- extração — ver scripts/migrar_de_meta_natal.py para a migração dos dados.

CREATE TABLE IF NOT EXISTS fusion_veiculos (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    placa_normalizada    TEXT NOT NULL UNIQUE,
    codigo_erp           TEXT NOT NULL UNIQUE,
    descricao            TEXT,
    modelo               TEXT,
    ano_modelo           INTEGER,
    ano_fabricacao       INTEGER,
    peso_max_entregas    REAL,
    volume_max_entregas  REAL,
    qtd_pallets_veiculo  REAL,
    ativo                INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

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
    veiculo_id            INTEGER REFERENCES fusion_veiculos(id),
    status                TEXT NOT NULL DEFAULT 'PENDENTE' CHECK (status IN ('PENDENTE', 'ENVIADA')),
    motivo                TEXT,
    avisos                TEXT NOT NULL DEFAULT '[]',
    tentativas            INTEGER NOT NULL DEFAULT 0,
    enviado_em            TEXT,
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id_empresa, id_precarga)
);
