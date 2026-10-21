import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { BuscaCargasSection } from "@/tools/cargas_fusion/BuscaCargasSection";
import { CadastroPessoaSection } from "@/tools/cargas_fusion/CadastroPessoaSection";
import { FusionServiceLoginForm } from "@/tools/cargas_fusion/FusionServiceLoginForm";
import { HistoricoSection } from "@/tools/cargas_fusion/HistoricoSection";
import {
  useAjudantes,
  useAtualizarAjudante,
  useCandidatosAjudante,
  useCriarAjudante,
  useSincronizarAjudante,
} from "@/tools/cargas_fusion/useCadastroFusion";
import { useFusionServiceAuth } from "@/tools/cargas_fusion/useFusionServiceAuth";

type Secao = "busca" | "historico" | "ajudantes";

const ABAS: { chave: Secao; rotulo: string }[] = [
  { chave: "busca", rotulo: "Buscar cargas" },
  { chave: "historico", rotulo: "Histórico" },
  { chave: "ajudantes", rotulo: "Ajudantes" },
];

export function CargasFusionPage() {
  const fusionAuth = useFusionServiceAuth();
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
          <CardTitle className="text-2xl tracking-tight">Cargas Fusion</CardTitle>
          <p className="text-sm text-muted-foreground">
            Buscar cargas e pré-cargas no CISSPoder e integrá-las ao Fusion.
            Motorista e veículo vêm direto do Fusion; ajudante vem de um
            cadastro próprio — não do CISS a cada busca.
          </p>
        </CardHeader>

        <CardContent className="flex flex-col gap-6">
          {fusionAuth.status === "precisa-login" && (
            <FusionServiceLoginForm
              loginDireto={fusionAuth.loginDireto}
              mensagemInicial={fusionAuth.mensagem}
            />
          )}

          {fusionAuth.status === "ready" && (
            <>
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
                <BuscaCargasSection cargaPreSelecionada={cargaNoHistorico} />
              )}
              {secao === "historico" && <HistoricoSection onAbrirCarga={abrirCargaDoHistorico} />}
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
