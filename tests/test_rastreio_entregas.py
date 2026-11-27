"""Entregas do rastreio (cargas_fusion/rastreio_entregas.py): formato do
código do pedido → NF no CISS, detalheEntrega → STATUSENTREGA, gravação
em DBA.ENTREGAS_FUSION_TRACK. Tudo dublê — sem Fusion, sem DB2."""

from datetime import datetime

import fakeredis
import pytest

from cargas_fusion import rastreio, rastreio_entregas
from cargas_fusion.rastreio_entregas import identificar_pedido
from database import connect, init_database
from integrations.ciss import CissDbUnavailable
from integrations.ciss_escrita import COLUNAS_ENTREGAS_FUSION_TRACK, SQL_MERGE_ENTREGAS_FUSION_TRACK, validate_escrita_permitida

AGORA = datetime(2026, 11, 26, 12, 0)


@pytest.fixture
def conn(tmp_path):
    init_database(tmp_path / "e.db")
    c = connect(tmp_path / "e.db")
    yield c
    c.close()


# --- Formatos ------------------------------------------------------------


@pytest.mark.parametrize(
    "codigo, filial, esperado",
    [
        ("21956497@1@0@1", "1", ("nativo", {"id_pre_nota": 21956497, "id_empresa": 1})),
        ("613768_22105494", "2", ("carga", {"id_orcamento": 613768, "id_planilha_carga": 22105494})),
        ("625240_22276368R1", "1", ("carga", {"id_orcamento": 625240, "id_planilha_carga": 22276368})),
        ("616205_PC796273", "2", ("pre_carga", {"id_orcamento": 616205, "id_precarga": 796273})),
        ("PN22171369_PC796450", "9", ("pre_nota", {"id_pre_nota": 22171369, "id_empresa": 9})),
        ("PN22171369_22171000", "9", ("pre_nota", {"id_pre_nota": 22171369, "id_empresa": 9})),
    ],
)
def test_identifica_cada_formato_visto_em_producao(codigo, filial, esperado):
    pedido = identificar_pedido(codigo, filial)
    formato, campos = esperado
    assert pedido.formato == formato
    for nome, valor in campos.items():
        assert getattr(pedido, nome) == valor


@pytest.mark.parametrize("codigo, filial", [("TESTE_CLAUDE_001", "1"), ("", "1"), (None, "1"), ("PN123_PC1", "")])
def test_formato_desconhecido(codigo, filial):
    assert identificar_pedido(codigo, filial) is None


def test_merge_de_entregas_passa_na_guarda():
    validate_escrita_permitida(SQL_MERGE_ENTREGAS_FUSION_TRACK)


# --- Dublês ----------------------------------------------------------------

NF = {"IDEMPRESA": 2, "IDPLANILHA": 22156452, "NUMNOTA": 167888}


class CissFalso:
    """`db2`: lista de linhas que "já estão" em ENTREGAS_FUSION_TRACK —
    aponte pra `EscritaFalsa.linhas` pra simular o round-trip de verdade."""

    def __init__(self, nfs=None, erro=None, db2=None, erro_leitura=None):
        self.nfs = [NF] if nfs is None else nfs
        self.erro = erro
        self.chamadas = []
        self.db2 = db2 if db2 is not None else []
        self.erro_leitura = erro_leitura
        self.leituras = 0

    def ler_track(self, tabela, colunas, coluna_filtro, valores):
        self.leituras += 1
        if self.erro_leitura:
            raise self.erro_leitura
        return [dict(linha) for linha in self.db2 if linha.get(coluna_filtro) in set(valores)]

    def _r(self, nome, *args, **kw):
        self.chamadas.append((nome, args, kw))
        if self.erro:
            raise self.erro
        return self.nfs

    def get_nfs_por_orcamento(self, *a, **kw):
        return self._r("orcamento", *a, **kw)

    def get_nfs_por_pre_nota_retirada(self, *a):
        return self._r("retirada", *a)

    def get_nfs_por_pre_nota_direta(self, *a):
        return self._r("direta", *a)

    def get_cargas_ciss_do_pedido(self, formato, **kw):
        return []


