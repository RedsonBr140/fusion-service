import {
  BadgeCheck,
  ChevronDown,
  ChevronRight,
  LoaderCircle,
  PackageCheck,
  Search,
  Send,
  Truck,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/api";
import { useVeiculos } from "@/tools/cargas_fusion/useCadastroFusion";
import {
  useBuscarCargas,
  useCargaPreview,
  useEmpresasCargasFusion,
  useEnviarCarga,
  useMarcarCargaIntegrada,
} from "@/tools/cargas_fusion/useCargasFusion";
import { MotoristaPickerFusion } from "@/tools/cargas_fusion/MotoristaPickerFusion";
import type { CargaResumo, ItemCargaFusion, MotoristaFusion, VeiculoFusion } from "@/types";

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

/** L×A×C (cm) e o volume calculado (L) — PRODUTO_GRADE não guarda volume
 * pronto, só as três dimensões. `null` quando o produto não tem nenhuma
 * cadastrada (comum — é um dado de catálogo, não de toda a base). */
function formatarDimensoes(item: ItemCargaFusion): string | null {
  const { LARGURA: l, ALTURA: a, COMPRIMENTO: c } = item;
  if (!l && !a && !c) return null;
  const volumeLitros = ((l ?? 0) * (a ?? 0) * (c ?? 0)) / 1000;
  return `${formatarNumero(l)}×${formatarNumero(a)}×${formatarNumero(c)} cm · ${formatarNumero(volumeLitros)} L`;
}

interface Props {
  cargaPreSelecionada: { idEmpresa: number; idPlanilha: number } | null;
}

export function BuscaCargasSection({ cargaPreSelecionada }: Props) {
  const [dataInicial, setDataInicial] = useState(agoraMenos(24));
  const [dataFinal, setDataFinal] = useState(agoraMenos(0));
  const [filtroEmpresaId, setFiltroEmpresaId] = useState<number | null>(null);
  const [ocultarEnviadas, setOcultarEnviadas] = useState(true);
  const [selecionada, setSelecionada] = useState<{ idEmpresa: number; idPlanilha: number } | null>(
    cargaPreSelecionada
  );

  // `null` em cada uma destas significa "usar a sugestão automática" (ou,
  // pros itens, "todos") — só vira um valor concreto quando a pessoa mexe.
  // Motorista guarda o objeto inteiro (lido ao vivo do Fusion), não um id —
  // não há mais lista local pra resolver um id de volta a um nome.
  const [motoristaEscolhido, setMotoristaEscolhido] = useState<MotoristaFusion | null>(null);
  const [veiculoId, setVeiculoId] = useState<number | null>(null);
  const [itensSelecionados, setItensSelecionados] = useState<Set<number> | null>(null);
  const [confirmarReenvio, setConfirmarReenvio] = useState(false);
  // Snapshot do que foi enviado com sucesso — a prévia pode mudar (item_fila
  // atualiza) depois do envio, então o modal mostra o que foi mandado, não
  // o estado atual da prévia.
  const [cargaEnviada, setCargaEnviada] = useState<{
    idEmpresa: number;
    idPlanilha: number;
    payload: Record<string, unknown>;
    motorista: MotoristaFusion | null;
    veiculo: VeiculoFusion | null;
  } | null>(null);

  const busca = useBuscarCargas();
  const empresas = useEmpresasCargasFusion();
  const veiculos = useVeiculos();
  const preview = useCargaPreview(selecionada, {
    motoristaCodigoErp: motoristaEscolhido?.codigo_erp ?? undefined,
    veiculoId: veiculoId ?? undefined,
    itens: itensSelecionados ? Array.from(itensSelecionados) : undefined,
  });
  const enviar = useEnviarCarga();
  const marcarIntegrada = useMarcarCargaIntegrada();

  // Chegou aqui vinda da fila: abre a prévia direto, sem precisar buscar.
  useEffect(() => {
    if (cargaPreSelecionada) {
      setSelecionada(cargaPreSelecionada);
      setMotoristaEscolhido(null);
      setVeiculoId(null);
      setItensSelecionados(null);
      setConfirmarReenvio(false);
      setCargaEnviada(null);
    }
  }, [cargaPreSelecionada]);

  const cargas = busca.data ?? [];
  const erro =
    mensagemDeErro(busca.error) ??
    mensagemDeErro(preview.error) ??
    mensagemDeErro(enviar.error) ??
    mensagemDeErro(marcarIntegrada.error) ??
    mensagemDeErro(veiculos.error) ??
    mensagemDeErro(empresas.error);

  function buscar() {
    setSelecionada(null);
    enviar.reset();
    marcarIntegrada.reset();
    busca.mutate({ dataInicial, dataFinal, idEmpresa: filtroEmpresaId, ocultarEnviadas });
  }

  function selecionar(carga: CargaResumo) {
    enviar.reset();
    marcarIntegrada.reset();
    setMotoristaEscolhido(null);
    setVeiculoId(null);
    setItensSelecionados(null);
    setConfirmarReenvio(false);
    setCargaEnviada(null);
    setSelecionada({ idEmpresa: carga.IDEMPRESA, idPlanilha: carga.IDPLANILHA });
  }

  function alternarPedido(numerosDoPedido: number[], todos: ItemCargaFusion[]) {
    // Um pedido vai pro Fusion inteiro ou não vai (D10 da spec — o
    // saveEntregaServico leva todo o itens[] do pedido de uma vez): a
    // seleção alterna o pedido como unidade, nunca uma linha isolada dele.
    const efetivos = itensSelecionados ?? new Set(todos.map((i) => i.NUMSEQUENCIA));
    const novo = new Set(efetivos);
    const pedidoCompleto = numerosDoPedido.every((n) => novo.has(n));
    for (const numero of numerosDoPedido) {
      if (pedidoCompleto) novo.delete(numero);
      else novo.add(numero);
    }
    setItensSelecionados(novo);
  }

  function enviarCarga(forcar = false) {
    if (!selecionada) return;
    const snapshot = selecionada;
    const payload = preview.isSuccess ? preview.data.payload : null;
    const motorista = preview.isSuccess
      ? motoristaEscolhido ?? preview.data.sugestao_motorista
      : null;
    const veiculo = preview.isSuccess
      ? (veiculos.data ?? []).find(
          (v) => v.id === (veiculoId ?? preview.data.sugestao_veiculo_id)
        ) ?? null
      : null;

    enviar.mutate(
      {
        ...selecionada,
        selecao: {
          motoristaCodigoErp: motoristaEscolhido?.codigo_erp ?? undefined,
          veiculoId: veiculoId ?? undefined,
          itens: itensSelecionados ? Array.from(itensSelecionados) : undefined,
        },
        forcar,
      },
      {
        onSuccess: (resultado) => {
          if (resultado.status === "enviada" && payload) {
            setCargaEnviada({ ...snapshot, payload, motorista, veiculo });
          }
        },
      }
    );
  }

  function marcarIntegradaManualmente() {
    if (!selecionada) return;
    marcarIntegrada.mutate(selecionada);
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="data-inicial">De</Label>
          <Input
            id="data-inicial"
            type="datetime-local"
            value={dataInicial}
            onChange={(e) => setDataInicial(e.target.value)}
            className="w-56"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="data-final">Até</Label>
          <Input
            id="data-final"
            type="datetime-local"
            value={dataFinal}
            onChange={(e) => setDataFinal(e.target.value)}
            className="w-56"
          />
        </div>
        <div className="flex flex-col gap-1">
          <Label htmlFor="filtro-empresa">Empresa</Label>
          <select
            id="filtro-empresa"
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
        Não mostrar cargas já enviadas
      </label>

      <p className="text-xs text-muted-foreground">
        Cargas de antes de 10/09/2026 06:00 nunca aparecem — até lá, a roteirização era
        feita pela integração nativa do Fusion, não por esta tool.
      </p>

      {erro && (
        <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
          {erro}
        </p>
      )}

      {busca.isSuccess && (
        <div className="overflow-x-auto">
          {cargas.length === 0 ? (
            <p className="text-sm text-muted-foreground">Nenhuma carga encontrada no período.</p>
          ) : (
            <table className="w-full min-w-[32rem] text-sm">
              <thead>
                <tr className="border-b border-border text-left text-muted-foreground">
                  <th scope="col" className="py-2 pr-3 font-medium">Empresa / Carga</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Motorista / Placa</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Alterada em</th>
                  <th scope="col" className="py-2 pr-3 font-medium">Confirmada</th>
                </tr>
              </thead>
              <tbody>
                {cargas.map((carga) => {
                  const marcada =
                    selecionada?.idEmpresa === carga.IDEMPRESA &&
                    selecionada?.idPlanilha === carga.IDPLANILHA;
                  return (
                    <tr
                      key={`${carga.IDEMPRESA}-${carga.IDPLANILHA}`}
                      onClick={() => selecionar(carga)}
                      className={
                        "cursor-pointer border-b border-border/50 " +
                        (marcada ? "bg-accent" : "hover:bg-muted/50")
                      }
                    >
                      <td className="py-2 pr-3 tabular-nums whitespace-nowrap">
                        {carga.IDEMPRESA}_{carga.IDPLANILHA}
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        {carga.NOMETRANSP ?? carga.IDMOTORISTA ?? "—"} ·{" "}
                        {carga.PLACATRANSP ?? "sem placa"}
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">
                        {formatarData(carga.DTALTERACAO)}
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        {carga.FLAGCONFIRMADA === "T" ? "Sim" : "Não"}
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
          <p className="text-sm font-medium text-foreground">
            Carga {selecionada.idEmpresa}_{selecionada.idPlanilha}
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
                  id="select-motorista"
                  sugestao={preview.data.sugestao_motorista}
                  escolhido={motoristaEscolhido}
                  onEscolher={setMotoristaEscolhido}
                />

                <div className="flex flex-col gap-1">
                  <Label htmlFor="select-veiculo">Veículo</Label>
                  <select
                    id="select-veiculo"
                    className="h-9 rounded-md border border-input bg-transparent px-3 text-sm"
                    value={veiculoId ?? preview.data.sugestao_veiculo_id ?? ""}
                    onChange={(e) => setVeiculoId(e.target.value ? Number(e.target.value) : null)}
                  >
                    <option value="">— nenhum —</option>
                    {(veiculos.data ?? []).map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.descricao ?? v.placa} ({v.codigo_erp})
                        {preview.data.sugestao_veiculo_id === v.id ? " · sugerido pelo CISS" : ""}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <CargaResumoCard
                payload={preview.data.payload}
                motorista={motoristaEscolhido ?? preview.data.sugestao_motorista}
                veiculo={
                  (veiculos.data ?? []).find(
                    (v) => v.id === (veiculoId ?? preview.data.sugestao_veiculo_id)
                  ) ?? null
                }
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
                onAlternarPedido={(numeros) => alternarPedido(numeros, preview.data.itens)}
              />

              {preview.data.item_fila?.status === "ENVIADA" && !confirmarReenvio && (
                <p className="rounded-md bg-yellow-500/10 px-3 py-2.5 text-sm text-foreground">
                  {preview.data.item_fila.integrada_manualmente
                    ? "Esta carga já foi marcada como integrada manualmente (sem passar por esta tool) em "
                    : "Esta carga já foi enviada ao Fusion em "}
                  {formatarData(preview.data.item_fila.enviado_em)}.
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                {preview.data.item_fila?.status === "ENVIADA" && !confirmarReenvio ? (
                  <Button type="button" variant="outline" onClick={() => setConfirmarReenvio(true)}>
                    Reenviar mesmo assim
                  </Button>
                ) : (
                  <>
                    <Button
                      type="button"
                      variant={confirmarReenvio ? "destructive" : "default"}
                      onClick={() => enviarCarga(confirmarReenvio)}
                      disabled={enviar.isPending}
                    >
                      {enviar.isPending ? <LoaderCircle className="animate-spin" /> : <Send />}
                      {confirmarReenvio ? "Confirmar reenvio" : "Enviar ao Fusion"}
                    </Button>
                    {!confirmarReenvio && (
                      <Button
                        type="button"
                        variant="outline"
                        onClick={marcarIntegradaManualmente}
                        disabled={marcarIntegrada.isPending}
                      >
                        {marcarIntegrada.isPending ? (
                          <LoaderCircle className="animate-spin" />
                        ) : (
                          <BadgeCheck />
                        )}
                        Marcar como integrada
                      </Button>
                    )}
                  </>
                )}
              </div>

              {enviar.isSuccess && <ResultadoEnvio resultado={enviar.data} />}
              {marcarIntegrada.isSuccess && (
                <p className="flex items-center gap-2 rounded-md bg-green-500/10 px-3 py-2.5 text-sm text-foreground">
                  <BadgeCheck className="size-4" /> Marcada como integrada — não vai ser reenviada sem
                  forçar.
                </p>
              )}
            </>
          )}
        </div>
      )}

      <Dialog open={cargaEnviada !== null} onOpenChange={(aberto) => !aberto && setCargaEnviada(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <PackageCheck className="size-5 text-primary" /> Carga enviada ao Fusion
            </DialogTitle>
            <DialogDescription>
              Abra o Fusion e clique em <strong>Roteirizar</strong> na tela{" "}
              <strong>3.1 "Gestão das Cargas"</strong> — o envio por aqui só cria a carga lá, não
              roteiriza sozinho.
            </DialogDescription>
          </DialogHeader>

          {cargaEnviada && (
            <CargaResumoCard
              payload={cargaEnviada.payload}
              motorista={cargaEnviada.motorista}
              veiculo={cargaEnviada.veiculo}
            />
          )}

          <DialogFooter>
            <Button type="button" onClick={() => setCargaEnviada(null)}>
              Entendi
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface PedidoAgrupado {
  /** Chave de agrupamento — a pré-nota (IDPLANILHADOCORI), sempre presente.
   * Não é o número exibido: ver numeroPedido. */
  idPlanilhaDocOri: number;
  /** O pedido real (IDORCAMENTO). */
  numeroPedido: number;
  nf: string | null;
  numeros: number[];
  valorTotal: number;
  pesoTotal: number;
  itens: ItemCargaFusion[];
}

function agruparPorPedido(itens: ItemCargaFusion[]): PedidoAgrupado[] {
  // Um "pedido" pode ter mais de uma linha em PRODUTO_RETIRADO — e vai pro
  // saveEntregaServico com todo o itens[] de uma vez (D10 da spec), então a
  // tela agrupa por pedido, não por linha. O agrupamento usa IDPLANILHADOCORI
  // (a pré-nota, sempre presente) como chave — o mesmo que o backend faz.
  //
  // Pedido sem IDORCAMENTO (sem orçamento vinculado no CISS) não aparece
  // aqui — o backend também o ignora ao montar o envio (D11 revisado).
  const grupos = new Map<number, ItemCargaFusion[]>();
  for (const item of itens) {
    if (item.IDORCAMENTO == null) continue;
    const grupo = grupos.get(item.IDPLANILHADOCORI);
    if (grupo) grupo.push(item);
    else grupos.set(item.IDPLANILHADOCORI, [item]);
  }
  return Array.from(grupos.entries()).map(([idPlanilhaDocOri, linhas]) => {
    const primeiroComNota = linhas.find((l) => l.NUMNOTA != null);
    return {
      idPlanilhaDocOri,
      numeroPedido: linhas[0].IDORCAMENTO as number,
      nf: primeiroComNota ? `${primeiroComNota.NUMNOTA}-${primeiroComNota.SERIENOTA ?? "?"}` : null,
      numeros: linhas.map((l) => l.NUMSEQUENCIA),
      valorTotal: linhas.reduce((soma, l) => soma + (l.VALTOTLIQUIDO ?? 0), 0),
      pesoTotal: linhas.reduce((soma, l) => soma + (l.PESOBRUTO ?? 0), 0),
      itens: linhas,
    };
  });
}

/** O que vai pro Fusion, em cards legíveis — nada de payload JSON cru na
 * tela. Cada pedido é selecionável como unidade (checkbox) e expansível
 * (seta) pra ver os itens de produto que entram no saveEntregaServico. */
function PedidosParaEnvio({
  itens,
  selecionados,
  onAlternarPedido,
}: {
  itens: ItemCargaFusion[];
  selecionados: Set<number> | null;
  onAlternarPedido: (numerosDoPedido: number[]) => void;
}) {
  const [expandidos, setExpandidos] = useState<Set<number>>(new Set());

  if (itens.length === 0) {
    return <p className="text-sm text-muted-foreground">Esta carga não tem entregas.</p>;
  }

  const pedidos = agruparPorPedido(itens);
  const efetivos = selecionados ?? new Set(itens.map((i) => i.NUMSEQUENCIA));
  const pedidosSelecionados = pedidos.filter((p) => p.numeros.every((n) => efetivos.has(n))).length;

  function alternarExpandido(id: number) {
    const novo = new Set(expandidos);
    if (novo.has(id)) novo.delete(id);
    else novo.add(id);
    setExpandidos(novo);
  }

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
                  onClick={() => alternarExpandido(pedido.idPlanilhaDocOri)}
                  className="flex min-w-0 flex-1 items-center justify-between gap-3 text-left"
                >
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-foreground">
                      Pedido {pedido.numeroPedido}
                      <span className="font-normal text-muted-foreground"> · NF {pedido.nf ?? "—"}</span>
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
                            <p className="text-muted-foreground">
                              #{item.IDSUBPRODUTO}
                              {formatarDimensoes(item) ? ` · ${formatarDimensoes(item)}` : ""}
                            </p>
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

/** O resumo da carga em si — destino, saída, motorista, veículo — lido do
 * payload só pelos campos que interessam à pessoa, nunca como JSON cru. */
function CargaResumoCard({
  payload,
  motorista,
  veiculo,
}: {
  payload: Record<string, unknown>;
  motorista: MotoristaFusion | null;
  veiculo: VeiculoFusion | null;
}) {
  const carga = (payload.cargas as Record<string, unknown>[] | undefined)?.[0];
  if (!carga) return null;

  return (
    <Card className="gap-3 py-4">
      <CardHeader className="gap-1 px-4">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Truck className="size-4 text-primary" />
          Carga {String(carga.codigo_erp ?? "—")} — o que vai pro Fusion
        </CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-x-4 gap-y-3 px-4 text-sm sm:grid-cols-4">
        <CampoResumo label="Destino" valor={String(carga.destino || "—")} />
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
        Não reenviada — esta carga já tinha sido enviada ao Fusion antes.
      </p>
    );
  }
  return (
    <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
      Não foi enviada — foi para a fila. {resultado.motivo}
    </p>
  );
}
