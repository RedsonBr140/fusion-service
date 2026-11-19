"""Única fronteira do projeto com o `fusion-py` (biblioteca própria do
Redson, ainda sem release pública — instalada local/editable a partir de
`../fusion-py`, não via PyPI). Nada com formato de `fusion-py` atravessa
este arquivo em direção a `core/` ou a uma tool, mesmo princípio de
`integrations/ciss_auth.py` e `integrations/fusion.py`.

`fusion-py` fala com o **portal web** do Fusion DMS — login humano
(`/login.html`, sessão por cookie `PHPSESSID`), não o webservice SOAP que
`integrations/fusion.py` usa. Usado aqui para coisas que o SOAP não
oferece: conferir se um cliente já está cadastrado (não há
`consultarCliente` no WSDL) antes de `sendClientes`, e ler o cadastro de
motorista e de veículo direto do Fusion (não há `consultarMotorista` no
WSDL; `consultarVeiculo` existe no SOAP, mas o portal também cobre
veículo e segue o mesmo padrão de motorista — nenhum dos dois tem mais
tabela local, ver `cargas_fusion/service.py`).

Veículo (`buscar_veiculos`, fusion-py) não tem campo de filtro confirmado
na grade do portal (`get_transportes.php`) — a captura usada para
desenhar o serviço listou tudo sem `campo`/`valor`. Por isso a busca por
placa/termo aqui é sempre "lista tudo, filtra em Python", diferente de
motorista (`buscar_motoristas`), que filtra no próprio servidor via
`CAMPO_NOME`/`CAMPO_CODIGO_ERP`.

Confirmado ao vivo (2026-10-20): o host certo é o padrão da biblioteca
(`fusiondms.com.br`) — **não** `int.fusiondms.com.br` (o host do webservice
SOAP, `FUSION_API_URL`). Esse segundo host loga, mas devolve uma resposta
sem `token`/`cnpj` e não dá acesso de grade (`cadcli`/`cadmot` redirecionam
pra fora).
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from tempfile import gettempdir
from typing import TYPE_CHECKING

from fusion import FusionClient as _FusionPyClient
from fusion import Motorista as _FusionPyMotorista
from fusion import Veiculo as _FusionPyVeiculo
from fusion.config import FusionConfig as _FusionPyConfig
from fusion.exceptions import FusionError as _FusionPyError
from fusion.services.clientes import CAMPO_CODIGO_ERP as _CAMPO_CLIENTE_CODIGO_ERP
from fusion.services.motoristas import CAMPO_CODIGO_ERP as _CAMPO_MOTORISTA_CODIGO_ERP
from fusion.services.motoristas import CAMPO_NOME as _CAMPO_MOTORISTA_NOME

if TYPE_CHECKING:
    from config import FusionPortalConfig

logger = logging.getLogger(__name__)

_DOWNLOAD_DIR = Path(gettempdir()) / "fusion_portal_downloads"


class FusionPortalError(Exception):
    """Falha ao consultar o portal web do Fusion."""


def _motorista_para_dict(motorista: _FusionPyMotorista) -> dict[str, object]:
    """Mesmas chaves que `_pessoa_local_para_envio_fusion`/`montar_payload_*`
    (`tools/cargas_fusion/service.py`) esperavam da antiga linha local de
    `fusion_motoristas` — assim o dado vindo direto do Fusion encaixa sem
    tradução extra em quem já consumia o cadastro local."""
    return {
        "codigo_erp": motorista.codigo_erp,
        "nome": motorista.nome,
        "cpf": motorista.cpf or "",
        "telefone": motorista.telefone or "",
        "endereco": motorista.endereco or "",
        "uf": motorista.uf or "",
        "cidade": motorista.cidade or "",
        "cep": motorista.cep or "",
    }


def _normalizar_placa(placa: str) -> str:
    """Mesma normalização de `cargas_fusion/service.py::normalizar_placa`
    (sem hífen/espaço, maiúscula) — duplicada aqui, não importada de lá,
    porque `integrations/` não depende de `cargas_fusion/` (a fronteira só
    fala com o Fusion, nunca o contrário)."""
    return re.sub(r"[^A-Z0-9]", "", (placa or "").upper())


def _veiculo_para_dict(veiculo: _FusionPyVeiculo) -> dict[str, object]:
    """Mesmas chaves que `veiculo_local_para_envio_fusion`
    (`cargas_fusion/service.py`) espera de uma linha de veículo — assim o
    dado vindo direto do Fusion encaixa sem tradução extra em quem antes
    consumia o cadastro local (`fusion_veiculos`, retirado — veículo segue
    motorista e passa a não ter mais tabela própria)."""
    return {
        "codigo_erp": veiculo.codigo_erp,
        "placa": veiculo.placa,
        "descricao": veiculo.descricao,
        "modelo": veiculo.modelo,
        "ano_modelo": veiculo.ano_modelo or None,
        "ano_fabricacao": veiculo.ano_fabricacao or None,
    }


class FusionPortalClient:
    """Sessão cacheada por até `_TTL_SESSAO_SEGUNDOS` (1h) — achado em
    produção, 2026-11-19: a versão anterior ("descartável por chamada",
    login novo a cada consulta) assumia uso raro, só antes de um envio
    manual, mas a tela hoje refaz a prévia (e com ela `motorista_sugerido`/
    `veiculo_sugerido`, 2 logins) a cada mudança de seleção — 82 logins
    contra o portal em 12 minutos de uso normal, cada um ~1.3-1.4s, dando a
    impressão de "estar sendo rate-limitado" pelo Fusion quando na verdade
    éramos nós relogando sem necessidade. O portal em si respondeu normal
    nos testes (sem 429, sem erro) — o problema era só volume/latência
    autoinfligida.

    Reusa a mesma sessão logada entre chamadas (processo inteiro, não por
    request — `self` vive em `app.config["FUSION_PORTAL_CLIENT"]`, uma
    instância por worker do gunicorn). Se uma chamada falhar com a sessão
    cacheada (pode ter expirado do lado do Fusion antes da hora, ou sido
    encerrada por outro motivo), descarta e tenta de novo com login novo —
    só propaga erro se a segunda tentativa também falhar. `_lock` serializa
    todo uso (login e as chamadas que usam a sessão) porque `fusion-py` não
    documenta ser thread-safe; isso é seguro aqui porque o volume por
    processo é baixo e o gargalo de qualquer forma é rede, não CPU."""

    _TTL_SESSAO_SEGUNDOS = 3600

    def __init__(self, config: "FusionPortalConfig") -> None:
        self._config = config
        self._lock = threading.Lock()
        self._cliente: _FusionPyClient | None = None
        self._login_em: float | None = None

    def _login_novo(self) -> _FusionPyClient:
        # `_FusionPyError` sobe crua daqui de propósito — `_com_sessao` é
        # quem decide reentar/embrulhar, pra uma falha de login (não só de
        # ação) também ganhar a segunda tentativa.
        cliente = _FusionPyClient(_DOWNLOAD_DIR, config=_FusionPyConfig(base_url=self._config.url))
        cliente.login(self._config.login, self._config.senha)
        return cliente

    def _descartar_sessao_sem_lock(self) -> None:
        if self._cliente is not None:
            try:
                self._cliente.close()
            except Exception:
                logger.warning("Falha ao fechar sessão antiga do portal Fusion — ignorada.")
        self._cliente = None
        self._login_em = None

    def _sessao_sem_lock(self) -> _FusionPyClient:
        agora = time.monotonic()
        expirada = self._login_em is None or (agora - self._login_em) >= self._TTL_SESSAO_SEGUNDOS
        if self._cliente is None or expirada:
            self._descartar_sessao_sem_lock()
            self._cliente = self._login_novo()
            self._login_em = agora
        return self._cliente

    def _com_sessao(self, acao: Callable[[_FusionPyClient], object]) -> object:
        """Executa `acao(cliente)` com a sessão cacheada; numa falha — de
        login ou da própria ação —, descarta a sessão e tenta mais uma vez
        com login novo. Só propaga `FusionPortalError` se a segunda
        tentativa também falhar."""
        with self._lock:
            for ultima_tentativa in (False, True):
                try:
                    cliente = self._sessao_sem_lock()
                    return acao(cliente)
                except _FusionPyError as exc:
                    self._descartar_sessao_sem_lock()
                    if ultima_tentativa:
                        raise FusionPortalError(str(exc)) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    def cliente_cadastrado(self, codigo_erp: str) -> bool:
        """True se já existe cliente com este `codigo_erp` no Fusion."""
        pagina = self._com_sessao(lambda cliente: cliente.buscar_clientes(_CAMPO_CLIENTE_CODIGO_ERP, codigo_erp))
        return pagina.total > 0

    def motorista_por_codigo_erp(self, codigo_erp: str) -> dict[str, object] | None:
        """O motorista com este `codigo_erp`, ou `None` se não cadastrado."""
        pagina = self._com_sessao(
            lambda cliente: cliente.buscar_motoristas(_CAMPO_MOTORISTA_CODIGO_ERP, codigo_erp)
        )
        return _motorista_para_dict(pagina.rows[0]) if pagina.rows else None

    def buscar_motoristas(self, termo: str, *, rows: int = 20) -> list[dict[str, object]]:
        """Busca por nome — usado no picker manual da tela (escolha de
        motorista, tanto em carga real quanto em pré-carga)."""
        pagina = self._com_sessao(lambda cliente: cliente.buscar_motoristas(_CAMPO_MOTORISTA_NOME, termo, rows=rows))
        return [_motorista_para_dict(m) for m in pagina.rows]

    def veiculo_por_codigo_erp(self, codigo_erp: str) -> dict[str, object] | None:
        """O veículo com este `codigo_erp`, ou `None` se não cadastrado —
        mesmo papel de `motorista_por_codigo_erp`, pra escolha manual na
        tela. Mesma ressalva de `veiculo_por_placa`: sem filtro server-side
        confirmado, lista tudo e compara aqui."""
        pagina = self._com_sessao(lambda cliente: cliente.buscar_veiculos(rows=250))
        for veiculo in pagina.rows:
            if veiculo.codigo_erp == codigo_erp:
                return _veiculo_para_dict(veiculo)
        return None

    def veiculo_por_placa(self, placa: str) -> dict[str, object] | None:
        """O veículo com esta placa, ou `None` se não cadastrado. Sem campo
        de filtro confirmado na grade (`buscar_veiculos` do fusion-py lista
        tudo), então busca a lista inteira e compara a placa normalizada
        aqui — mesmo critério de `cargas_fusion/service.py::normalizar_placa`."""
        placa_norm = _normalizar_placa(placa)
        pagina = self._com_sessao(lambda cliente: cliente.buscar_veiculos(rows=250))
        for veiculo in pagina.rows:
            if _normalizar_placa(veiculo.placa) == placa_norm:
                return _veiculo_para_dict(veiculo)
        return None

    def buscar_veiculos(self, termo: str, *, rows: int = 250) -> list[dict[str, object]]:
        """Busca por placa ou descrição — usado no picker manual da tela,
        igual a `buscar_motoristas`. Sem filtro server-side confirmado: lista
        até `rows` veículos e filtra pelo termo aqui (case-insensitive,
        substring)."""
        termo_norm = termo.strip().lower()
        if not termo_norm:
            return []
        pagina = self._com_sessao(lambda cliente: cliente.buscar_veiculos(rows=rows))
        return [
            _veiculo_para_dict(veiculo)
            for veiculo in pagina.rows
            if termo_norm in (veiculo.placa or "").lower()
            or termo_norm in (veiculo.descricao or "").lower()
        ]
