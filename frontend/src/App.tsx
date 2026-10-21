import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { CargasFusionPage } from "@/tools/cargas_fusion/CargasFusionPage";

const queryClient = new QueryClient();

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <CargasFusionPage />
    </QueryClientProvider>
  );
}
