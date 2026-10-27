import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  enviarPreCargaFusion,
  enviarPreCargaFusionSolta,
  getEnvioAutomaticoEstado,
  getFilaPreCargasFusion,
  getPreCargaFusionPreview,
  getPreCargasFusion,
  pausarEnvioAutomatico,
  removerDaFilaPreCargasFusion,
  retomarEnvioAutomatico,
} from "@/api";
import type { SelecaoEnvioPreCargaFusion } from "@/types";

export function useBuscarPreCargas() {
  return useMutation({
    mutationFn: ({
      dataInicial,
      dataFinal,
      idEmpresa,
      ocultarEnviadas,
      apenasFusion,
    }: {
      dataInicial: string;
      dataFinal: string;
      idEmpresa?: number | null;
      ocultarEnviadas?: boolean;
      apenasFusion?: boolean;
    }) =>
      getPreCargasFusion(dataInicial, dataFinal, 100, idEmpresa, ocultarEnviadas, apenasFusion).then(
        (r) => r.pre_cargas
      ),
  });
}

/** `selecao` entra na query key — mudar motorista/veículo/itens escolhidos
 * busca uma prévia nova, já refletindo a escolha. */
export function usePreCargaPreview(
  selecionada: { idEmpresa: number; idPrecarga: number } | null,
  selecao: SelecaoEnvioPreCargaFusion = {}
) {
  return useQuery({
    queryKey: [
      "cargas-fusion",
      "pre-carga-preview",
      selecionada?.idEmpresa,
      selecionada?.idPrecarga,
      selecao.motoristaCodigoErp,
      selecao.veiculoCodigoErp,
      selecao.itens?.join(","),
      selecao.dtSaida,
    ],
    queryFn: () =>
      getPreCargaFusionPreview(selecionada!.idEmpresa, selecionada!.idPrecarga, selecao),
    enabled: selecionada !== null,
  });
}

export function useEnviarPreCarga() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      idEmpresa,
      idPrecarga,
      selecao,
      forcar,
    }: {
      idEmpresa: number;
      idPrecarga: number;
      selecao?: SelecaoEnvioPreCargaFusion;
      forcar?: boolean;
    }) => enviarPreCargaFusion(idEmpresa, idPrecarga, selecao, forcar),
    onSuccess: (_dados, variaveis) => {
      queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "pre-carga-fila"] });
      queryClient.invalidateQueries({
        queryKey: [
          "cargas-fusion",
          "pre-carga-preview",
          variaveis.idEmpresa,
          variaveis.idPrecarga,
        ],
      });
    },
  });
}

/** Modo "solto" (ver api.ts `enviarPreCargaFusionSolta`) — só habilitado
 * no preview quando `permite_envio_solto` é `true` (pré-carga de pedido
 * único). */
export function useEnviarPreCargaSolta() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      idEmpresa,
      idPrecarga,
      itens,
      forcar,
    }: {
      idEmpresa: number;
      idPrecarga: number;
      itens?: number[] | null;
      forcar?: boolean;
    }) => enviarPreCargaFusionSolta(idEmpresa, idPrecarga, itens, forcar),
    onSuccess: (_dados, variaveis) => {
      queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "pre-carga-fila"] });
      queryClient.invalidateQueries({
        queryKey: [
          "cargas-fusion",
          "pre-carga-preview",
          variaveis.idEmpresa,
          variaveis.idPrecarga,
        ],
      });
    },
  });
}

export function useFilaPreCargasFusion(todas = false) {
  return useQuery({
    queryKey: ["cargas-fusion", "pre-carga-fila", todas],
    queryFn: async () => (await getFilaPreCargasFusion(todas)).fila,
  });
}

export function useRemoverDaFilaPreCargas() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => removerDaFilaPreCargasFusion(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "pre-carga-fila"] });
    },
  });
}

/** Estado do ciclo automático (pausado/última execução/multi-pedido
 * ignorado) — widget de status na página, não ligado a nenhuma busca. */
export function useEnvioAutomaticoEstado() {
  return useQuery({
    queryKey: ["cargas-fusion", "envio-automatico"],
    queryFn: getEnvioAutomaticoEstado,
  });
}

function useAtualizarEnvioAutomatico(mutationFn: () => ReturnType<typeof pausarEnvioAutomatico>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "envio-automatico"] });
    },
  });
}

export function usePausarEnvioAutomatico() {
  return useAtualizarEnvioAutomatico(pausarEnvioAutomatico);
}

export function useRetomarEnvioAutomatico() {
  return useAtualizarEnvioAutomatico(retomarEnvioAutomatico);
}
