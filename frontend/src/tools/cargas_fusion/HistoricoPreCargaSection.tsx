import { LoaderCircle, PackageCheck, Trash2, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { ApiError } from "@/api";
import { useFilaPreCargasFusion, useRemoverDaFilaPreCargas } from "@/tools/cargas_fusion/usePreCargasFusion";
import type { ItemFilaPreCargaFusion } from "@/types";

function mensagemDeErro(erro: unknown): string | null {
  return erro instanceof ApiError ? erro.message : erro ? "Erro inesperado." : null;
}

function formatarData(iso: string | null): string {
  if (!iso) return "—";
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? iso : data.toLocaleString("pt-BR");
}

type Filtro = "todas" | "erros";

function StatusBadge({ item }: { item: ItemFilaPreCargaFusion }) {
  if (item.status === "ENVIADA") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-green-500/10 px-2 py-0.5 text-xs font-medium text-foreground">
        <PackageCheck className="size-3" />
        Enviada
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

function ModoBadge({ modo }: { modo: ItemFilaPreCargaFusion["modo"] }) {
  return (
    <span
      className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-xs font-medium text-muted-foreground"
      title={
        modo === "SOLTA"
          ? "Só saveEntregaServico — pedido livre pra agrupamento manual no Fusion"
          : "Empacotado num cargas[] via saveCarga"
      }
    >
      {modo}
    </span>
  );
}

/** Mesmo papel de `HistoricoSection.tsx`, pra fila de pré-carga — modo
 * atual de produção (`mostrarCargasCiss=false`): a carga real não tem
 * mais tela própria, mas o histórico de envio não pode ficar sem
 * nenhuma, senão falha fica invisível até alguém notar no Fusion. */
export function HistoricoPreCargaSection() {
  const [filtro, setFiltro] = useState<Filtro>("todas");
  const fila = useFilaPreCargasFusion(filtro === "todas");
  const remover = useRemoverDaFilaPreCargas();

  const erro = mensagemDeErro(fila.error) ?? mensagemDeErro(remover.error);
  const itens = fila.data ?? [];

  return (
    <div className="flex flex-col gap-3">
      <p className="text-sm text-muted-foreground">
        Todo envio de pré-carga tentado por esta tool fica aqui — com sucesso (enviada ao
        Fusion) ou com erro (aviso bloqueante ou rejeição do próprio Fusion). "SOLTA" é o
        pedido avulso, pronto pra agrupamento manual em "Formação de carga" no Fusion;
        "BUNDLE" é o modo legado, empacotado num `cargas[]`.
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
          {filtro === "erros" ? "Nenhum erro pendente." : "Nenhum envio de pré-carga registrado ainda."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[40rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th scope="col" className="py-2 pr-3 font-medium">Pré-carga</th>
                <th scope="col" className="py-2 pr-3 font-medium">Modo</th>
                <th scope="col" className="py-2 pr-3 font-medium">Status</th>
                <th scope="col" className="py-2 pr-3 font-medium">Motivo / Quando</th>
                <th scope="col" className="py-2 pr-3 font-medium">Tentativas</th>
                <th scope="col" className="py-2 pr-3 font-medium">
                  <span className="sr-only">Ações</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {itens.map((item) => (
                <tr key={item.id} className="border-b border-border/50">
                  <td className="py-2 pr-3 tabular-nums whitespace-nowrap">
                    {item.id_empresa}_{item.id_precarga}
                  </td>
                  <td className="py-2 pr-3 whitespace-nowrap">
                    <ModoBadge modo={item.modo} />
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
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      disabled={remover.isPending}
                      onClick={() => remover.mutate(item.id)}
                      title={
                        item.status === "ENVIADA"
                          ? "Remove do histórico — a pré-carga volta a poder ser enviada sem forçar"
                          : "Remove da fila"
                      }
                    >
                      <Trash2 />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
