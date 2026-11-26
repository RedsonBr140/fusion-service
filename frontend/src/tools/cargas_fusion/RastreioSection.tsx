import { CheckCircle2, Clock, Database, LoaderCircle, TriangleAlert, Zap } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useExecutarRastreioAgora, useItemRastreio, useRastreio } from "@/tools/cargas_fusion/useRastreio";
import type { EntregaRastreio, EventoRastreio, ItemRastreio, ResultadoCicloRastreio, StatusRastreio } from "@/types";

const STATUS: StatusRastreio[] = ["RECEBIDO", "INTERNALIZADO", "SUPERADO", "IGNORADO", "ERRO"];

function formatarData(iso: string | null): string {
  if (!iso) return "—";
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? iso : data.toLocaleString("pt-BR");
}

function descreverCiclo(r: ResultadoCicloRastreio): string {
  if (r.status === "pulado") return `Pulado: ${r.motivo ?? "ciclo já em andamento"}.`;
  if (r.status === "nao_configurado") return "Fusion ou CISS DB2 não configurado neste ambiente.";
  const proc = Object.entries(r.processadas ?? {})
    .map(([s, n]) => `${n} ${s.toLowerCase()}`)
    .join(", ");
  return (
    `Ciclo executado em ${r.duracao_ms ?? 0}ms: ${r.recebidas ?? 0} recebida(s) do getIntErp` +
    (proc ? ` · ${proc}` : "") +
    ` · ${r.confirmadas ?? 0} confirmada(s) no setIntErp` +
    (r.falhas_confirmacao ? ` · ${r.falhas_confirmacao} falha(s) de confirmação` : "") +
    (r.entregas
      ? ` · entregas: ${Object.entries(r.entregas)
          .filter(([s]) => s !== "restantes")
          .map(([s, n]) => `${n} ${s.toLowerCase()}`)
          .join(", ") || "nada a fazer"}` +
        (r.entregas.restantes ? ` (${r.entregas.restantes} pra próximos ciclos)` : "")
      : "") +
    (r.eventos
      ? ` · eventos: ${r.eventos.recebidos ?? 0} consumido(s) do Fusion` +
        (Object.entries(r.eventos)
          .filter(([s]) => s !== "restantes" && s !== "recebidos")
          .map(([s, n]) => `, ${n} ${s.toLowerCase()}`)
          .join("")) +
        (r.eventos.restantes ? ` (${r.eventos.restantes} pra próximos ciclos)` : "")
      : "") +
    (r.erro_coleta ? ` · ${r.erro_coleta}` : "")
  );
}

function StatusBadge({ status }: { status: StatusRastreio }) {
  const estilo =
    status === "ERRO"
      ? "bg-destructive/10 text-destructive-text"
      : status === "INTERNALIZADO"
        ? "bg-green-500/10 text-foreground"
        : "bg-muted text-muted-foreground";
  return <span className={`rounded-full px-2 py-0.5 text-xs font-medium ${estilo}`}>{status}</span>;
}

function ConfirmacaoBadge({ item }: { item: Pick<ItemRastreio, "status" | "confirmado_em" | "confirmacao_erro"> }) {
  if (item.confirmado_em) {
    return (
      <span className="inline-flex items-center gap-1 text-xs" title={formatarData(item.confirmado_em)}>
        <CheckCircle2 className="size-3" /> confirmado
      </span>
    );
  }
  if (item.status !== "INTERNALIZADO" && item.status !== "SUPERADO") {
    return <span className="text-xs text-muted-foreground">—</span>;
  }
  return (
    <span
      className="inline-flex items-center gap-1 text-xs text-destructive-text"
      title={item.confirmacao_erro ?? undefined}
    >
      <Clock className="size-3" /> {item.confirmacao_erro ? "falhou" : "pendente"}
    </span>
  );
}

function formatarValor(valor: unknown): string {
  if (valor === null || valor === undefined) return "NULL";
  if (typeof valor === "object") return JSON.stringify(valor);
  return String(valor);
}

