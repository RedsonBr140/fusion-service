import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getFilaCargasFusion, removerDaFilaCargasFusion } from "@/api";

/** `todas = false` restringe ao que hoje está pendente (erro/aviso
 * bloqueante não resolvido) — `true` é o histórico completo, com carga
 * enviada também. A key depende de `todas` pra não misturar cache. */
export function useFila(todas: boolean) {
  return useQuery({
    queryKey: ["cargas-fusion", "fila", todas],
    queryFn: async () => (await getFilaCargasFusion(todas)).fila,
  });
}

export function useRemoverDaFila() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => removerDaFilaCargasFusion(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["cargas-fusion", "fila"] }),
  });
}
