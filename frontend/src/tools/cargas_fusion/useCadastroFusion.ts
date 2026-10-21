import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  atualizarAjudanteFusion,
  criarAjudanteFusion,
  getAjudantesFusion,
  getCandidatosAjudanteFusion,
  getMotoristasFusion,
  getVeiculosFusion,
  sincronizarAjudanteFusion,
} from "@/api";
import type { AjudanteFusion } from "@/types";

// --- Veículo e motorista (lidos ao vivo do Fusion) -------------------------
// Sem cadastro local — busca por placa/descrição ou por nome no portal.

export function useBuscarVeiculos() {
  return useMutation({
    mutationFn: (termo: string) => getVeiculosFusion(termo).then((r) => r.veiculos),
  });
}

export function useBuscarMotoristas() {
  return useMutation({
    mutationFn: (termo: string) => getMotoristasFusion(termo).then((r) => r.motoristas),
  });
}

// --- Ajudante -----------------------------------------------------------

export function useAjudantes() {
  return useQuery({
    queryKey: ["cargas-fusion", "ajudantes"],
    queryFn: async () => (await getAjudantesFusion()).ajudantes,
  });
}

export function useCriarAjudante() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dados: Partial<AjudanteFusion>) => criarAjudanteFusion(dados),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "ajudantes"] }),
  });
}

export function useAtualizarAjudante() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, dados }: { id: number; dados: Partial<AjudanteFusion> }) =>
      atualizarAjudanteFusion(id, dados),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "ajudantes"] }),
  });
}

export function useCandidatosAjudante() {
  return useMutation({
    mutationFn: (termo: string) => getCandidatosAjudanteFusion(termo).then((r) => r.candidatos),
  });
}

export function useSincronizarAjudante() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => sincronizarAjudanteFusion(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "ajudantes"] }),
  });
}
