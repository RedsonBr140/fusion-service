import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { executarRastreioAgora, getItemRastreio, getRastreio } from "@/api";

const CHAVE = ["cargas-fusion", "rastreio"] as const;

/** Atualiza sozinho a cada 5s — a tela existe pra acompanhar o ciclo
 * (inclusive o agendado) ao vivo, não só depois de um clique. */
export function useRastreio(status?: string) {
  return useQuery({
    queryKey: [...CHAVE, "lista", status ?? "todas"],
    queryFn: () => getRastreio(status),
    refetchInterval: 5000,
  });
}

export function useItemRastreio(id: number | null) {
  return useQuery({
    queryKey: [...CHAVE, "item", id],
    queryFn: () => getItemRastreio(id as number),
    enabled: id !== null,
    refetchInterval: 5000,
  });
}

export function useExecutarRastreioAgora() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: executarRastreioAgora,
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: CHAVE });
    },
  });
}
