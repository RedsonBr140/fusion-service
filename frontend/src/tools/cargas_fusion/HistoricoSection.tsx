import { ChevronDown, ChevronRight, LoaderCircle, PackageCheck, Trash2, TriangleAlert } from "lucide-react";
import { Fragment, useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/api";
import { PedidosParaEnvio } from "@/tools/cargas_fusion/BuscaCargasSection";
import { useCargaPreview } from "@/tools/cargas_fusion/useCargasFusion";
import { useFila, useRemoverDaFila } from "@/tools/cargas_fusion/useFilaCargasFusion";
import type { ItemFilaFusion } from "@/types";

function mensagemDeErro(erro: unknown): string | null {
  return erro instanceof ApiError ? erro.message : erro ? "Erro inesperado." : null;
}

function formatarData(iso: string | null): string {
  if (!iso) return "—";
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? iso : data.toLocaleString("pt-BR");
}

type Filtro = "todas" | "erros";

function StatusBadge({ item }: { item: ItemFilaFusion }) {
  if (item.status === "ENVIADA") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-500/10 px-2 py-0.5 text-xs font-medium text-foreground">
        <PackageCheck className="size-3" />
        {item.integrada_manualmente ? "Marcada manualmente" : "Enviada"}
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-destructive/10 px-2 py-0.5 text-xs font-medium text-destructive-text">
      <TriangleAlert className="size-3" />
      Erro
    </span>
  );
}

/** O detalhe de uma linha do histórico, carregado só quando a linha é
 * expandida. Mostra os avisos completos (`motivo` na tabela é só o
 * primeiro, truncado) e os itens — lidos ao vivo do CISS, não o payload
 * exato do momento do envio (este serviço não guarda isso): pode já ter
 * mudado desde então, mas é a melhor aproximação disponível sem abrir a
 * carga na aba de busca. */
function DetalheHistorico({ item }: { item: ItemFilaFusion }) {
  const preview = useCargaPreview({ idEmpresa: item.id_empresa, idPlanilha: item.id_planilha });

  return (
    <div className="flex flex-col gap-3 px-3 py-3">
      {item.avisos.length > 0 && (
        <div className="flex flex-col gap-1">
          <p className="text-xs font-medium text-muted-foreground">Avisos deste envio</p>
          <ul className="list-inside list-disc text-xs text-muted-foreground">
            {item.avisos.map((aviso, i) => (
              <li key={i}>{aviso}</li>
            ))}
          </ul>
        </div>
      )}

      {preview.isPending ? (
        <p className="flex items-center gap-2 text-xs text-muted-foreground">
          <LoaderCircle className="animate-spin size-3.5" /> Carregando itens...
        </p>
      ) : preview.error ? (
        <p className="text-xs text-destructive-text">
          Não foi possível carregar os itens (a carga pode não existir mais no CISS).
        </p>
      ) : (
        <PedidosParaEnvio
          tipo="carga"
          itens={preview.data?.itens ?? []}
          selecionados={null}
          somenteLeitura
        />
      )}
    </div>
  );
}

interface Props {
  onAbrirCarga: (idEmpresa: number, idPlanilha: number) => void;
}

/** O que já foi "fila" virou histórico: toda tentativa de envio fica
 * registrada, com sucesso ou não — não só a pendente de resolução. O
 * filtro "Erros" existe pra voltar ao que a fila mostrava antes. */
export function HistoricoSection({ onAbrirCarga }: Props) {
  const [filtro, setFiltro] = useState<Filtro>("todas");
  const [expandido, setExpandido] = useState<number | null>(null);
  const fila = useFila(filtro === "todas");
  const remover = useRemoverDaFila();

  const erro = mensagemDeErro(fila.error) ?? mensagemDeErro(remover.error);
  const itens = fila.data ?? [];

  function alternarExpandido(id: number) {
    setExpandido((atual) => (atual === id ? null : id));
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        Todo envio tentado por esta tool fica aqui — com sucesso (enviada ao Fusion, ou
        marcada manualmente como já integrada) ou com erro (aviso bloqueante ou rejeição do
        próprio Fusion).
      </p>

      <div className="flex w-fit gap-2 rounded-md border border-border p-1">
        <Button
          type="button"
          size="sm"
          variant={filtro === "todas" ? "default" : "ghost"}
          onClick={() => setFiltro("todas")}
        >
          Todas
        </Button>
        <Button
          type="button"
          size="sm"
          variant={filtro === "erros" ? "default" : "ghost"}
          onClick={() => setFiltro("erros")}
        >
          Só erros
        </Button>
      </div>

      {erro && (
        <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
          {erro}
        </p>
      )}

      {fila.isPending ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="animate-spin size-4" /> Carregando...
        </p>
      ) : itens.length === 0 ? (
        <p className="text-sm text-muted-foreground">
          {filtro === "erros" ? "Nenhum erro pendente." : "Nenhum envio registrado ainda."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th scope="col" className="py-2 pr-3 font-medium">
                  <span className="sr-only">Expandir</span>
                </th>
                <th scope="col" className="py-2 pr-3 font-medium">Carga</th>
                <th scope="col" className="py-2 pr-3 font-medium">Status</th>
                <th scope="col" className="py-2 pr-3 font-medium">Motivo / Quando</th>
                <th scope="col" className="py-2 pr-3 font-medium">Tentativas</th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  <span className="sr-only">Ações</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {itens.map((item) => {
                const aberto = expandido === item.id;
                return (
                  <Fragment key={item.id}>
                    <tr className="border-b border-border/50">
                      <td className="py-2 pl-1">
                        <button
                          type="button"
                          onClick={() => alternarExpandido(item.id)}
                          className="text-muted-foreground"
                          title="Ver avisos e itens deste envio"
                        >
                          {aberto ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
                        </button>
                      </td>
                      <td className="py-2 pr-3 tabular-nums whitespace-nowrap">
                        {item.id_empresa}_{item.id_planilha}
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <StatusBadge item={item} />
                      </td>
                      <td className="w-full max-w-0 py-2 pr-3">
                        {item.status === "ENVIADA" ? (
                          <div className="truncate text-muted-foreground">
                            {formatarData(item.enviado_em)}
                          </div>
                        ) : (
                          <div className="truncate" title={item.motivo ?? ""}>
                            {item.motivo}
                          </div>
                        )}
                      </td>
                      <td className="py-2 pr-3 text-center tabular-nums">{item.tentativas}</td>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <div className="flex gap-2">
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            onClick={() => onAbrirCarga(item.id_empresa, item.id_planilha)}
                          >
                            Ver
                          </Button>
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon-sm"
                            disabled={remover.isPending}
                            onClick={() => remover.mutate(item.id)}
                            title={
                              item.status === "ENVIADA"
                                ? "Remove do histórico — a carga volta a poder ser enviada sem forçar"
                                : "Remove da fila"
                            }
                          >
                            <Trash2 />
                          </Button>
                        </div>
                      </td>
                    </tr>
                    {aberto && (
                      <tr className="border-b border-border/50 bg-muted/30">
                        <td colSpan={6}>
                          <DetalheHistorico item={item} />
                        </td>
                      </tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
