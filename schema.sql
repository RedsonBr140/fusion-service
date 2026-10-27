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
    modo                  TEXT NOT NULL DEFAULT 'BUNDLE' CHECK (modo IN ('BUNDLE', 'SOLTA')),
    status                TEXT NOT NULL DEFAULT 'PENDENTE' CHECK (status IN ('PENDENTE', 'ENVIADA')),
    motivo                TEXT,
    avisos                TEXT NOT NULL DEFAULT '[]',
    tentativas            INTEGER NOT NULL DEFAULT 0,
    enviado_em            TEXT,
    created_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at            TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id_empresa, id_precarga)
);

-- Linha única (id=1): estado do ciclo automático de envio de pré-carga
-- solta (cargas_fusion/auto_envio.py). O lock contra os 2 workers do
-- gunicorn vive no Redis, não aqui — esta tabela é só config/estatística
-- compartilhada (pausa, última execução), sem disputa de concorrência
-- real.
CREATE TABLE IF NOT EXISTS cargas_fusion_auto_envio_estado (
    id                          INTEGER PRIMARY KEY CHECK (id = 1),
    pausado                     INTEGER NOT NULL DEFAULT 0,
    ultima_execucao_em          TEXT,
    ultima_execucao_duracao_ms  INTEGER,
    ultima_execucao_candidatas  INTEGER,
    ultima_execucao_enviadas    INTEGER,
    ultima_execucao_falhas      INTEGER,
    updated_at                  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
INSERT OR IGNORE INTO cargas_fusion_auto_envio_estado (id) VALUES (1);

-- Pré-carga com mais de um pedido, vista pelo ciclo automático — nunca
-- enviada (só pedido único entra no envio automático), mas registrada
-- pra medir o impacto real e achar qual empresa comete isso mais. Uma
-- linha por pré-carga, não por tick do scheduler.
CREATE TABLE IF NOT EXISTS cargas_fusion_precarga_multi_pedido (
    id_empresa      INTEGER NOT NULL,
    id_precarga      INTEGER NOT NULL,
    qtd_pedidos      INTEGER NOT NULL,
    primeira_vez_em  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ultima_vez_em    TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id_empresa, id_precarga)
);

-- Histórico de execuções do ciclo automático, pra diagnóstico de
-- desempenho ("qual etapa demorou mais, por quê") — uma linha por
-- ciclo, podada pra manter só as últimas 200 (ver
-- `auto_envio._podar_execucoes`). `breakdown` é JSON
-- `{"<cliente>.<metodo>": {"duracao_ms": N, "chamadas": N}}`, somado por
-- método de cliente (CISS/Fusion/portal) ao longo do ciclo inteiro.
-- `itens_lentos` é JSON, lista das pré-cargas mais lentas do ciclo (até
-- 10), cada uma com o método que dominou o tempo dela.
CREATE TABLE IF NOT EXISTS cargas_fusion_auto_envio_execucoes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    executado_em   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    duracao_ms     INTEGER NOT NULL,
    candidatas     INTEGER NOT NULL,
    enviadas       INTEGER NOT NULL,
    falhas         INTEGER NOT NULL,
    breakdown      TEXT NOT NULL DEFAULT '{}',
    itens_lentos   TEXT NOT NULL DEFAULT '[]'
);
