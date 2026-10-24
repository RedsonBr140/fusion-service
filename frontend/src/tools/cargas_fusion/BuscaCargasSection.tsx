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
import {
  useBuscarCargaPorDocumento,
  useBuscarCargas,
  useCargaPreview,
  useEmpresasCargasFusion,
  useEnviarCarga,
  useMarcarCargaIntegrada,
} from "@/tools/cargas_fusion/useCargasFusion";
import {
  useBuscarPreCargas,
  useEnviarPreCarga,
  usePreCargaPreview,
} from "@/tools/cargas_fusion/usePreCargasFusion";
import { MotoristaPickerFusion } from "@/tools/cargas_fusion/MotoristaPickerFusion";
import { VeiculoPickerFusion } from "@/tools/cargas_fusion/VeiculoPickerFusion";
import type {
  CargaResumo,
  ItemCargaFusion,
  ItemPreCargaFusion,
  MotoristaFusion,
  PreCargaResumo,
  VeiculoFusion,
} from "@/types";

function mensagemDeErro(erro: unknown): string | null {
  return erro instanceof ApiError ? erro.message : erro ? "Erro inesperado." : null;
}

/** Data de hoje às `hora:00:00`, em wall-clock local — nunca `.toISOString()`,
 * que devolve UTC e faz um "7h" virar "10h"/"4h" dependendo do horário do
 * ano, a mesma classe de bug de fuso documentada pra data_inicial/data_final
 * do CISS (CLAUDE.md). `data_inicial`/`data_final` da API esperam a mesma
 * wall-clock ingênua que `DTALTERACAO` tem no CISS (Brasília, sem offset) —
 * `type="datetime-local"` já é local ao navegador, então os getters locais
 * do Date bastam, sem nenhuma conversão. */
