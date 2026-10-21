import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import {
  atualizarAjudanteFusion,
  atualizarVeiculoFusion,
  criarAjudanteFusion,
  criarVeiculoFusion,
  getAjudantesFusion,
  getCandidatosAjudanteFusion,
  getCandidatosVeiculoFusion,
  getMotoristasFusion,
  getVeiculosFusion,
  sincronizarAjudanteFusion,
  sincronizarVeiculoFusion,
} from "@/api";
import type { AjudanteFusion, VeiculoFusion } from "@/types";

// --- Veículo -----------------------------------------------------------

export function useVeiculos() {
  return useQuery({
    queryKey: ["cargas-fusion", "veiculos"],
    queryFn: async () => (await getVeiculosFusion()).veiculos,
  });
}

export function useCriarVeiculo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (dados: Partial<VeiculoFusion>) => criarVeiculoFusion(dados),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "veiculos"] }),
  });
}

export function useAtualizarVeiculo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, dados }: { id: number; dados: Partial<VeiculoFusion> }) =>
      atualizarVeiculoFusion(id, dados),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "veiculos"] }),
  });
}

export function useCandidatosVeiculo() {
  return useMutation({
    mutationFn: (termo: string) => getCandidatosVeiculoFusion(termo).then((r) => r.candidatos),
  });
}

export function useSincronizarVeiculo() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => sincronizarVeiculoFusion(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "veiculos"] }),
  });
}

// --- Motorista (lido ao vivo do Fusion, 2026-10-20) -----------------------
// Sem cadastro local — busca por nome no portal, via useBuscarMotoristas.

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