class FusionFalso:
    def __init__(self, detalhe=None):
        self.detalhe = {"status": "1", "fusion_romaneio_id": "816622", "observacao": "LIGAR ANTES",
                        "data_prevista_entrega_atualizada": "27/10/2026 07:32"} if detalhe is None else detalhe
        self.chamadas = []

    def detalhe_entrega(self, pedido):
        self.chamadas.append(pedido)
        return self.detalhe


class EscritaFalsa:
    def __init__(self, erro=None):
        self.erro = erro
        self.linhas = []

    def upsert_entrega_fusion_track(self, linha):
        if self.erro:
            raise self.erro
        self.linhas.append(linha)

    def sincronizar_cargas_ciss(self, id_carga_fusion, inserir, remover=()):
        pass


def _romaneio(conn, t10_id=816622, entregas=None, codigo_int=1, **extra):
    item = {
        "CODIGO_INT": str(codigo_int), "TIPO_INT": "T10_ROMANEIO_MONTAGEM_CARGA", "t10_id": str(t10_id),
        "t43_codigo_filial_erp": "2", "t05_codigo_erp": "1", "t10_data_saida": "2026-11-25 07:00:00",
        "ENTREGAS": entregas if entregas is not None else [{
            "t32_pedido_original": "616205_PC796273", "t43_codigo_filial_erp": "2", "t32_nf": "0",
            "t12_data_prevista_entrega": "2026-10-27 07:32:06", "t12_data_real_entrega": None,
            "t12_ordemEntrega": "3",
        }],
    }
    item.update(extra)
    rastreio._registrar_recebidos(conn, [item])
    conn.execute("UPDATE cargas_fusion_int_erp SET status = 'INTERNALIZADO' WHERE codigo_int = ?", (codigo_int,))
    conn.commit()


def _processar(conn, ciss=None, fusion=None, escrita=None, alertas=None, **kw):
    return rastreio_entregas.processar_entregas(
        conn, ciss or CissFalso(), fusion or FusionFalso(), escrita or EscritaFalsa(),
        alertas if alertas is not None else [], agora=kw.pop("agora", AGORA), **kw
    )


def _situacoes(conn):
    return {r["pedido_erp"]: (r["situacao"], r["status_entrega"]) for r in conn.execute("SELECT * FROM cargas_fusion_entrega_track")}


# --- Ciclo -----------------------------------------------------------------


def test_grava_uma_linha_por_nf_com_status_do_detalhe_entrega(conn):
    _romaneio(conn)
    escrita = EscritaFalsa()
    ciss = CissFalso(nfs=[NF, {"IDEMPRESA": 2, "IDPLANILHA": 22156999, "NUMNOTA": 167900}])

    resultado = _processar(conn, ciss=ciss, escrita=escrita)

    assert resultado["GRAVADA"] == 1
    assert [l["IDPLANILHA"] for l in escrita.linhas] == [22156452, 22156999]
    linha = escrita.linhas[0]
    assert set(linha) == {n for n, _ in COLUNAS_ENTREGAS_FUSION_TRACK}
    assert linha["IDCARGAFUSION"] == 816622 and linha["NUMNOTA"] == 167888 and linha["IDEMPRESA"] == 2
    assert linha["STATUSENTREGA"] == 1
    assert linha["DATAPREVISTAENTREGA"] == datetime(2026, 10, 27, 7, 32, 6)
    assert linha["DATAPREVISTAENTREGAATUALIZADA"] == datetime(2026, 10, 27, 7, 32)
    assert linha["OBERSERVACAOENTREGA"] == "LIGAR ANTES"
    assert linha["IDENTREGAFUSION"] == 3  # sequência no romaneio (t12_ordemEntrega)
    assert escrita.linhas[1]["IDENTREGAFUSION"] == 3  # mesma entrega, outra NF
    assert linha["IDDEVOLUCAO"] is None
    assert ciss.chamadas[0][0] == "orcamento" and ciss.chamadas[0][2] == {"id_precarga": 796273}


