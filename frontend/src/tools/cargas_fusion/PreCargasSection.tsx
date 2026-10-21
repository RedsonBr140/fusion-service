import {
  ChevronDown,
  ChevronRight,
  LoaderCircle,
  PackageCheck,
  Search,
  Send,
  Truck,
} from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/api";
import { useVeiculos } from "@/tools/cargas_fusion/useCadastroFusion";
import { useEmpresasCargasFusion } from "@/tools/cargas_fusion/useCargasFusion";
import { MotoristaPickerFusion } from "@/tools/cargas_fusion/MotoristaPickerFusion";
import {
  useBuscarPreCargas,
  useEnviarPreCarga,
  usePreCargaPreview,
} from "@/tools/cargas_fusion/usePreCargasFusion";
import type { ItemPreCargaFusion, MotoristaFusion, PreCargaResumo } from "@/types";

function mensagemDeErro(erro: unknown): string | null {
  return erro instanceof ApiError ? erro.message : erro ? "Erro inesperado." : null;
}

function agoraMenos(horas: number): string {
  const data = new Date(Date.now() - horas * 3600_000);
  return data.toISOString().slice(0, 19);
}

function formatarData(iso: string | null): string {
  if (!iso) return "—";
  const data = new Date(iso);
  return Number.isNaN(data.getTime()) ? iso : data.toLocaleString("pt-BR");
}

