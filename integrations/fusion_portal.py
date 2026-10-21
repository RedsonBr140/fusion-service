"""Única fronteira do projeto com o `fusion-py` (biblioteca própria do
Redson, ainda sem release pública — instalada local/editable a partir de
`../fusion-py`, não via PyPI). Nada com formato de `fusion-py` atravessa
este arquivo em direção a `core/` ou a uma tool, mesmo princípio de
`integrations/ciss_auth.py` e `integrations/fusion.py`.

`fusion-py` fala com o **portal web** do Fusion DMS — login humano
(`/login.html`, sessão por cookie `PHPSESSID`), não o webservice SOAP que
`integrations/fusion.py` usa. Usado aqui para duas coisas que o SOAP não
oferece: conferir se um cliente já está cadastrado (não há
`consultarCliente` no WSDL) antes de `sendClientes`, e ler o cadastro de
motorista direto do Fusion (não há `consultarMotorista` no WSDL, e desde
2026-10-20 o Fusion passou a ser a única fonte — ver `tools/cargas_fusion/
service.py`, motorista não tem mais tabela local).

Confirmado ao vivo (2026-10-20): o host certo é o padrão da biblioteca
(`fusiondms.com.br`) — **não** `int.fusiondms.com.br` (o host do webservice
SOAP, `FUSION_API_URL`). Esse segundo host loga, mas devolve uma resposta
sem `token`/`cnpj` e não dá acesso de grade (`cadcli`/`cadmot` redirecionam
pra fora).
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import gettempdir
from typing import TYPE_CHECKING

from fusion import FusionClient as _FusionPyClient
from fusion import Motorista as _FusionPyMotorista
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


class FusionPortalClient:
    """Descartável por chamada, como `integrations.fusion.FusionClient`:
    loga de novo a cada consulta — a frequência de uso (antes de um envio
    manual de carga ou pré-carga) não justifica manter sessão viva entre
    chamadas."""

    def __init__(self, config: "FusionPortalConfig") -> None:
        self._config = config

    @contextmanager
    def _sessao(self) -> Iterator[_FusionPyClient]:
        cliente = _FusionPyClient(_DOWNLOAD_DIR, config=_FusionPyConfig(base_url=self._config.url))
        try:
            cliente.login(self._config.login, self._config.senha)
            yield cliente
        except _FusionPyError as exc:
            raise FusionPortalError(str(exc)) from exc
        finally:
            cliente.close()

    def cliente_cadastrado(self, codigo_erp: str) -> bool:
        """True se já existe cliente com este `codigo_erp` no Fusion."""
        with self._sessao() as cliente:
            try:
                pagina = cliente.buscar_clientes(_CAMPO_CLIENTE_CODIGO_ERP, codigo_erp)
            except _FusionPyError as exc:
                raise FusionPortalError(str(exc)) from exc
        return pagina.total > 0

    def motorista_por_codigo_erp(self, codigo_erp: str) -> dict[str, object] | None:
        """O motorista com este `codigo_erp`, ou `None` se não cadastrado."""
        with self._sessao() as cliente:
            try:
                pagina = cliente.buscar_motoristas(_CAMPO_MOTORISTA_CODIGO_ERP, codigo_erp)
            except _FusionPyError as exc:
                raise FusionPortalError(str(exc)) from exc
        return _motorista_para_dict(pagina.rows[0]) if pagina.rows else None

    def buscar_motoristas(self, termo: str, *, rows: int = 20) -> list[dict[str, object]]:
        """Busca por nome — usado no picker manual da tela (escolha de
        motorista, tanto em carga real quanto em pré-carga)."""
        with self._sessao() as cliente:
            try:
                pagina = cliente.buscar_motoristas(_CAMPO_MOTORISTA_NOME, termo, rows=rows)
            except _FusionPyError as exc:
                raise FusionPortalError(str(exc)) from exc
        return [_motorista_para_dict(m) for m in pagina.rows]
