import { LoaderCircle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BuscaCargasSection } from "@/tools/cargas_fusion/BuscaCargasSection";
import { CadastroPessoaSection } from "@/tools/cargas_fusion/CadastroPessoaSection";
import { DiagnosticoSection } from "@/tools/cargas_fusion/DiagnosticoSection";
import { EnvioAutomaticoStatus } from "@/tools/cargas_fusion/EnvioAutomaticoStatus";
import { FusionServiceLoginForm } from "@/tools/cargas_fusion/FusionServiceLoginForm";
import { HistoricoPreCargaSection } from "@/tools/cargas_fusion/HistoricoPreCargaSection";
import { HistoricoSection } from "@/tools/cargas_fusion/HistoricoSection";
import { MetricasSection } from "@/tools/cargas_fusion/MetricasSection";
import {
  useAjudantes,
  useAtualizarAjudante,
  useCandidatosAjudante,
  useCriarAjudante,
  useSincronizarAjudante,
} from "@/tools/cargas_fusion/useCadastroFusion";
import { useAppConfig } from "@/tools/cargas_fusion/useCargasFusion";
import { useFusionServiceAuth } from "@/tools/cargas_fusion/useFusionServiceAuth";

type Secao = "busca" | "historico" | "metricas" | "diagnostico" | "ajudantes";

const ABAS: { chave: Secao; rotulo: string }[] = [
  { chave: "busca", rotulo: "Buscar cargas" },
  { chave: "historico", rotulo: "Histórico" },
  { chave: "metricas", rotulo: "Métricas" },
  { chave: "diagnostico", rotulo: "Diagnóstico" },
  { chave: "ajudantes", rotulo: "Ajudantes" },
];

export function CargasFusionPage() {
  const fusionAuth = useFusionServiceAuth();
  const appConfig = useAppConfig();
  const mostrarCargasCiss = appConfig.data?.mostrar_cargas_ciss ?? false;
  const somentePedidosSoltos = appConfig.data?.somente_pedidos_soltos ?? true;
  const [secao, setSecao] = useState<Secao>("busca");
  const [cargaNoHistorico, setCargaNoHistorico] = useState<{
    idEmpresa: number;
    idPlanilha: number;
  } | null>(null);

  function abrirCargaDoHistorico(idEmpresa: number, idPlanilha: number) {
    setCargaNoHistorico({ idEmpresa, idPlanilha });
    setSecao("busca");
  }

  return (
    <div className="min-h-screen bg-background p-8">
      <Card className="mx-auto max-w-5xl">
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle className="text-2xl tracking-tight">Cargas Fusion</CardTitle>
              <p className="text-sm text-muted-foreground">
                Buscar cargas e pré-cargas no CISSPoder e integrá-las ao Fusion.
                Motorista e veículo vêm direto do Fusion; ajudante vem de um
                cadastro próprio — não do CISS a cada busca.
              </p>
            </div>
            {fusionAuth.status === "ready" && (
              <div className="flex shrink-0 items-center gap-2">
                <span className="text-sm text-muted-foreground">{fusionAuth.user.name}</span>
                <Button type="button" variant="outline" size="sm" onClick={fusionAuth.sair}>
                  Sair
                </Button>
              </div>
            )}
          </div>
        </CardHeader>

        <CardContent className="flex flex-col gap-6">
          {fusionAuth.status === "restaurando" && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <LoaderCircle className="animate-spin size-4" /> Restaurando sessão...
            </p>
          )}

          {fusionAuth.status === "precisa-login" && (
            <FusionServiceLoginForm
              loginDireto={fusionAuth.loginDireto}
              mensagemInicial={fusionAuth.mensagem}
            />
          )}

          {fusionAuth.status === "ready" && (
            <>
              <EnvioAutomaticoStatus />

              <div className="flex flex-wrap gap-2 border-b border-border pb-4">
                {ABAS.map((aba) => (
                  <Button
                    key={aba.chave}
                    type="button"
                    variant={secao === aba.chave ? "default" : "outline"}
                    size="sm"
                    onClick={() => setSecao(aba.chave)}
                  >
                    {aba.rotulo}
                  </Button>
                ))}
              </div>

              {secao === "busca" && (
                <BuscaCargasSection
                  cargaPreSelecionada={cargaNoHistorico}
                  mostrarCargasCiss={mostrarCargasCiss}
                  somentePedidosSoltos={somentePedidosSoltos}
                />
              )}
              {secao === "historico" &&
                (mostrarCargasCiss ? (
                  <HistoricoSection onAbrirCarga={abrirCargaDoHistorico} />
                ) : (
                  <HistoricoPreCargaSection />
                ))}
              {secao === "metricas" && <MetricasSection />}
              {secao === "diagnostico" && <DiagnosticoSection />}
              {secao === "ajudantes" && (
                <CadastroPessoaSection
                  tipo="ajudante"
                  listar={useAjudantes}
                  criar={useCriarAjudante}
                  atualizar={useAtualizarAjudante}
                  candidatos={useCandidatosAjudante}
                  sincronizar={useSincronizarAjudante}
                  mostrarCamposExtras={false}
                />
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
