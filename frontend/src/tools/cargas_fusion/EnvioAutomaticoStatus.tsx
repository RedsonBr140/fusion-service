import { LoaderCircle, Pause, Play, Zap } from "lucide-react";

import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import {
  useEnvioAutomaticoEstado,
  useExecutarCicloAgora,
  usePausarEnvioAutomatico,
  useRetomarEnvioAutomatico,
} from "@/tools/cargas_fusion/usePreCargasFusion";

function descreverResultadoCiclo(resultado: {
  status: string;
  motivo?: string;
  candidatas?: number;
  enviadas?: number;
  falhas?: number;
}): string {
  switch (resultado.status) {
    case "executado":
      return `Ciclo executado: ${resultado.candidatas ?? 0} candidata(s), ` +
        `${resultado.enviadas ?? 0} enviada(s), ${resultado.falhas ?? 0} falha(s).`;
    case "pausado":
      return "Ciclo está pausado — retome antes de executar manualmente.";
    case "ciss_nao_configurado":
      return "CISS DB2 não configurado neste ambiente.";
    case "antes_do_corte":
      return "Janela do ciclo cai inteira antes do corte configurado — nada a fazer.";
    case "pulado":
      return `Pulado: ${resultado.motivo ?? "ciclo automático já em andamento"}.`;
    default:
      return `Status: ${resultado.status}`;
  }
}

function formatarData(iso: string | null): string {
  if (!iso) return "nunca";
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? iso : data.toLocaleString("pt-BR");
}

/** Widget pequeno de status/pausa do ciclo automático de envio de
 * pré-carga solta — sempre visível (não depende de `mostrarCargasCiss`,
 * já que o ciclo só lida com pré-carga). O botão Pausar/Retomar é o
 * "kill switch" ao vivo, sem redeploy; o master switch de ambiente
 * (`AUTO_ENVIO_PRE_CARGA_ATIVO`) é outra camada, só no `.env`. */
export function EnvioAutomaticoStatus() {
  const estado = useEnvioAutomaticoEstado();
  const pausar = usePausarEnvioAutomatico();
  const retomar = useRetomarEnvioAutomatico();
  const executarAgora = useExecutarCicloAgora();

  if (estado.isPending || !estado.data) return null;

  const dados = estado.data;
  const pendente = pausar.isPending || retomar.isPending;

  return (
    <div className="flex flex-col gap-2 rounded-md border border-border px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-medium">Envio automático de pré-carga:</span>
        <span className={dados.pausado ? "text-destructive-text" : "text-foreground"}>
          {dados.pausado ? "pausado" : "ativo"}
        </span>
        <span className="text-muted-foreground">
          Último ciclo: {formatarData(dados.ultima_execucao_em)}
          {dados.ultima_execucao_duracao_ms != null && ` (${dados.ultima_execucao_duracao_ms}ms`}
          {dados.ultima_execucao_candidatas != null &&
            `, ${dados.ultima_execucao_candidatas} candidata(s), ${dados.ultima_execucao_enviadas} enviada(s), ${dados.ultima_execucao_falhas} falha(s))`}
        </span>
        {dados.multi_pedido_ignorado.total > 0 && (
          <span
            className="text-muted-foreground"
            title={dados.multi_pedido_ignorado.por_empresa
              .map((e) => `Empresa ${e.id_empresa}: ${e.qtd}`)
              .join(" · ")}
          >
            · {dados.multi_pedido_ignorado.total} pré-carga(s) multi-pedido ignorada(s)
          </span>
        )}
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={pendente}
          onClick={() => (dados.pausado ? retomar.mutate() : pausar.mutate())}
        >
          {pendente ? (
            <LoaderCircle className="animate-spin" />
          ) : dados.pausado ? (
            <Play />
          ) : (
            <Pause />
          )}
          {dados.pausado ? "Retomar" : "Pausar"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={executarAgora.isPending}
          onClick={() => executarAgora.mutate()}
          title="Roda um tick agora, sem esperar o intervalo do scheduler — disputa o mesmo lock do ciclo automático."
        >
          {executarAgora.isPending ? <LoaderCircle className="animate-spin" /> : <Zap />}
          Executar agora
        </Button>
      </div>
      {executarAgora.data && (
        <span className="text-xs text-muted-foreground">{descreverResultadoCiclo(executarAgora.data)}</span>
      )}
      {executarAgora.isError && (
        <span className="text-xs text-destructive-text">
          {executarAgora.error instanceof ApiError
            ? executarAgora.error.message
            : "Falha ao executar o ciclo."}
        </span>
      )}
    </div>
  );
}