function TabelaEntregas({ entregas }: { entregas: EntregaRastreio[] }) {
  if (entregas.length === 0) {
    return <p className="text-sm text-muted-foreground">Nenhuma entrega processada ainda pra este romaneio.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground">
            <th scope="col" className="py-1 pr-3 font-medium">Pedido (Fusion)</th>
            <th scope="col" className="py-1 pr-3 font-medium">Formato</th>
            <th scope="col" className="py-1 pr-3 font-medium">NF no CISS (empresa · IDPLANILHA · NUMNOTA)</th>
            <th scope="col" className="py-1 pr-3 font-medium">Status Fusion</th>
            <th scope="col" className="py-1 pr-3 font-medium">Situação</th>
            <th scope="col" className="py-1 pr-3 font-medium">Atualizada</th>
          </tr>
        </thead>
        <tbody>
          {entregas.map((e) => (
            <tr key={e.pedido_erp} className="border-b border-border/50 align-top">
              <td className="py-1 pr-3 font-mono">{e.pedido_erp}</td>
              <td className="py-1 pr-3">{e.formato ?? "—"}</td>
              <td className="py-1 pr-3 font-mono">
                {e.nfs.length === 0
                  ? "—"
                  : e.nfs.map((nf) => `${nf.IDEMPRESA} · ${nf.IDPLANILHA} · ${nf.NUMNOTA}`).join(" / ")}
              </td>
              <td className="py-1 pr-3 font-mono">{e.status_entrega ?? "—"}</td>
              <td className={`py-1 pr-3 ${e.situacao === "ERRO" ? "text-destructive-text" : ""}`} title={e.erro ?? undefined}>
                {e.situacao}
                {e.erro && e.situacao !== "GRAVADA" ? ` — ${e.erro}` : ""}
              </td>
              <td className="py-1 pr-3">{formatarData(e.atualizado_em)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function TabelaEventos({ eventos }: { eventos: EventoRastreio[] }) {
  if (eventos.length === 0) {
    return <p className="text-sm text-muted-foreground">Nenhum evento recebido ainda pra este romaneio.</p>;
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground">
            <th scope="col" className="py-1 pr-3 font-medium">Quando</th>
            <th scope="col" className="py-1 pr-3 font-medium">Evento</th>
            <th scope="col" className="py-1 pr-3 font-medium">Pedido</th>
            <th scope="col" className="py-1 pr-3 font-medium">Motivo</th>
            <th scope="col" className="py-1 pr-3 font-medium">Itens devolvidos</th>
            <th scope="col" className="py-1 pr-3 font-medium">Situação</th>
          </tr>
        </thead>
        <tbody>
          {eventos.map((e) => (
            <tr key={e.t15_id} className="border-b border-border/50 align-top">
              <td className="py-1 pr-3 whitespace-nowrap">{formatarData(e.data_hora)}</td>
              <td className="py-1 pr-3">
                <span className="font-mono text-muted-foreground">{e.tipo ?? "?"}</span> {e.descricao ?? "—"}
              </td>
              <td className="py-1 pr-3 font-mono">{e.pedido_erp ?? "—"}</td>
              <td className="py-1 pr-3">{e.motivo ?? "—"}</td>
              <td className="py-1 pr-3 font-mono">{e.itens_gravados ?? "—"}</td>
              <td className={`py-1 pr-3 ${e.status === "ERRO" ? "text-destructive-text" : ""}`} title={e.erro ?? undefined}>
                {e.status}
                {e.erro ? ` — ${e.erro}` : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Linha mapeada (o que mandamos) ao lado da linha lida de volta do CISS
 * (o que de fato ficou gravado) — diferença destacada. */
function ComparacaoLinhas({
  mapeada,
  ciss,
}: {
  mapeada: Record<string, unknown> | null;
  ciss: Record<string, unknown> | null;
}) {
  const colunas = Object.keys(mapeada ?? ciss ?? {});
  if (colunas.length === 0) return null;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground">
            <th scope="col" className="py-1 pr-3 font-medium">Coluna</th>
            <th scope="col" className="py-1 pr-3 font-medium">Mapeada (enviada ao CISS)</th>
            <th scope="col" className="py-1 pr-3 font-medium">Gravada em CARGAS_FUSION_TRACK</th>
          </tr>
        </thead>
        <tbody>
          {colunas.map((coluna) => {
            const a = formatarValor(mapeada?.[coluna]);
            const b = ciss ? formatarValor(ciss[coluna]) : "—";
            const difere = ciss !== null && mapeada !== null && !valoresEquivalentes(mapeada[coluna], ciss[coluna]);
            return (
              <tr key={coluna} className="border-b border-border/50 align-top">
                <td className="py-1 pr-3 font-mono">{coluna}</td>
                <td className="py-1 pr-3 font-mono break-all">{a}</td>
                <td className={`py-1 pr-3 font-mono break-all ${difere ? "text-destructive-text" : ""}`}>{b}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** Decimal chega como string com casas diferentes ("20.0000000" vs
 * "20"), data com ou sem fração — compara pelo valor, não pelo texto. */
function valoresEquivalentes(a: unknown, b: unknown): boolean {
  if (a === null || a === undefined) return b === null || b === undefined;
  if (b === null || b === undefined) return false;
  const na = Number(a);
  const nb = Number(b);
  if (!Number.isNaN(na) && !Number.isNaN(nb) && String(a).trim() !== "" && String(b).trim() !== "") {
    return na === nb;
  }
  const da = Date.parse(String(a));
  const db = Date.parse(String(b));
  if (!Number.isNaN(da) && !Number.isNaN(db)) return da === db;
  return String(a).trim() === String(b).trim();
}

function DetalheItem({ id, onFechar }: { id: number; onFechar: () => void }) {
  const detalhe = useItemRastreio(id);
  const [mostrarPayload, setMostrarPayload] = useState(false);

  return (
    <div className="flex flex-col gap-3 rounded-md border border-border p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium">
          Pendência {detalhe.data?.codigo_int ?? "…"} · romaneio {detalhe.data?.t10_id ?? "…"}
        </span>
        <Button type="button" size="sm" variant="ghost" onClick={onFechar}>
          Fechar
        </Button>
      </div>
      {detalhe.isPending && <LoaderCircle className="animate-spin size-4" />}
      {detalhe.data && (
        <>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <StatusBadge status={detalhe.data.status} />
            <span className="text-muted-foreground">{detalhe.data.tipo_int}</span>
            <ConfirmacaoBadge item={detalhe.data} />
          </div>
          {detalhe.data.erro && <p className="text-sm text-destructive-text">{detalhe.data.erro}</p>}
          {detalhe.data.motivo_mapeamento && (
            <p className="text-sm text-destructive-text">Mapeamento: {detalhe.data.motivo_mapeamento}</p>
          )}
          {detalhe.data.confirmacao_erro && (
            <p className="text-sm text-destructive-text">setIntErp: {detalhe.data.confirmacao_erro}</p>
          )}
          {detalhe.data.erro_ciss && <p className="text-sm text-destructive-text">{detalhe.data.erro_ciss}</p>}
          {!detalhe.data.erro_ciss && detalhe.data.linha_ciss === null && detalhe.data.linha_mapeada && (
            <p className="text-sm text-muted-foreground">Romaneio ainda não existe em CARGAS_FUSION_TRACK.</p>
          )}
          <ComparacaoLinhas mapeada={detalhe.data.linha_mapeada} ciss={detalhe.data.linha_ciss} />
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium">Entregas → ENTREGAS_FUSION_TRACK</span>
            <TabelaEntregas entregas={detalhe.data.entregas} />
          </div>
          <div className="flex flex-col gap-1">
            <span className="text-sm font-medium">Eventos → EVENTOS_FUSION_TRACK</span>
            <TabelaEventos eventos={detalhe.data.eventos ?? []} />
          </div>
          <div>
            <Button type="button" size="xs" variant="outline" onClick={() => setMostrarPayload((v) => !v)}>
              {mostrarPayload ? "Esconder" : "Ver"} payload cru do Fusion
            </Button>
            {mostrarPayload && (
              <pre className="mt-2 max-h-96 overflow-auto rounded bg-muted p-2 text-xs">
                {JSON.stringify(detalhe.data.payload, null, 2)}
              </pre>
            )}
          </div>
        </>
      )}
    </div>
  );
}

/** Rastreio Fusion → CISS: fila `getIntErp` gravada em
 * `DBA.CARGAS_FUSION_TRACK`, confirmada com `setIntErp`. Atualiza sozinho
 * a cada 5s. "Executar agora" é destrutivo do lado do Fusion (getIntErp
 * tira da fila o que devolve) — por isso pede confirmação. */
export function RastreioSection() {
  const [filtro, setFiltro] = useState<StatusRastreio | undefined>(undefined);
  const [selecionado, setSelecionado] = useState<number | null>(null);
  const [confirmando, setConfirmando] = useState(false);
  const lista = useRastreio(filtro);
  const executar = useExecutarRastreioAgora();

  const dados = lista.data;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <span className="inline-flex items-center gap-1 font-medium">
          <Database className="size-4" /> getIntErp → CARGAS_FUSION_TRACK
        </span>
        {dados && (
          <span className={dados.ativo ? "text-foreground" : "text-destructive-text"}>
            {!dados.ativo
              ? "desligado (RASTREIO_INT_ERP_ATIVO=false)"
              : dados.automatico
                ? `automático — roda sozinho a cada ${Math.round(dados.intervalo_segundos / 60)} min`
                : "manual — só roda pelo botão (RASTREIO_INT_ERP_AUTOMATICO=false)"}
          </span>
        )}
        {lista.isFetching && <LoaderCircle className="animate-spin size-3 text-muted-foreground" />}
        <Button
          type="button"
          size="sm"
          disabled={!dados?.ativo || executar.isPending}
          onClick={() => setConfirmando(true)}
        >
          {executar.isPending ? <LoaderCircle className="animate-spin" /> : <Zap />}
          Executar ciclo agora
        </Button>
      </div>

      {executar.isPending && (
        <p className="text-sm text-muted-foreground">Consumindo a fila do Fusion e gravando no CISS…</p>
      )}
      {executar.data && <p className="text-sm">{descreverCiclo(executar.data)}</p>}
      {executar.isError && (
        <p className="text-sm text-destructive-text">
          {executar.error instanceof ApiError ? executar.error.message : "Falha ao executar o ciclo."}
        </p>
      )}

      {dados && (
        <div className="flex flex-wrap gap-2">
          <Button
            type="button"
            size="xs"
            variant={filtro === undefined ? "default" : "outline"}
            onClick={() => setFiltro(undefined)}
          >
            Todas ({Object.values(dados.contagem).reduce((a, b) => a + (b ?? 0), 0)})
          </Button>
          {STATUS.map((s) => (
            <Button
              key={s}
              type="button"
              size="xs"
              variant={filtro === s ? "default" : "outline"}
              onClick={() => setFiltro(s)}
            >
              {s} ({dados.contagem[s] ?? 0})
            </Button>
          ))}
          {Object.keys(dados.entregas_contagem).length > 0 && (
            <span className="text-xs text-muted-foreground">
              · Entregas:{" "}
              {Object.entries(dados.entregas_contagem)
                .map(([s, n]) => `${n} ${s.toLowerCase()}`)
                .join(", ")}
            </span>
          )}
          {Object.keys(dados.eventos_contagem ?? {}).length > 0 && (
            <span className="text-xs text-muted-foreground">
              · Eventos:{" "}
              {Object.entries(dados.eventos_contagem)
                .map(([s, n]) => `${n} ${s.toLowerCase()}`)
                .join(", ")}
            </span>
          )}
          {dados.nao_confirmadas > 0 && (
            <span className="inline-flex items-center gap-1 text-xs text-destructive-text">
              <TriangleAlert className="size-3" /> {dados.nao_confirmadas} gravada(s) sem confirmação no Fusion
            </span>
          )}
        </div>
      )}

      {selecionado !== null && <DetalheItem id={selecionado} onFechar={() => setSelecionado(null)} />}

      {lista.isError && <p className="text-sm text-destructive-text">Falha ao carregar o rastreio.</p>}
      {dados && dados.itens.length === 0 && (
        <p className="text-sm text-muted-foreground">Nenhuma pendência recebida ainda.</p>
      )}
      {dados && dados.itens.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[44rem] text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th scope="col" className="py-2 pr-3 font-medium">CODIGO_INT</th>
                <th scope="col" className="py-2 pr-3 font-medium">Tipo</th>
                <th scope="col" className="py-2 pr-3 font-medium">Romaneio</th>
                <th scope="col" className="py-2 pr-3 font-medium">Status</th>
                <th scope="col" className="py-2 pr-3 font-medium">setIntErp</th>
                <th scope="col" className="py-2 pr-3 font-medium">Recebida</th>
                <th scope="col" className="py-2 pr-3 font-medium">Erro</th>
              </tr>
            </thead>
            <tbody>
              {dados.itens.map((item) => (
                <tr
                  key={item.id}
                  className={`cursor-pointer border-b border-border/50 hover:bg-muted/50 ${selecionado === item.id ? "bg-muted/50" : ""}`}
                  onClick={() => setSelecionado(item.id)}
                >
                  <td className="py-2 pr-3 font-mono">{item.codigo_int ?? "—"}</td>
                  <td className="py-2 pr-3 text-xs">{item.tipo_int?.replace("T10_ROMANEIO_", "") ?? "—"}</td>
                  <td className="py-2 pr-3 font-mono">{item.t10_id ?? "—"}</td>
                  <td className="py-2 pr-3">
                    <StatusBadge status={item.status} />
                    {item.tentativas > 1 && (
                      <span className="ml-1 text-xs text-muted-foreground">×{item.tentativas}</span>
                    )}
                  </td>
                  <td className="py-2 pr-3">
                    <ConfirmacaoBadge item={item} />
                  </td>
                  <td className="py-2 pr-3 text-xs">{formatarData(item.recebido_em)}</td>
                  <td className="max-w-[16rem] truncate py-2 pr-3 text-xs text-destructive-text" title={item.erro ?? undefined}>
                    {item.erro ?? ""}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <Dialog open={confirmando} onOpenChange={setConfirmando}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Consumir a fila do Fusion agora?</DialogTitle>
            <DialogDescription>
              O <strong>getIntErp</strong> é destrutivo: toda pendência que ele devolver sai da fila do
              Fusion na hora. Cada uma fica guardada aqui, é gravada em{" "}
              <strong>DBA.CARGAS_FUSION_TRACK</strong> (CISS DB2) e confirmada com{" "}
              <strong>setIntErp</strong>.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmando(false)}>
              Cancelar
            </Button>
            <Button
              type="button"
              onClick={() => {
                setConfirmando(false);
                executar.mutate();
              }}
            >
              <Zap /> Executar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