function hojeAs(hora: number): string {
  const hoje = new Date();
  const ano = hoje.getFullYear();
  const mes = String(hoje.getMonth() + 1).padStart(2, "0");
  const dia = String(hoje.getDate()).padStart(2, "0");
  return `${ano}-${mes}-${dia}T${String(hora).padStart(2, "0")}:00:00`;
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

function rotuloVeiculo(v: VeiculoFusion): string {
  return v.descricao ? `${v.placa} - ${v.descricao}` : v.placa;
}

type ItemComPedido = ItemCargaFusion | ItemPreCargaFusion;

/** L×A×C (cm) e o volume calculado (L) — PRODUTO_GRADE não guarda volume
 * pronto, só as três dimensões. `null` quando o produto não tem nenhuma
 * cadastrada (comum — é um dado de catálogo, não de toda a base). */
function formatarDimensoes(item: ItemComPedido): string | null {
  const { LARGURA: l, ALTURA: a, COMPRIMENTO: c } = item;
  if (!l && !a && !c) return null;
  const volumeLitros = ((l ?? 0) * (a ?? 0) * (c ?? 0)) / 1000;
  return `${formatarNumero(l)}×${formatarNumero(a)}×${formatarNumero(c)} cm · ${formatarNumero(volumeLitros)} L`;
}

// --- lista unificada: carga + pré-carga, uma tabela só -----------------

interface LinhaCarga {
  tipo: "carga";
  idEmpresa: number;
  idPlanilha: number;
  codigo: string;
  detalhe: string;
  data: string | null;
  confirmada: boolean;
}

interface LinhaPreCarga {
  tipo: "pre-carga";
  idEmpresa: number;
  idPrecarga: number;
  codigo: string;
  detalhe: string;
  data: string | null;
  confirmada: boolean;
}

type Linha = LinhaCarga | LinhaPreCarga;

function linhaDeCarga(c: CargaResumo): LinhaCarga {
  return {
    tipo: "carga",
    idEmpresa: c.IDEMPRESA,
    idPlanilha: c.IDPLANILHA,
    codigo: `${c.IDEMPRESA}_${c.IDPLANILHA}`,
    // Motorista, não a transportadora (NOMETRANSP) — a transportadora é
    // quase sempre a mesma em toda carga, não ajuda a distinguir uma da
    // outra na listagem.
    detalhe: `${c.NOME_MOTORISTA ?? c.IDMOTORISTA ?? "—"} · ${c.PLACATRANSP ?? "sem placa"}`,
    data: c.DTALTERACAO,
    confirmada: c.FLAGCONFIRMADA === "T",
  };
}

function linhaDePreCarga(pc: PreCargaResumo): LinhaPreCarga {
  return {
    tipo: "pre-carga",
    idEmpresa: pc.IDEMPRESA,
    idPrecarga: pc.IDPRECARGA,
    codigo: `${pc.IDEMPRESA}_${pc.IDPRECARGA}`,
    detalhe: pc.DESCRPRECARGA || "—",
    data: pc.DTMOVIMENTO,
    confirmada: pc.FLAGCONFERIDA === "T",
  };
}

function TagPreCarga() {
  return (
    <span className="rounded-full border border-info/60 bg-info/10 px-2 py-0.5 text-xs font-normal text-info-text">
      Pré-carga
    </span>
  );
}

type Selecionada =
  | { tipo: "carga"; idEmpresa: number; idPlanilha: number }
  | { tipo: "pre-carga"; idEmpresa: number; idPrecarga: number };

interface Props {
  cargaPreSelecionada: { idEmpresa: number; idPlanilha: number } | null;
}

export function BuscaCargasSection({ cargaPreSelecionada }: Props) {
  const [dataInicial, setDataInicial] = useState(hojeAs(7));
  const [dataFinal, setDataFinal] = useState(hojeAs(19));
  const [filtroEmpresaId, setFiltroEmpresaId] = useState<number | null>(null);
  const [ocultarEnviadas, setOcultarEnviadas] = useState(true);
  const [mostrarPreCargas, setMostrarPreCargas] = useState(true);
  const [apenasFusion, setApenasFusion] = useState(true);
  const [termoDocumento, setTermoDocumento] = useState("");
  const [selecionada, setSelecionada] = useState<Selecionada | null>(
    cargaPreSelecionada ? { tipo: "carga", ...cargaPreSelecionada } : null
  );

  // `null` em cada uma destas significa "usar a sugestão automática" (ou,
  // pros itens, "todos") — só vira um valor concreto quando a pessoa mexe.
  // Motorista e veículo guardam o objeto inteiro (lidos ao vivo do Fusion),
  // não um id — não há mais lista local pra resolver um id de volta a um nome.
  const [motoristaEscolhido, setMotoristaEscolhido] = useState<MotoristaFusion | null>(null);
  const [veiculoEscolhido, setVeiculoEscolhido] = useState<VeiculoFusion | null>(null);
  const [itensSelecionados, setItensSelecionados] = useState<Set<number> | null>(null);
  const [confirmarReenvio, setConfirmarReenvio] = useState(false);
  // Snapshot do que foi enviado com sucesso (só carga — pré-carga não abre
  // esse diálogo) — a prévia pode mudar (item_fila atualiza) depois do
  // envio, então o modal mostra o que foi mandado, não o estado atual.
  const [cargaEnviada, setCargaEnviada] = useState<{
    idEmpresa: number;
    idPlanilha: number;
    payload: Record<string, unknown>;
    motorista: MotoristaFusion | null;
    veiculo: VeiculoFusion | null;
  } | null>(null);

  const busca = useBuscarCargas();
  const buscaPreCargas = useBuscarPreCargas();
  const buscaDocumento = useBuscarCargaPorDocumento();
  const empresas = useEmpresasCargasFusion();

  const previewCarga = useCargaPreview(
    selecionada?.tipo === "carga"
      ? { idEmpresa: selecionada.idEmpresa, idPlanilha: selecionada.idPlanilha }
      : null,
    {
      motoristaCodigoErp: motoristaEscolhido?.codigo_erp ?? undefined,
      veiculoCodigoErp: veiculoEscolhido?.codigo_erp ?? undefined,
      itens: itensSelecionados ? Array.from(itensSelecionados) : undefined,
    }
  );
  const previewPreCarga = usePreCargaPreview(
    selecionada?.tipo === "pre-carga"
      ? { idEmpresa: selecionada.idEmpresa, idPrecarga: selecionada.idPrecarga }
      : null,
    {
      motoristaCodigoErp: motoristaEscolhido?.codigo_erp ?? undefined,
      veiculoCodigoErp: veiculoEscolhido?.codigo_erp ?? undefined,
      itens: itensSelecionados ? Array.from(itensSelecionados) : undefined,
    }
  );

  const enviar = useEnviarCarga();
  const enviarPreCarga = useEnviarPreCarga();
  const marcarIntegrada = useMarcarCargaIntegrada();

  // Chegou aqui vinda da fila (histórico): abre a prévia direto, sem
  // precisar buscar. Histórico só lista cargas, nunca pré-carga.
  useEffect(() => {
    if (cargaPreSelecionada) {
      setSelecionada({ tipo: "carga", ...cargaPreSelecionada });
      setMotoristaEscolhido(null);
      setVeiculoEscolhido(null);
      setItensSelecionados(null);
      setConfirmarReenvio(false);
      setCargaEnviada(null);
    }
  }, [cargaPreSelecionada]);

  const linhas: Linha[] = [
    ...(busca.data ?? []).map(linhaDeCarga),
    ...(mostrarPreCargas ? (buscaPreCargas.data ?? []).map(linhaDePreCarga) : []),
  ].sort((a, b) => (b.data ?? "").localeCompare(a.data ?? ""));

  const erro =
    mensagemDeErro(busca.error) ??
    mensagemDeErro(buscaPreCargas.error) ??
    mensagemDeErro(buscaDocumento.error) ??
    mensagemDeErro(previewCarga.error) ??
    mensagemDeErro(previewPreCarga.error) ??
    mensagemDeErro(enviar.error) ??
    mensagemDeErro(enviarPreCarga.error) ??
    mensagemDeErro(marcarIntegrada.error) ??
    mensagemDeErro(empresas.error);

  function buscarPorDocumento() {
    if (!termoDocumento.trim()) return;
    enviar.reset();
    enviarPreCarga.reset();
    marcarIntegrada.reset();
    buscaDocumento.mutate(termoDocumento.trim(), {
      onSuccess: (resultado) => {
        setMotoristaEscolhido(null);
        setVeiculoEscolhido(null);
        setItensSelecionados(null);
        setConfirmarReenvio(false);
        setCargaEnviada(null);
        setSelecionada({ tipo: "carga", idEmpresa: resultado.id_empresa, idPlanilha: resultado.id_planilha });
      },
    });
  }

  function buscar() {
    setSelecionada(null);
    enviar.reset();
    enviarPreCarga.reset();
    marcarIntegrada.reset();
    busca.mutate({ dataInicial, dataFinal, idEmpresa: filtroEmpresaId, ocultarEnviadas, apenasFusion });
    buscaPreCargas.mutate({
      dataInicial,
      dataFinal,
      idEmpresa: filtroEmpresaId,
      ocultarEnviadas,
      apenasFusion,
    });
  }

  function selecionar(linha: Linha) {
    enviar.reset();
    enviarPreCarga.reset();
    marcarIntegrada.reset();
    setMotoristaEscolhido(null);
    setVeiculoEscolhido(null);
    setItensSelecionados(null);
    setConfirmarReenvio(false);
    setCargaEnviada(null);
    setSelecionada(
      linha.tipo === "carga"
        ? { tipo: "carga", idEmpresa: linha.idEmpresa, idPlanilha: linha.idPlanilha }
        : { tipo: "pre-carga", idEmpresa: linha.idEmpresa, idPrecarga: linha.idPrecarga }
    );
  }

  function alternarPedido(numerosDoPedido: number[], todos: ItemComPedido[]) {
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

  function enviarCargaSelecionada(forcar = false) {
    if (!selecionada || selecionada.tipo !== "carga") return;
    const snapshot = selecionada;
    const payload = previewCarga.isSuccess ? previewCarga.data.payload : null;
    const motorista = previewCarga.isSuccess
      ? motoristaEscolhido ?? previewCarga.data.sugestao_motorista
      : null;
    const veiculo = previewCarga.isSuccess ? veiculoEscolhido ?? previewCarga.data.sugestao_veiculo : null;

    enviar.mutate(
      {
        idEmpresa: snapshot.idEmpresa,
        idPlanilha: snapshot.idPlanilha,
        selecao: {
          motoristaCodigoErp: motoristaEscolhido?.codigo_erp ?? undefined,
          veiculoCodigoErp: veiculoEscolhido?.codigo_erp ?? undefined,
          itens: itensSelecionados ? Array.from(itensSelecionados) : undefined,
        },
        forcar,
      },
      {
        onSuccess: (resultado) => {
          if (resultado.status === "enviada" && payload) {
            setCargaEnviada({
              idEmpresa: snapshot.idEmpresa,
              idPlanilha: snapshot.idPlanilha,
              payload,
              motorista,
              veiculo,
            });
          }
        },
      }
    );
  }

  function enviarPreCargaSelecionada(forcar = false) {
    if (!selecionada || selecionada.tipo !== "pre-carga") return;
    enviarPreCarga.mutate({
      idEmpresa: selecionada.idEmpresa,
      idPrecarga: selecionada.idPrecarga,
      selecao: {
        motoristaCodigoErp: motoristaEscolhido?.codigo_erp ?? undefined,
        veiculoCodigoErp: veiculoEscolhido?.codigo_erp ?? undefined,
        itens: itensSelecionados ? Array.from(itensSelecionados) : undefined,
      },
      forcar,
    });
  }

  function marcarIntegradaManualmente() {
    if (!selecionada || selecionada.tipo !== "carga") return;
    marcarIntegrada.mutate({ idEmpresa: selecionada.idEmpresa, idPlanilha: selecionada.idPlanilha });
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end gap-3 rounded-md border border-border p-3">
        <div className="flex flex-col gap-1">
          <Label htmlFor="busca-documento">Buscar por código de carga, pedido ou NF</Label>
          <Input
            id="busca-documento"
            value={termoDocumento}
            onChange={(e) => setTermoDocumento(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && termoDocumento.trim()) {
                e.preventDefault();
                buscarPorDocumento();
              }
            }}
            placeholder="1_22144395, 616139 ou 166105-2"
            className="w-64"
          />
        </div>
        <Button
          type="button"
          variant="secondary"
          onClick={buscarPorDocumento}
          disabled={!termoDocumento.trim() || buscaDocumento.isPending}
        >
          {buscaDocumento.isPending ? <LoaderCircle className="animate-spin" /> : <Search />}
          Abrir carga
        </Button>
      </div>

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
        <Button type="button" onClick={buscar} disabled={busca.isPending || buscaPreCargas.isPending}>
          {busca.isPending || buscaPreCargas.isPending ? (
            <LoaderCircle className="animate-spin" />
          ) : (
            <Search />
          )}
          Buscar
        </Button>
      </div>

      <div className="flex flex-wrap gap-4">
        <label className="flex w-fit items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={ocultarEnviadas}
            onChange={(e) => setOcultarEnviadas(e.target.checked)}
          />
          Não mostrar cargas já enviadas
        </label>
        <label className="flex w-fit items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={mostrarPreCargas}
            onChange={(e) => setMostrarPreCargas(e.target.checked)}
          />
          Mostrar pré-cargas
        </label>
        <label className="flex w-fit items-center gap-2 text-sm text-foreground">
          <input
            type="checkbox"
            className="size-4 accent-primary"
            checked={apenasFusion}
            onChange={(e) => setApenasFusion(e.target.checked)}
          />
          Só cargas com integração Fusion
        </label>
      </div>

      <p className="text-xs text-muted-foreground">
        A maioria das cargas é venda balcão (retirada na hora, sem entrega) e nunca aparece aqui por
        padrão. Desmarque "Só cargas com integração Fusion" se a carga que você procura não aparecer.
      </p>

      <p className="text-xs text-muted-foreground">
        Cargas de antes de 10/09/2026 06:00 nunca aparecem — até lá, a roteirização era
        feita pela integração nativa do Fusion, não por esta tool. Pré-carga ainda não tem
        motorista/veículo no CISS — escolha os dois antes de enviar.
      </p>

      {erro && (
        <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
          {erro}
        </p>
      )}

      {(busca.isSuccess || buscaPreCargas.isSuccess) && (
        <div className="overflow-x-auto">
          {linhas.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              {mostrarPreCargas ? "Nenhuma carga ou pré-carga encontrada no período." : "Nenhuma carga encontrada no período."}
            </p>
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
                {linhas.map((linha) => {
                  const marcada =
                    linha.tipo === "carga"
                      ? selecionada?.tipo === "carga" &&
                        selecionada.idEmpresa === linha.idEmpresa &&
                        selecionada.idPlanilha === linha.idPlanilha
                      : selecionada?.tipo === "pre-carga" &&
                        selecionada.idEmpresa === linha.idEmpresa &&
                        selecionada.idPrecarga === linha.idPrecarga;
                  return (
                    <tr
                      key={`${linha.tipo}-${linha.codigo}`}
                      onClick={() => selecionar(linha)}
                      className={
                        "cursor-pointer border-b border-border/50 " +
                        (marcada ? "bg-accent" : "hover:bg-muted/50")
                      }
                    >
                      <td className="py-2 pr-3 tabular-nums whitespace-nowrap">
                        <span className="inline-flex items-center gap-2">
                          {linha.codigo}
                          {linha.tipo === "pre-carga" && <TagPreCarga />}
                        </span>
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">{linha.detalhe}</td>
                      <td className="py-2 pr-3 whitespace-nowrap text-muted-foreground">
                        {formatarData(linha.data)}
                      </td>
                      <td className="py-2 pr-3 whitespace-nowrap">{linha.confirmada ? "Sim" : "Não"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
      )}

      {selecionada?.tipo === "carga" && (
        <div className="flex flex-col gap-3 rounded-md border border-border p-4">
          <p className="text-sm font-medium text-foreground">
            Carga {selecionada.idEmpresa}_{selecionada.idPlanilha}
          </p>

          {previewCarga.isPending && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="animate-spin size-4" /> Montando prévia...
            </p>
          )}

          {previewCarga.isSuccess && (() => {
            const motoristaResolvido = motoristaEscolhido ?? previewCarga.data.sugestao_motorista;
            const veiculoResolvido = veiculoEscolhido ?? previewCarga.data.sugestao_veiculo;
            const faltaSelecao = !motoristaResolvido || !veiculoResolvido;
            return (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <MotoristaPickerFusion
                  id="select-motorista"
                  sugestao={previewCarga.data.sugestao_motorista}
                  escolhido={motoristaEscolhido}
                  onEscolher={setMotoristaEscolhido}
                />

                <VeiculoPickerFusion
                  id="select-veiculo"
                  sugestao={previewCarga.data.sugestao_veiculo}
                  escolhido={veiculoEscolhido}
                  onEscolher={setVeiculoEscolhido}
                />
              </div>

              {faltaSelecao && (
                <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
                  Selecione {!motoristaResolvido && !veiculoResolvido
                    ? "motorista e veículo"
                    : !motoristaResolvido
                      ? "um motorista"
                      : "um veículo"} antes de enviar.
                </p>
              )}

              <ResumoCard
                rotulo="Carga"
                payload={previewCarga.data.payload}
                motorista={motoristaEscolhido ?? previewCarga.data.sugestao_motorista}
                veiculo={veiculoEscolhido ?? previewCarga.data.sugestao_veiculo}
              />

              {previewCarga.data.avisos.length > 0 && (
                <ul className="list-disc rounded-md bg-yellow-500/10 px-3 py-2.5 pl-6 text-sm text-foreground">
                  {previewCarga.data.avisos.map((aviso) => (
                    <li key={aviso}>{aviso}</li>
                  ))}
                </ul>
              )}

              <PedidosParaEnvio
                tipo="carga"
                itens={previewCarga.data.itens}
                selecionados={itensSelecionados}
                onAlternarPedido={(numeros) => alternarPedido(numeros, previewCarga.data.itens)}
              />

              {previewCarga.data.item_fila?.status === "ENVIADA" && !confirmarReenvio && (
                <p className="rounded-md bg-yellow-500/10 px-3 py-2.5 text-sm text-foreground">
                  {previewCarga.data.item_fila.integrada_manualmente
                    ? "Esta carga já foi marcada como integrada manualmente (sem passar por esta tool) em "
                    : "Esta carga já foi enviada ao Fusion em "}
                  {formatarData(previewCarga.data.item_fila.enviado_em)}.
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                {previewCarga.data.item_fila?.status === "ENVIADA" && !confirmarReenvio ? (
                  <Button type="button" variant="outline" onClick={() => setConfirmarReenvio(true)}>
                    Reenviar mesmo assim
                  </Button>
                ) : (
                  <>
                    <Button
                      type="button"
                      variant={confirmarReenvio ? "destructive" : "default"}
                      onClick={() => enviarCargaSelecionada(confirmarReenvio)}
                      disabled={enviar.isPending || faltaSelecao}
                      title={faltaSelecao ? "Selecione motorista e veículo antes de enviar." : undefined}
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
            );
          })()}
        </div>
      )}

      {selecionada?.tipo === "pre-carga" && (
        <div className="flex flex-col gap-3 rounded-md border border-border p-4">
          <p className="flex items-center gap-2 text-sm font-medium text-foreground">
            Pré-carga {selecionada.idEmpresa}_{selecionada.idPrecarga}
            <TagPreCarga />
          </p>

          {previewPreCarga.isPending && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="animate-spin size-4" /> Montando prévia...
            </p>
          )}

          {previewPreCarga.isSuccess && (() => {
            const faltaSelecao = !motoristaEscolhido || !veiculoEscolhido;
            return (
            <>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <MotoristaPickerFusion
                  id="pc-select-motorista"
                  sugestao={null}
                  escolhido={motoristaEscolhido}
                  onEscolher={setMotoristaEscolhido}
                />

                <VeiculoPickerFusion
                  id="pc-select-veiculo"
                  sugestao={null}
                  escolhido={veiculoEscolhido}
                  onEscolher={setVeiculoEscolhido}
                />
              </div>

              {faltaSelecao && (
                <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
                  Selecione {!motoristaEscolhido && !veiculoEscolhido
                    ? "motorista e veículo"
                    : !motoristaEscolhido
                      ? "um motorista"
                      : "um veículo"} antes de enviar.
                </p>
              )}

              <ResumoCard
                rotulo="Pré-carga"
                payload={previewPreCarga.data.payload}
                motorista={motoristaEscolhido}
                veiculo={veiculoEscolhido}
              />

              {previewPreCarga.data.avisos.length > 0 && (
                <ul className="list-disc rounded-md bg-yellow-500/10 px-3 py-2.5 pl-6 text-sm text-foreground">
                  {previewPreCarga.data.avisos.map((aviso) => (
                    <li key={aviso}>{aviso}</li>
                  ))}
                </ul>
              )}

              <PedidosParaEnvio
                tipo="pre-carga"
                itens={previewPreCarga.data.itens}
                selecionados={itensSelecionados}
                onAlternarPedido={(numeros) => alternarPedido(numeros, previewPreCarga.data.itens)}
              />

              {previewPreCarga.data.item_fila?.status === "ENVIADA" && !confirmarReenvio && (
                <p className="rounded-md bg-yellow-500/10 px-3 py-2.5 text-sm text-foreground">
                  Esta pré-carga já foi enviada ao Fusion em{" "}
                  {formatarData(previewPreCarga.data.item_fila.enviado_em)}.
                </p>
              )}

              <div className="flex flex-wrap gap-2">
                {previewPreCarga.data.item_fila?.status === "ENVIADA" && !confirmarReenvio ? (
                  <Button type="button" variant="outline" onClick={() => setConfirmarReenvio(true)}>
                    Reenviar mesmo assim
                  </Button>
                ) : (
                  <Button
                    type="button"
                    variant={confirmarReenvio ? "destructive" : "default"}
                    onClick={() => enviarPreCargaSelecionada(confirmarReenvio)}
                    disabled={enviarPreCarga.isPending || faltaSelecao}
                    title={faltaSelecao ? "Selecione motorista e veículo antes de enviar." : undefined}
                  >
                    {enviarPreCarga.isPending ? <LoaderCircle className="animate-spin" /> : <Send />}
                    {confirmarReenvio ? "Confirmar reenvio" : "Enviar pré-carga ao Fusion"}
                  </Button>
                )}
              </div>

              {enviarPreCarga.isSuccess && <ResultadoEnvio resultado={enviarPreCarga.data} />}
            </>
            );
          })()}
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
            <ResumoCard
              rotulo="Carga"
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
  /** CLIENTE_FORNECEDOR.NOME de quem é este pedido — mesmo em toda linha
   * do pedido, pega da primeira. */
  nomeCliente: string | null;
  numeros: number[];
  valorTotal: number;
  pesoTotal: number;
  itens: ItemComPedido[];
}

/** O peso de uma linha — nunca só `PESOBRUTO` (`ESTOQUE_ANALITICO`, o
 * movimento de venda): essa coluna vem `0` em ~96% das linhas desta base
 * (achado em produção, carga `1_22142248` — nenhum dos 10 itens tinha
 * `PESOBRUTO` de movimento, mas 9 tinham `PRODUTO_PESOBRUTO` de catálogo).
 * `PRODUTO_PESOBRUTO` é peso por unidade da grade, por isso `× QTDPRODUTO`
 * — mesmo cálculo que o backend usa pra montar o payload do Fusion; sem
 * isso a tela mostraria peso zerado enquanto o envio real já vai certo. */
function pesoEfetivo(item: ItemComPedido): number {
  if (item.PESOBRUTO) return item.PESOBRUTO;
  return (item.PRODUTO_PESOBRUTO ?? 0) * item.QTDPRODUTO;
}

function agruparPorPedido(itens: ItemComPedido[]): PedidoAgrupado[] {
  // Um "pedido" pode ter mais de uma linha em PRODUTO_RETIRADO — e vai pro
  // saveEntregaServico com todo o itens[] de uma vez (D10 da spec), então a
  // tela agrupa por pedido, não por linha. O agrupamento usa IDPLANILHADOCORI
  // (a pré-nota, sempre presente) como chave — o mesmo que o backend faz.
  //
  // Pedido sem IDORCAMENTO (sem orçamento vinculado no CISS) não aparece
  // aqui — o backend também o ignora ao montar o envio (D11 revisado).
  //
  // Pré-carga não tem NUMNOTA/SERIENOTA (a NF só nasce quando a carga é
  // formada) — `"NUMNOTA" in l` cobre os dois tipos sem precisar de um
  // branch por `tipo`.
  const grupos = new Map<number, ItemComPedido[]>();
  for (const item of itens) {
    if (item.IDORCAMENTO == null) continue;
    const grupo = grupos.get(item.IDPLANILHADOCORI);
    if (grupo) grupo.push(item);
    else grupos.set(item.IDPLANILHADOCORI, [item]);
  }
  return Array.from(grupos.entries()).map(([idPlanilhaDocOri, linhas]) => {
    const primeiroComNota = linhas.find(
      (l): l is ItemCargaFusion => "NUMNOTA" in l && l.NUMNOTA != null
    );
    return {
      idPlanilhaDocOri,
      numeroPedido: linhas[0].IDORCAMENTO as number,
      nf: primeiroComNota ? `${primeiroComNota.NUMNOTA}-${primeiroComNota.SERIENOTA ?? "?"}` : null,
      nomeCliente: linhas[0].NOME_CLIENTE,
      numeros: linhas.map((l) => l.NUMSEQUENCIA),
      valorTotal: linhas.reduce((soma, l) => soma + (l.VALTOTLIQUIDO ?? 0), 0),
      pesoTotal: linhas.reduce((soma, l) => soma + pesoEfetivo(l), 0),
      itens: linhas,
    };
  });
}

/** O que vai pro Fusion, em cards legíveis — nada de payload JSON cru na
 * tela. Cada pedido é selecionável como unidade (checkbox) e expansível
 * (seta) pra ver os itens de produto que entram no saveEntregaServico.
 * Compartilhado entre carga e pré-carga — só o texto de "sem entregas" e o
 * rótulo de NF sem valor mudam com `tipo`. */
function PedidosParaEnvio({
  tipo,
  itens,
  selecionados,
  onAlternarPedido,
}: {
  tipo: "carga" | "pre-carga";
  itens: ItemComPedido[];
  selecionados: Set<number> | null;
  onAlternarPedido: (numerosDoPedido: number[]) => void;
}) {
  const [expandidos, setExpandidos] = useState<Set<number>>(new Set());

  if (itens.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        {tipo === "carga" ? "Esta carga não tem entregas." : "Esta pré-carga não tem entregas."}
      </p>
    );
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
                      <span className="font-normal text-muted-foreground">
                        {" "}
                        · NF {pedido.nf ?? (tipo === "pre-carga" ? "ainda não emitida" : "—")}
                      </span>
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {pedido.nomeCliente ?? "Cliente não identificado"}
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
                              · {formatarNumero(pesoEfetivo(item), 2)} kg
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

/** O resumo da carga/pré-carga em si — destino, saída, motorista, veículo —
 * lido do payload só pelos campos que interessam à pessoa, nunca como JSON
 * cru. `destino` só aparece quando o payload tem o campo preenchido —
 * pré-carga nunca tem (DBA.PRE_CARGA não guarda UF/cidade). */
function ResumoCard({
  rotulo,
  payload,
  motorista,
  veiculo,
}: {
  rotulo: string;
  payload: Record<string, unknown>;
  motorista: MotoristaFusion | null;
  veiculo: VeiculoFusion | null;
}) {
  const carga = (payload.cargas as Record<string, unknown>[] | undefined)?.[0];
  if (!carga) return null;
  const destino = carga.destino ? String(carga.destino) : null;

  return (
    <Card className="gap-3 py-4">
      <CardHeader className="gap-1 px-4">
        <CardTitle className="flex items-center gap-2 text-sm">
          <Truck className="size-4 text-primary" />
          {rotulo} {String(carga.codigo_erp ?? "—")} — o que vai pro Fusion
        </CardTitle>
      </CardHeader>
      <CardContent
        className={`grid grid-cols-2 gap-x-4 gap-y-3 px-4 text-sm ${destino ? "sm:grid-cols-4" : "sm:grid-cols-3"}`}
      >
        {destino && <CampoResumo label="Destino" valor={destino} />}
        <CampoResumo label="Saída" valor={formatarData(carga.dt_saida ? String(carga.dt_saida) : null)} />
        <CampoResumo
          label="Motorista"
          valor={motorista ? `${motorista.nome} (${motorista.codigo_erp})` : "— nenhum —"}
        />
        <CampoResumo label="Veículo" valor={veiculo ? rotuloVeiculo(veiculo) : "— nenhum —"} />
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