def test_nf_nunca_vem_do_t32_nf_do_fusion(conn):
    """t32_nf é "0" em toda pré-carga — a NF sai do CISS."""
    _romaneio(conn)
    escrita = EscritaFalsa()
    _processar(conn, escrita=escrita)
    assert escrita.linhas[0]["NUMNOTA"] == 167888


def test_sem_nf_no_ciss_fica_sem_nf_e_retenta(conn):
    _romaneio(conn)
    ciss = CissFalso(nfs=[])
    _processar(conn, ciss=ciss)
    assert _situacoes(conn) == {"616205_PC796273": ("SEM_NF", None)}

    ciss.nfs = [NF]
    _processar(conn, ciss=ciss)
    assert _situacoes(conn) == {"616205_PC796273": ("GRAVADA", 1)}


def test_nf_resolvida_fica_em_cache(conn):
    _romaneio(conn)
    ciss = CissFalso()
    _processar(conn, ciss=ciss)
    _processar(conn, ciss=ciss)  # romaneio não terminado → reatualiza
    assert len(ciss.chamadas) == 1


def test_romaneio_nao_terminado_e_reatualizado(conn):
    _romaneio(conn)
    fusion = FusionFalso()
    escrita = EscritaFalsa()
    _processar(conn, fusion=fusion, escrita=escrita)
    fusion.detalhe = dict(fusion.detalhe, status="7")
    _processar(conn, fusion=fusion, escrita=escrita)

    assert [l["STATUSENTREGA"] for l in escrita.linhas] == [1, 7]
    # Toda entrega concluída (7) → terminado, não chama mais.
    _processar(conn, fusion=fusion, escrita=escrita)
    assert len(fusion.chamadas) == 2


def test_romaneio_com_data_de_termino_grava_uma_vez_so(conn):
    _romaneio(conn, t10_data_termino="2026-09-12 07:41:42")
    fusion = FusionFalso()
    _processar(conn, fusion=fusion)
    _processar(conn, fusion=fusion)
    assert len(fusion.chamadas) == 1


def test_romaneio_expirado_nao_e_reatualizado(conn):
    _romaneio(conn, t10_data_saida="2026-09-01 07:00:00")
    fusion = FusionFalso()
    _processar(conn, fusion=fusion)  # primeira gravação acontece mesmo assim
    _processar(conn, fusion=fusion)
    assert len(fusion.chamadas) == 1


def test_entrega_em_outro_romaneio_nao_grava(conn):
    _romaneio(conn)
    escrita = EscritaFalsa()
    _processar(conn, fusion=FusionFalso({"status": "1", "fusion_romaneio_id": "999"}), escrita=escrita)
    assert escrita.linhas == []
    assert _situacoes(conn)["616205_PC796273"][0] == "OUTRO_ROMANEIO"


def test_pedido_nao_encontrado_no_fusion_e_erro_com_alerta(conn):
    _romaneio(conn)
    alertas = []
    _processar(conn, fusion=FusionFalso(detalhe={}), alertas=alertas)  # {} → tratado abaixo
    fusion = FusionFalso()
    fusion.detalhe = None
    _romaneio(conn, t10_id=2, codigo_int=2, entregas=[{"t32_pedido_original": "1_2", "t43_codigo_filial_erp": "1"}])
    _processar(conn, fusion=fusion, alertas=alertas)
    assert _situacoes(conn)["1_2"][0] == "ERRO"
    assert any("1_2" in a for a in alertas)


def test_falha_no_db2_e_erro_e_retenta(conn):
    _romaneio(conn)
    escrita = EscritaFalsa(erro=CissDbUnavailable("SQL0530N"))
    _processar(conn, escrita=escrita)
    assert _situacoes(conn)["616205_PC796273"][0] == "ERRO"
    escrita.erro = None
    _processar(conn, escrita=escrita)
    assert _situacoes(conn)["616205_PC796273"][0] == "GRAVADA"


def test_formato_desconhecido_e_ignorado_sem_chamar_ninguem(conn):
    _romaneio(conn, entregas=[{"t32_pedido_original": "TESTE_CLAUDE_001", "t43_codigo_filial_erp": "1"}])
    ciss, fusion = CissFalso(), FusionFalso()
    _processar(conn, ciss=ciss, fusion=fusion)
    _processar(conn, ciss=ciss, fusion=fusion)
    assert _situacoes(conn) == {"TESTE_CLAUDE_001": ("IGNORADA", None)}
    assert ciss.chamadas == [] and fusion.chamadas == []


