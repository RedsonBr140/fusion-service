import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  enviarPreCargaFusion,
  getFilaPreCargasFusion,
  getPreCargaFusionPreview,
  getPreCargasFusion,
  removerDaFilaPreCargasFusion,
} from "@/api";
import type { SelecaoEnvioPreCargaFusion } from "@/types";

export function useBuscarPreCargas() {
  return useMutation({
    mutationFn: ({
      dataInicial,
      dataFinal,
      idEmpresa,
      ocultarEnviadas,
    }: {
      dataInicial: string;
      dataFinal: string;
      idEmpresa?: number | null;
      ocultarEnviadas?: boolean;
    }) =>
      getPreCargasFusion(dataInicial, dataFinal, 100, idEmpresa, ocultarEnviadas).then(
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
