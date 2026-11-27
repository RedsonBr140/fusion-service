-- Vínculo romaneio do Fusion <-> carga do CISS (decisão do negócio, 2026-11-27).
-- Populada pelo fusion-service (cargas_fusion/rastreio_cargas_ciss.py), no
-- mesmo ciclo do rastreio. Muitos pra muitos: um romaneio leva pedidos de
-- várias cargas do CISS; a PK é as três colunas.
--
--   IDEMPRESA     = RETIRADA_PRODUTOS.IDEMPRESA  (empresa do cabeçalho da carga)
--   IDCARGACISS   = RETIRADA_PRODUTOS.IDPLANILHA (o "21312456" de "1_21312456")
--   IDCARGAFUSION = romaneio do Fusion (t10_id) = CARGAS_FUSION_TRACK.IDCARGAFUSION
--
-- O serviço espelha o conteúdo atual do romaneio: insere o que falta e
-- remove o que saiu (DELETE pela PK inteira, única tabela com DELETE na
-- allow-list de integrations/ciss_escrita.py).
--
-- Rodar uma vez, como DBA. Nomes seguem as outras *_FUSION_TRACK
-- (PK_<TABELA_SEM_UNDERSCORE>, FK_<...>_CARGAFUSION, tablespace USERSPACE1).
-- Sem acento nos comentários do catálogo: a base não é Unicode.

CREATE TABLE DBA.CARGAS_CISS_FUSION_TRACK (
    IDEMPRESA      INTEGER NOT NULL,
    IDCARGACISS    INTEGER NOT NULL,
    IDCARGAFUSION  INTEGER NOT NULL,
    CONSTRAINT PK_CARGASCISSFUSIONTRACK PRIMARY KEY (IDEMPRESA, IDCARGACISS, IDCARGAFUSION),
    CONSTRAINT FK_CARGASCISSFUSION_CARGAFUSION FOREIGN KEY (IDCARGAFUSION)
        REFERENCES DBA.CARGAS_FUSION_TRACK (IDCARGAFUSION)
) IN USERSPACE1;

-- O serviço lê por romaneio (WHERE IDCARGAFUSION IN (...)); a PK começa por
-- IDEMPRESA e não serve pra isso. Busca pela carga do CISS usa a própria PK.
CREATE INDEX DBA.IX_CARGASCISSFUSION_IDCARGAFUSION
    ON DBA.CARGAS_CISS_FUSION_TRACK (IDCARGAFUSION);

COMMENT ON TABLE DBA.CARGAS_CISS_FUSION_TRACK IS
    'Vinculo romaneio Fusion x carga CISS (RETIRADA_PRODUTOS). Mantida pelo fusion-service.';
COMMENT ON COLUMN DBA.CARGAS_CISS_FUSION_TRACK.IDEMPRESA IS 'Empresa da carga no CISS (RETIRADA_PRODUTOS.IDEMPRESA)';
COMMENT ON COLUMN DBA.CARGAS_CISS_FUSION_TRACK.IDCARGACISS IS 'Carga no CISS (RETIRADA_PRODUTOS.IDPLANILHA)';
COMMENT ON COLUMN DBA.CARGAS_CISS_FUSION_TRACK.IDCARGAFUSION IS 'Romaneio no Fusion (t10_id)';