def test_limite_por_ciclo_e_restantes(conn):
    _romaneio(conn, entregas=[{"t32_pedido_original": f"{i}_22161374", "t43_codigo_filial_erp": "1"} for i in range(5)])
    resultado = _processar(conn, limite=2)
    # O dublê dá a mesma NF pra todo pedido (mesma linha no DB2): a 2ª do
    # lote já encontra a 1ª gravada e conta como SEM_MUDANCA.
    assert resultado.get("GRAVADA", 0) + resultado.get("SEM_MUDANCA", 0) == 2
    assert resultado["restantes"] == 3


def test_sem_nf_nao_mata_de_fome_a_reatualizacao(conn):
    """Prioridade: nunca processadas, depois reatualização, depois retentativa."""
    _romaneio(conn, entregas=[{"t32_pedido_original": "1_10", "t43_codigo_filial_erp": "1"},
                              {"t32_pedido_original": "2_10", "t43_codigo_filial_erp": "1"}])
    ciss = CissFalso()
    _processar(conn, ciss=ciss)  # as duas gravadas
    conn.execute("UPDATE cargas_fusion_entrega_track SET situacao='SEM_NF', nfs=NULL WHERE pedido_erp='2_10'")
    conn.commit()
    fusion = FusionFalso()
    _processar(conn, ciss=ciss, fusion=fusion, limite=1)
    assert fusion.chamadas == ["1_10"]


def test_so_romaneio_internalizado_entra(conn):
    """FK no DB2: sem linha em CARGAS_FUSION_TRACK, entrega não pode existir."""
    _romaneio(conn)
    conn.execute("UPDATE cargas_fusion_int_erp SET status = 'ERRO'")
    conn.commit()
    assert _processar(conn) == {"restantes": 0}


def test_ciclo_completo_grava_entregas_depois_do_romaneio(conn):
    from tests.test_rastreio import EscritaFalsa as EscritaCargas, FusionFalso as FusionCiclo, _pendencia

    class Escrita(EscritaCargas):
        def __init__(self):
            super().__init__()
            self.entregas = []

        def upsert_entrega_fusion_track(self, linha):
            self.entregas.append(linha)

    class Fusion(FusionCiclo):
        def detalhe_entrega(self, pedido):
            return {"status": "1", "fusion_romaneio_id": "823636"}

    item = _pendencia(10, ENTREGAS=[{"t32_pedido_original": "616205_PC796273", "t43_codigo_filial_erp": "2"}])
    escrita = Escrita()
    resultado = rastreio.executar_ciclo(
        conn, fakeredis.FakeRedis(), Fusion([[item]]), escrita, discord_webhook_url=None,
        lock_timeout_segundos=60, ciss_client=CissFalso(),
    )

    assert resultado["entregas"]["GRAVADA"] == 1
    assert len(escrita.linhas) == 1 and escrita.entregas[0]["IDCARGAFUSION"] == 823636


def test_formato_desconhecido_e_outro_romaneio_viram_aviso(conn):
    _romaneio(conn, entregas=[{"t32_pedido_original": "TESTE_CLAUDE_001", "t43_codigo_filial_erp": "1"},
                              {"t32_pedido_original": "1_10", "t43_codigo_filial_erp": "1"}])
    avisos = []
    fusion = FusionFalso({"status": "1", "fusion_romaneio_id": "999"})
    rastreio_entregas.processar_entregas(conn, CissFalso(), fusion, EscritaFalsa(), [], avisos, agora=AGORA)
    rastreio_entregas.processar_entregas(conn, CissFalso(), fusion, EscritaFalsa(), [], avisos, agora=AGORA)
    assert len(avisos) == 2  # uma vez cada, nunca repete
    assert any("TESTE_CLAUDE_001" in a for a in avisos) and any("romaneio 999" in a for a in avisos)