function formatarMoeda(valor: number | null): string {
  return (valor ?? 0).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatarNumero(valor: number | null, casasDecimais = 1): string {
  return (valor ?? 0).toLocaleString("pt-BR", { maximumFractionDigits: casasDecimais });
}

interface PedidoAgrupado {
  idPlanilhaDocOri: number;
  numeroPedido: number;
  numeros: number[];
  valorTotal: number;
  pesoTotal: number;
  itens: ItemPreCargaFusion[];
}

function agruparPorPedido(itens: ItemPreCargaFusion[]): PedidoAgrupado[] {
  const grupos = new Map<number, ItemPreCargaFusion[]>();
  for (const item of itens) {
    if (item.IDORCAMENTO == null) continue;
    const grupo = grupos.get(item.IDPLANILHADOCORI);
    if (grupo) grupo.push(item);
    else grupos.set(item.IDPLANILHADOCORI, [item]);
  }
  return Array.from(grupos.entries()).map(([idPlanilhaDocOri, linhas]) => ({
    idPlanilhaDocOri,
    numeroPedido: linhas[0].IDORCAMENTO as number,
    numeros: linhas.map((l) => l.NUMSEQUENCIA),
    valorTotal: linhas.reduce((soma, l) => soma + (l.VALTOTLIQUIDO ?? 0), 0),
    pesoTotal: linhas.reduce((soma, l) => soma + (l.PESOBRUTO ?? 0), 0),
    itens: linhas,
  }));
}

export function PreCargasSection() {
  const [dataInicial, setDataInicial] = useState(agoraMenos(24));
  const [dataFinal, setDataFinal] = useState(agoraMenos(0));
  const [filtroEmpresaId, setFiltroEmpresaId] = useState<number | null>(null);
  const [ocultarEnviadas, setOcultarEnviadas] = useState(true);
  const [selecionada, setSelecionada] = useState<{ idEmpresa: number; idPrecarga: number } | null>(
    null
  );
  const [motoristaEscolhido, setMotoristaEscolhido] = useState<MotoristaFusion | null>(null);
  const [veiculoId, setVeiculoId] = useState<number | null>(null);
  const [itensSelecionados, setItensSelecionados] = useState<Set<number> | null>(null);
  const [confirmarReenvio, setConfirmarReenvio] = useState(false);
  const [expandidos, setExpandidos] = useState<Set<number>>(new Set());

  const busca = useBuscarPreCargas();
  const empresas = useEmpresasCargasFusion();
  const veiculos = useVeiculos();
  const preview = usePreCargaPreview(selecionada, {
    motoristaCodigoErp: motoristaEscolhido?.codigo_erp ?? undefined,
    veiculoId: veiculoId ?? undefined,
    itens: itensSelecionados ? Array.from(itensSelecionados) : undefined,
  });
  const enviar = useEnviarPreCarga();

  const preCargas = busca.data ?? [];
  const erro =
    mensagemDeErro(busca.error) ??
    mensagemDeErro(preview.error) ??
    mensagemDeErro(enviar.error) ??
    mensagemDeErro(veiculos.error) ??
    mensagemDeErro(empresas.error);

  function buscar() {
    setSelecionada(null);
    enviar.reset();
    busca.mutate({ dataInicial, dataFinal, idEmpresa: filtroEmpresaId, ocultarEnviadas });
  }

  function selecionar(preCarga: PreCargaResumo) {
    enviar.reset();
    setMotoristaEscolhido(null);
    setVeiculoId(null);
    setItensSelecionados(null);
    setConfirmarReenvio(false);
    setSelecionada({ idEmpresa: preCarga.IDEMPRESA, idPrecarga: preCarga.IDPRECARGA });
  }

  function alternarPedido(numerosDoPedido: number[], todos: ItemPreCargaFusion[]) {
    const efetivos = itensSelecionados ?? new Set(todos.map((i) => i.NUMSEQUENCIA));
    const novo = new Set(efetivos);
    const pedidoCompleto = numerosDoPedido.every((n) => novo.has(n));
    for (const numero of numerosDoPedido) {
      if (pedidoCompleto) novo.delete(numero);
      else novo.add(numero);
    }
    setItensSelecionados(novo);
  }

  function alternarExpandido(id: number) {
    const novo = new Set(expandidos);
    if (novo.has(id)) novo.delete(id);
    else novo.add(id);
    setExpandidos(novo);
  }

  function enviarPreCarga(forcar = false) {
    if (!selecionada) return;
    enviar.mutate({
      ...selecionada,
      selecao: {
        motoristaCodigoErp: motoristaEscolhido?.codigo_erp ?? undefined,
        veiculoId: veiculoId ?? undefined,
        itens: itensSelecionados ? Array.from(itensSelecionados) : undefined,
      },
      forcar,
    });
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs text-muted-foreground">
        A pré-carga ainda não tem motorista/veículo no CISS — escolha os dois aqui antes de
        enviar. Quando o CISS consolidar esta pré-carga numa carga real, ela não vai ser
        reenviada na aba "Buscar cargas".
      </p>

      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="pc-data-inicial">De</Label>
          <Input
            id="pc-data-inicial"
            type="datetime-local"
            value={dataInicial}
            onChange={(e) => setDataInicial(e.target.value)}
            className="w-56"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="pc-data-final">Até</Label>
          <Input
            id="pc-data-final"
            type="datetime-local"
            value={dataFinal}
            onChange={(e) => setDataFinal(e.target.value)}
            className="w-56"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="pc-filtro-empresa">Empresa</Label>
          <select
            id="pc-filtro-empresa"
            className="h-9 w-48 rounded-md border border-input bg-transparent px-3 text-sm"
            value={filtroEmpresaId ?? ""}
            onChange={(e) => setFiltroEmpresaId(e.target.value ? Number(e.target.value) : null)}
          >
            <option value="">Todas</option>
            {(empresas.data ?? []).map((e) => (
              <option key={e.id_empresa} value={e.id_empresa}>
                {e.nome}
              </option>
            ))}
          </select>
        </div>
        <Button type="button" onClick={buscar} disabled={busca.isPending}>
          {busca.isPending ? <LoaderCircle className="animate-spin" /> : <Search />}
          Buscar
        </Button>
      </div>

      <label className="flex w-fit items-center gap-2 text-sm text-foreground">
        <input
          type="checkbox"
          className="size-4 accent-primary"
          checked={ocultarEnviadas}
          onChange={(e) => setOcultarEnviadas(e.target.checked)}
        />
        Não mostrar pré-cargas já enviadas
      </label>

      {erro && (
        <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
          {erro}
        </p>
      )}

      {busca.isSuccess && (
        <div className="overflow-x-auto">
          {preCargas.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nenhuma pré-carga encontrada no período.</p>
          ) : (
            <table className="w-full min-w-[32rem] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th scope="col" className="py-2 pr-3 font-medium">Empresa / Pré-carga</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Descrição</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Movimento</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Conferida</th>
                </tr>
              </thead>
              <tbody>
                {preCargas.map((preCarga) => {
                  const marcada =
                    selecionada?.idEmpresa === preCarga.IDEMPRESA &&
                    selecionada?.idPrecarga === preCarga.IDPRECARGA;
                  return (
                    <tr
                      key={`${preCarga.IDEMPRESA}-${preCarga.IDPRECARGA}`}
                      onClick={() => selecionar(preCarga)}
                      className={
                        "cursor-pointer border-b border-border/50 " +
                        (marcada ? "bg-accent" : "hover:bg-muted/50")
                      }
                    >
                      <td className="py-2 pr-3 tabular-nums whitespace-nowrap">
                        {preCarga.IDEMPRESA}_{preCarga.IDPRECARGA}
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        {preCarga.DESCRPRECARGA || "—"}
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">
                        {formatarData(preCarga.DTMOVIMENTO)}
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        {preCarga.FLAGCONFERIDA === "T" ? "Sim" : "Não"}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {selecionada && (
        <div className="flex flex-col gap-3 rounded-md border border-border p-4">
          <p className="flex items-center gap-2 text-sm font-medium text-foreground">
            Pré-carga {selecionada.idEmpresa}_{selecionada.idPrecarga}
            <span className="rounded-full border border-info/60 bg-info/10 px-2 py-0.5 text-xs font-normal text-info-text">
              Pré-carga
            </span>
          </p>

          {preview.isPending && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="animate-spin size-4" /> Montando prévia...
            </p>
          )}

          {preview.isSuccess && (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <MotoristaPickerFusion
                  id="pc-select-motorista"
                  sugestao={null}
                  escolhido={motoristaEscolhido}
                  onEscolher={setMotoristaEscolhido}
                />

                <div className="flex flex-col gap-1">
                  <Label htmlFor="pc-select-veiculo">Veículo</Label>
                  <select
                    id="pc-select-veiculo"
                    className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                    value={veiculoId ?? ""}
                    onChange={(e) => setVeiculoId(e.target.value ? Number(e.target.value) : null)}
                  >
                    <option value="">— escolha o veículo —</option>
                    {(veiculos.data ?? []).map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.descricao ?? v.placa} ({v.codigo_erp})
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <PreCargaResumoCard
                payload={preview.data.payload}
                motorista={motoristaEscolhido}
                veiculo={(veiculos.data ?? []).find((v) => v.id === veiculoId) ?? null}
              />

              {preview.data.avisos.length > 0 && (
                <ul className="list-disc rounded-md bg-yellow-500/10 px-3 py-2.5 pl-6 text-sm text-foreground">
                  {preview.data.avisos.map((aviso) => (
                    <li key={aviso}>{aviso}</li>
                  ))}
                </ul>
              )}

              <PedidosParaEnvio
                itens={preview.data.itens}
                selecionados={itensSelecionados}
                expandidos={expandidos}
                onAlternarExpandido={alternarExpandido}
                onAlternarPedido={(numeros) => alternarPedido(numeros, preview.data.itens)}
              />

              {preview.data.item_fila?.status === "ENVIADA" && !confirmarReenvio && (
                <p className="rounded-md bg-yellow-500/10 px-3 py-2.5 text-sm text-foreground">
                  Esta pré-carga já foi enviada ao Fusion em{" "}
                  {formatarData(preview.data.item_fila.enviado_em)}.
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                {preview.data.item_fila?.status === "ENVIADA" && !confirmarReenvio ? (
                  <Button type="button" variant="outline" onClick={() => setConfirmarReenvio(true)}>
                    Reenviar mesmo assim
                  </Button>
                ) : (
                  <Button
                    type="button"
                    variant={confirmarReenvio ? "destructive" : "default"}
                    onClick={() => enviarPreCarga(confirmarReenvio)}
                    disabled={enviar.isPending}
                  >
                    {enviar.isPending ? <LoaderCircle className="animate-spin" /> : <Send />}
                    {confirmarReenvio ? "Confirmar reenvio" : "Enviar pré-carga ao Fusion"}
                  </Button>
                )}
              </div>

              {enviar.isSuccess && <ResultadoEnvio resultado={enviar.data} />}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function PedidosParaEnvio({
  itens,
  selecionados,
  expandidos,
  onAlternarExpandido,
  onAlternarPedido,
}: {
  itens: ItemPreCargaFusion[];
  selecionados: Set<number> | null;
  expandidos: Set<number>;
  onAlternarExpandido: (id: number) => void;
  onAlternarPedido: (numerosDoPedido: number[]) => void;
}) {
  if (itens.length === 0) {
    return <p className="text-sm text-muted-foreground">Esta pré-carga não tem entregas.</p>;
  }

  const pedidos = agruparPorPedido(itens);
  const efetivos = selecionados ?? new Set(itens.map((i) => i.NUMSEQUENCIA));
  const pedidosSelecionados = pedidos.filter((p) => p.numeros.every((n) => efetivos.has(n))).length;

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <PackageCheck className="size-4 text-muted-foreground" />
        <Label>
          Pedidos para o Fusion ({pedidosSelecionados} de {pedidos.length} selecionado(s))
        </Label>
      </div>
      <div className="flex max-h-96 flex-col gap-2 overflow-auto pr-1">
        {pedidos.map((pedido) => {
          const completo = pedido.numeros.every((n) => efetivos.has(n));
          const expandido = expandidos.has(pedido.idPlanilhaDocOri);
          return (
            <div
              key={pedido.idPlanilhaDocOri}
              className={
                "overflow-hidden rounded-lg border transition-colors " +
                (completo ? "border-primary/40 bg-primary/5" : "border-border bg-card")
              }
            >
              <div className="flex items-center gap-3 px-3 py-2.5">
                <input
                  type="checkbox"
                  className="size-4 shrink-0 accent-primary"
                  checked={completo}
                  onChange={() => onAlternarPedido(pedido.numeros)}
                />
                <button
                  type="button"
                  onClick={() => onAlternarExpandido(pedido.idPlanilhaDocOri)}
                  className="flex min-w-0 flex-1 items-center justify-between gap-3 text-left"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">
                      Pedido {pedido.numeroPedido}
                      <span className="font-normal text-muted-foreground"> · NF ainda não emitida</span>
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {pedido.numeros.length} {pedido.numeros.length === 1 ? "item" : "itens"} ·{" "}
                      {pedido.pesoTotal.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} kg
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className="text-sm font-medium tabular-nums text-foreground">
                      R$ {formatarMoeda(pedido.valorTotal)}
                    </span>
                    {expandido ? (
                      <ChevronDown className="size-4 text-muted-foreground" />
                    ) : (
                      <ChevronRight className="size-4 text-muted-foreground" />
                    )}
                  </div>
                </button>
              </div>

              {expandido && (
                <div className="border-t border-border/60 bg-muted/30 px-3 py-2">
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="text-left text-muted-foreground">
                        <th className="pb-1 pr-3 font-medium">Produto</th>
                        <th className="pb-1 pr-3 font-medium text-right">Qtd</th>
                        <th className="pb-1 font-medium text-right">Valor</th>
                      </tr>
                    </thead>
                    <tbody>
                      {pedido.itens.map((item) => (
                        <tr key={item.NUMSEQUENCIA} className="border-t border-border/40 align-top">
                          <td className="py-1.5 pr-3">
                            <p
                              className="font-medium text-foreground"
                              title={item.DESCRCOMPRODUTO ?? undefined}
                            >
                              {item.DESCRCOMPRODUTO || `Produto ${item.IDSUBPRODUTO}`}
                            </p>
                            <p className="text-muted-foreground">#{item.IDSUBPRODUTO}</p>
                          </td>
                          <td className="py-1.5 pr-3 text-right tabular-nums whitespace-nowrap">
                            {item.QTDPRODUTO}
                            <span className="text-muted-foreground">
                              {" "}
                              · {formatarNumero(item.PESOBRUTO, 2)} kg
                            </span>
                          </td>
                          <td className="py-1.5 text-right tabular-nums whitespace-nowrap text-muted-foreground">
                            R$ {formatarMoeda(item.VALTOTLIQUIDO)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

function PreCargaResumoCard({
  payload,
  motorista,
  veiculo,
}: {
  payload: Record<string, unknown>;
  motorista: { nome: string; codigo_erp: string } | null;
  veiculo: { descricao: string | null; placa: string; codigo_erp: string } | null;
}) {
  const carga = (payload.cargas as Record<string, unknown>[] | undefined)?.[0];
  if (!carga) return null;

  return (
    <Card className="gap-3 py-4">
      <CardHeader className="gap-1 px-4">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Truck className="size-4 text-primary" />
          Pré-carga {String(carga.codigo_erp ?? "—")} — o que vai pro Fusion
        </CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-x-4 gap-y-3 px-4 text-sm sm:grid-cols-3">
        <CampoResumo label="Saída" valor={formatarData(carga.dt_saida ? String(carga.dt_saida) : null)} />
        <CampoResumo
          label="Motorista"
          valor={motorista ? `${motorista.nome} (${motorista.codigo_erp})` : "— nenhum —"}
        />
        <CampoResumo
          label="Veículo"
          valor={veiculo ? `${veiculo.descricao ?? veiculo.placa} (${veiculo.codigo_erp})` : "— nenhum —"}
        />
      </CardContent>
      {carga.obs ? (
        <CardContent className="px-4 pt-0 text-xs text-muted-foreground">Obs: {String(carga.obs)}</CardContent>
      ) : null}
    </Card>
  );
}

function CampoResumo({ label, valor }: { label: string; valor: string }) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      <p className="truncate font-medium text-foreground">{valor}</p>
    </div>
  );
}

function ResultadoEnvio({ resultado }: { resultado: { status: string; motivo?: string } }) {
  if (resultado.status === "enviada") {
    return (
      <p className="flex items-center gap-2 rounded-md bg-green-500/10 px-3 py-2.5 text-sm text-foreground">
        <PackageCheck className="size-4" /> Enviada com sucesso ao Fusion.
      </p>
    );
  }
  if (resultado.status === "ja_enviada") {
    return (
      <p className="rounded-md bg-yellow-500/10 px-3 py-2.5 text-sm text-foreground">
        Não reenviada — esta pré-carga já tinha sido enviada ao Fusion antes.
      </p>
    );
  }
  return (
    <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
      Não foi enviada — foi para a fila. {resultado.motivo}
    </p>
  );
}
