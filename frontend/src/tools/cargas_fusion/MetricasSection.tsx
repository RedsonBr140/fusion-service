import { LoaderCircle } from "lucide-react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useMetricas } from "@/tools/cargas_fusion/usePreCargasFusion";
import type { PontoPorDia } from "@/types";

function StatTile({
  rotulo,
  valor,
  tom = "default",
}: {
  rotulo: string;
  valor: number | string;
  tom?: "default" | "atencao";
}) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-border px-4 py-3">
      <span className="text-xs text-muted-foreground">{rotulo}</span>
      <span className={`text-2xl font-semibold ${tom === "atencao" ? "text-destructive-text" : ""}`}>
        {valor}
      </span>
    </div>
  );
}

/** Barra horizontal simples (sem lib de gráfico — não há nenhuma no
 * projeto, e esta tela não justifica adicionar uma). `dias` já vem
 * ordenado por dia (ASC) do backend; dias sem envio não aparecem na
 * lista — preenchidos aqui como 0 pra não sumir da barra. */
function BarrasPorDia({ pontos, dias }: { pontos: PontoPorDia[]; dias: number }) {
  const porDia = new Map(pontos.map((p) => [p.dia, p.qtd]));
  const hoje = new Date();
  const serie: PontoPorDia[] = [];
  for (let i = dias - 1; i >= 0; i--) {
    const d = new Date(hoje);
    d.setDate(d.getDate() - i);
    const chave = d.toISOString().slice(0, 10);
    serie.push({ dia: chave, qtd: porDia.get(chave) ?? 0 });
  }
  const max = Math.max(1, ...serie.map((p) => p.qtd));

  return (
    <div className="flex h-24 items-end gap-0.5">
      {serie.map((p) => (
        <div key={p.dia} className="group relative flex-1">
          <div
            className="w-full rounded-sm bg-primary/70 transition-colors group-hover:bg-primary"
            style={{ height: `${Math.max(2, (p.qtd / max) * 100)}%` }}
          />
          <div className="pointer-events-none absolute bottom-full left-1/2 mb-1 -translate-x-1/2 whitespace-nowrap rounded bg-popover px-1.5 py-0.5 text-xs text-popover-foreground opacity-0 shadow group-hover:opacity-100">
            {new Date(p.dia).toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })}: {p.qtd}
          </div>
        </div>
      ))}
    </div>
  );
}

/** Métricas de envio ao Fusion — pedido solto é o fluxo corrente (ver
 * README.md), carga empacotada é legado mantido só pra detectar se algo
 * ainda passa por ali. Multi-pedido não é um bug a corrigir: uma entrega
 * solta não tem onde amarrar mais de um pedido, então é contado como
 * volume permanente fora de escopo, não como pendência. */
export function MetricasSection() {
  const dias = 14;
  const metricas = useMetricas(dias);

  if (metricas.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <LoaderCircle className="animate-spin size-4" /> Carregando métricas...
      </p>
    );
  }

  if (!metricas.data) {
    return <p className="text-sm text-destructive-text">Não foi possível carregar as métricas.</p>;
  }

  const { pre_cargas, cargas, multi_pedido } = metricas.data;
  const legadoAtivo = cargas.enviadas_total > 0 || cargas.pendentes > 0 || (pre_cargas.enviadas_por_modo.BUNDLE ?? 0) > 0;

  return (
    <div className="flex flex-col gap-6">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pedido solto (fluxo corrente)</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile rotulo="Enviadas (total)" valor={pre_cargas.enviadas_total} />
            <StatTile rotulo="Pendentes" valor={pre_cargas.pendentes} />
            <StatTile rotulo="Com falha" valor={pre_cargas.com_falha} tom={pre_cargas.com_falha > 0 ? "atencao" : "default"} />
            <StatTile rotulo="Multi-pedido (fora de escopo)" valor={multi_pedido.total} />
          </div>
          <div>
            <p className="mb-1 text-xs text-muted-foreground">Enviadas por dia (últimos {dias} dias)</p>
            <BarrasPorDia pontos={pre_cargas.enviadas_por_dia} dias={dias} />
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Pré-cargas multi-pedido</CardTitle>
          <p className="text-sm text-muted-foreground">
            Fora do escopo do envio automático pra sempre — uma entrega solta
            (<code>saveEntregaServico</code> sem <code>saveCarga</code>) não tem
            como amarrar mais de um pedido. Não é uma pendência de bug; é só a
            contagem de quanto isso acontece.
          </p>
        </CardHeader>
        <CardContent className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile rotulo="Total já visto" valor={multi_pedido.total} />
            {multi_pedido.por_empresa.slice(0, 3).map((e) => (
              <StatTile key={e.id_empresa} rotulo={`Empresa ${e.id_empresa}`} valor={e.qtd} />
            ))}
          </div>
          {multi_pedido.total > 0 && (
            <div>
              <p className="mb-1 text-xs text-muted-foreground">Vistas pela 1ª vez por dia (últimos {dias} dias)</p>
              <BarrasPorDia pontos={multi_pedido.por_dia} dias={dias} />
            </div>
          )}
        </CardContent>
      </Card>

      {legadoAtivo && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Carga empacotada (caminho legado)</CardTitle>
            <p className="text-sm text-muted-foreground">
              Algo passou por aqui mesmo com o escopo padrão sendo pedido
              solto — vale checar <code>MOSTRAR_CARGAS_CISS</code>/
              <code>SOMENTE_PEDIDOS_SOLTOS</code> neste ambiente.
            </p>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              <StatTile rotulo="Pré-carga em modo BUNDLE" valor={pre_cargas.enviadas_por_modo.BUNDLE ?? 0} />
              <StatTile rotulo="Carga real enviada" valor={cargas.enviadas_total} />
              <StatTile rotulo="Carga real pendente" valor={cargas.pendentes} />
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
