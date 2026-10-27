import { LoaderCircle } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useExecucoesEnvioAutomatico } from "@/tools/cargas_fusion/usePreCargasFusion";
import type { ExecucaoEnvioAutomatico } from "@/types";

function formatarHora(iso: string): string {
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? iso : data.toLocaleString("pt-BR", { timeStyle: "short", dateStyle: "short" });
}

function descricaoBreakdown(execucao: ExecucaoEnvioAutomatico): string {
  return Object.entries(execucao.breakdown)
    .sort((a, b) => b[1].duracao_ms - a[1].duracao_ms)
    .map(([metodo, dado]) => `${metodo}: ${dado.duracao_ms}ms (${dado.chamadas}x)`)
    .join("\n");
}

/** Duração total de cada ciclo, mais recente à direita — tooltip mostra
 * o breakdown por método daquele ciclo específico, pra responder "por
 * quê" sem precisar de um gráfico empilhado. */
function DuracaoPorCiclo({ execucoes }: { execucoes: ExecucaoEnvioAutomatico[] }) {
  const max = Math.max(1, ...execucoes.map((e) => e.duracao_ms));
  return (
    <div className="flex h-28 items-end gap-0.5">
      {execucoes.map((e, i) => (
        <div key={i} className="group relative flex-1">
          <div
            className="w-full rounded-sm bg-primary/70 transition-colors group-hover:bg-primary"
            style={{ height: `${Math.max(2, (e.duracao_ms / max) * 100)}%` }}
          />
          <div className="pointer-events-none absolute bottom-full left-1/2 z-10 mb-1 w-max -translate-x-1/2 whitespace-pre rounded bg-popover px-2 py-1 text-xs text-popover-foreground opacity-0 shadow group-hover:opacity-100">
            {formatarHora(e.executado_em)} · {e.duracao_ms}ms · {e.candidatas} candidata(s)
            {"\n"}
            {descricaoBreakdown(e) || "(nenhuma chamada registrada)"}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Ranking de etapas (método de client) por tempo total somado em todos
 * os ciclos carregados — a resposta direta pra "qual etapa demora mais,
 * no geral". Uma chamada contada muitas vezes com média alta aponta
 * N+1 (ex. `ciss.get_cliente_fusion` ou `portal.cliente_cadastrado` por
 * item), não só uma chamada isolada lenta. */
function RankingEtapas({ execucoes }: { execucoes: ExecucaoEnvioAutomatico[] }) {
  const agregado = new Map<string, { duracao_ms: number; chamadas: number }>();
  for (const execucao of execucoes) {
    for (const [metodo, dado] of Object.entries(execucao.breakdown)) {
      const atual = agregado.get(metodo) ?? { duracao_ms: 0, chamadas: 0 };
      atual.duracao_ms += dado.duracao_ms;
      atual.chamadas += dado.chamadas;
      agregado.set(metodo, atual);
    }
  }
  const linhas = [...agregado.entries()].sort((a, b) => b[1].duracao_ms - a[1].duracao_ms);
  const max = Math.max(1, ...linhas.map(([, v]) => v.duracao_ms));

  if (linhas.length === 0) {
    return <p className="text-sm text-muted-foreground">Sem execuções registradas ainda.</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      {linhas.map(([metodo, dado]) => (
        <div key={metodo} className="flex items-center gap-2 text-sm">
          <span className="w-56 shrink-0 truncate font-mono text-xs" title={metodo}>
            {metodo}
          </span>
          <div className="h-3 flex-1 rounded-sm bg-muted">
            <div
              className="h-full rounded-sm bg-primary/70"
              style={{ width: `${Math.max(2, (dado.duracao_ms / max) * 100)}%` }}
            />
          </div>
          <span className="w-40 shrink-0 text-right text-xs text-muted-foreground">
            {dado.duracao_ms}ms total · {dado.chamadas}x · {(dado.duracao_ms / dado.chamadas).toFixed(0)}ms/chamada
          </span>
        </div>
      ))}
    </div>
  );
}

/** As pré-cargas mais lentas entre os ciclos carregados, com o método
 * que dominou o tempo de cada uma — a resposta pra "por quê *esta*
 * pré-carga específica foi lenta", não só a média geral. */
function ItensLentos({ execucoes }: { execucoes: ExecucaoEnvioAutomatico[] }) {
  const itens = execucoes
    .flatMap((e) => e.itens_lentos.map((item) => ({ ...item, executado_em: e.executado_em })))
    .sort((a, b) => b.duracao_ms - a.duracao_ms)
    .slice(0, 15);

  if (itens.length === 0) {
    return <p className="text-sm text-muted-foreground">Nenhum item processado ainda.</p>;
  }

  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border text-left text-xs text-muted-foreground">
          <th className="py-1 pr-2">Quando</th>
          <th className="py-1 pr-2">Pré-carga</th>
          <th className="py-1 pr-2">Duração</th>
          <th className="py-1">Etapa dominante</th>
        </tr>
      </thead>
      <tbody>
        {itens.map((item, i) => (
          <tr key={i} className="border-b border-border/50">
            <td className="py-1 pr-2 text-muted-foreground">{formatarHora(item.executado_em)}</td>
            <td className="py-1 pr-2 font-mono text-xs">
              {item.id_empresa}/{item.id_precarga}
            </td>
            <td className="py-1 pr-2">{item.duracao_ms}ms</td>
            <td className="py-1 font-mono text-xs text-muted-foreground">
              {item.metodo_dominante ? `${item.metodo_dominante} (${item.duracao_dominante_ms}ms)` : "—"}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** Diagnóstico de desempenho do ciclo automático de envio de pré-carga
 * solta — qual etapa (chamada a CISS/Fusion/portal) consome mais tempo
 * e quais pré-cargas específicas puxaram a média pra cima. Instrumentado
 * por proxy em volta dos clients (`auto_envio._ClienteCronometrado`),
 * sem nenhuma mudança na lógica de negócio em `service.py`. */
export function DiagnosticoSection() {
  const execucoes = useExecucoesEnvioAutomatico(50);

  if (execucoes.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <LoaderCircle className="animate-spin size-4" /> Carregando diagnóstico...
      </p>
    );
  }

  if (!execucoes.data) {
    return <p className="text-sm text-destructive-text">Não foi possível carregar o diagnóstico.</p>;
  }

  if (execucoes.data.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        Nenhuma execução do ciclo automático registrada ainda.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Duração por ciclo (últimos {execucoes.data.length})</CardTitle>
          <p className="text-sm text-muted-foreground">
            Passe o mouse numa barra pra ver o breakdown por etapa daquele ciclo.
          </p>
        </CardHeader>
        <CardContent>
          <DuracaoPorCiclo execucoes={execucoes.data} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Ranking de etapas (tempo total somado)</CardTitle>
        </CardHeader>
        <CardContent>
          <RankingEtapas execucoes={execucoes.data} />
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pré-cargas mais lentas</CardTitle>
        </CardHeader>
        <CardContent>
          <ItensLentos execucoes={execucoes.data} />
        </CardContent>
      </Card>
    </div>
  );
}
