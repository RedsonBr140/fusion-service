import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  enviarCargaFusion,
  getCargaFusionPreview,
  getCargasFusion,
  getEmpresasCargasFusion,
  marcarCargaFusionIntegrada,
} from "@/api";
import type { SelecaoEnvioCargaFusion } from "@/types";

export function useBuscarCargas() {
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
      getCargasFusion(dataInicial, dataFinal, 100, idEmpresa, ocultarEnviadas, apenasFusion).then(
        (r) => r.cargas
      ),
  });
}

export function useEmpresasCargasFusion() {
  return useQuery({
    queryKey: ["cargas-fusion", "empresas"],
    queryFn: async () => (await getEmpresasCargasFusion()).empresas,
  });
}

/** `selecao` entra na query key — mudar motorista/veículo/itens escolhidos
 * busca uma prévia nova, já refletindo a escolha. */
export function useCargaPreview(
  selecionada: { idEmpresa: number; idPlanilha: number } | null,
  selecao: SelecaoEnvioCargaFusion = {}
) {
  return useQuery({
    queryKey: [
      "cargas-fusion",
      "preview",
      selecionada?.idEmpresa,
      selecionada?.idPlanilha,
      selecao.motoristaCodigoErp,
      selecao.veiculoCodigoErp,
      selecao.itens?.join(","),
    ],
    queryFn: () => getCargaFusionPreview(selecionada!.idEmpresa, selecionada!.idPlanilha, selecao),
    enabled: selecionada !== null,
  });
}

export function useEnviarCarga() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({
      idEmpresa,
      idPlanilha,
      selecao,
      forcar,
    }: {
      idEmpresa: number;
      idPlanilha: number;
      selecao?: SelecaoEnvioCargaFusion;
      forcar?: boolean;
    }) => enviarCargaFusion(idEmpresa, idPlanilha, selecao, forcar),
    // Enviar pode criar ou resolver um item da fila — a lista e a prévia
    // (item_fila) saem de sincronia.
    onSuccess: (_dados, variaveis) => {
      queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "fila"] });
      queryClient.invalidateQueries({
        queryKey: ["cargas-fusion", "preview", variaveis.idEmpresa, variaveis.idPlanilha],
      });
    },
  });
}

export function useMarcarCargaIntegrada() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ idEmpresa, idPlanilha }: { idEmpresa: number; idPlanilha: number }) =>
      marcarCargaFusionIntegrada(idEmpresa, idPlanilha),
    onSuccess: (_dados, variaveis) => {
      queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "fila"] });
      queryClient.invalidateQueries({
        queryKey: ["cargas-fusion", "preview", variaveis.idEmpresa, variaveis.idPlanilha],
      });
    },
  });
}
