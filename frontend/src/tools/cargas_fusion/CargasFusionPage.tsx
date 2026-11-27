import { useQuery } from "@tanstack/react-query";
import {
  Activity,
  BarChart3,
  History,
  Info,
  LoaderCircle,
  Radar,
  Settings,
  Truck,
  Users,
} from "lucide-react";
import { useEffect, useState } from "react";

import { getSetupStatus } from "@/api";
import {
  BarraStatus,
  BarraTitulo,
  CabecalhoPainel,
  JanelaCentral,
  Lateral,
  type ItemNavegacao,
} from "@/components/shell";
import { Button } from "@/components/ui/button";
import { AdministracaoSection } from "@/tools/cargas_fusion/AdministracaoSection";
import { BuscaCargasSection } from "@/tools/cargas_fusion/BuscaCargasSection";
import { CadastroPessoaSection } from "@/tools/cargas_fusion/CadastroPessoaSection";
import { DiagnosticoSection } from "@/tools/cargas_fusion/DiagnosticoSection";
import { EnvioAutomaticoStatus } from "@/tools/cargas_fusion/EnvioAutomaticoStatus";
import { FusionServiceLoginForm } from "@/tools/cargas_fusion/FusionServiceLoginForm";
import { HistoricoPreCargaSection } from "@/tools/cargas_fusion/HistoricoPreCargaSection";
import { HistoricoSection } from "@/tools/cargas_fusion/HistoricoSection";
import { MetricasSection } from "@/tools/cargas_fusion/MetricasSection";
import { RastreioSection } from "@/tools/cargas_fusion/RastreioSection";
import { SetupInicialForm } from "@/tools/cargas_fusion/SetupInicialForm";
import {
  useAjudantes,
  useAtualizarAjudante,
  useCandidatosAjudante,
  useCriarAjudante,
  useSincronizarAjudante,
} from "@/tools/cargas_fusion/useCadastroFusion";
import { useAppConfig } from "@/tools/cargas_fusion/useCargasFusion";
import { useFusionServiceAuth } from "@/tools/cargas_fusion/useFusionServiceAuth";

type Secao = "busca" | "historico" | "rastreio" | "metricas" | "diagnostico" | "ajudantes" | "administracao";

const NAVEGACAO: (ItemNavegacao<Secao> & { descricao: string })[] = [
  {
    chave: "busca",
    rotulo: "Cargas e pré-cargas",
    icone: Truck,
    grupo: "Operação",
    descricao: "Buscar no CISSPoder e enviar ao Fusion.",
  },
  {
    chave: "historico",
    rotulo: "Histórico de envios",
    icone: History,
    grupo: "Operação",
    descricao: "O que já foi mandado ao Fusion, e o que falhou.",
  },
  {
    chave: "rastreio",
    rotulo: "Rastreio",
    icone: Radar,
    grupo: "Integração",
    descricao: "Fusion → CISS: getIntErp, entregas e eventos gravados em DBA.*_FUSION_TRACK.",
  },
  {
    chave: "metricas",
    rotulo: "Métricas",
    icone: BarChart3,
    grupo: "Integração",
    descricao: "Volume de envio e pré-cargas fora do escopo automático.",
  },
  {
    chave: "diagnostico",
    rotulo: "Ciclo automático",
    icone: Activity,
    grupo: "Integração",
    descricao: "Estado, controle e duração do ciclo de envio automático.",
  },
  {
    chave: "ajudantes",
    rotulo: "Ajudantes",
    icone: Users,
    grupo: "Cadastros",
    descricao: "Cadastro local de ajudantes, sincronizado com o Fusion.",
  },
];

const ADMINISTRACAO = {
  chave: "administracao" as const,
  rotulo: "Administração",
  icone: Settings,
  grupo: "Sistema",
  descricao: "Configuração da integração e usuários com acesso.",
};

/** Usuário logado mas não admin, numa instalação sem admin: sem isto, o
 * caminho pro setup só existia na tela de login. */
function AvisoSetupPendente({ onSetup }: { onSetup: () => void }) {
  const setup = useQuery({ queryKey: ["setup-status"], queryFn: getSetupStatus });
  if (!setup.data?.necessario) return null;
  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-border bg-info/10 px-4 py-1.5 text-xs">
      <Info className="size-3.5 text-info-text" />
      <span>Esta instalação ainda não tem administrador.</span>
      <Button type="button" size="xs" variant="outline" className="ml-auto" onClick={onSetup}>
        Fazer o setup inicial
      </Button>
    </div>
  );
}

export function CargasFusionPage() {
  const fusionAuth = useFusionServiceAuth();
  const appConfig = useAppConfig(fusionAuth.status === "ready");
  const mostrarCargasCiss = appConfig.data?.mostrar_cargas_ciss ?? false;
  const somentePedidosSoltos = appConfig.data?.somente_pedidos_soltos ?? true;
  const [secao, setSecao] = useState<Secao>("busca");
  const [cargaNoHistorico, setCargaNoHistorico] = useState<{
    idEmpresa: number;
    idPlanilha: number;
  } | null>(null);

  const admin = fusionAuth.status === "ready" && fusionAuth.user.admin;
  const itens = admin ? [...NAVEGACAO, ADMINISTRACAO] : NAVEGACAO;
  const atual = itens.find((i) => i.chave === secao) ?? itens[0];

  // Alt+1..9 troca de seção, como atalho de aplicativo desktop.
  useEffect(() => {
    if (fusionAuth.status !== "ready") return;
    const ouvir = (e: KeyboardEvent) => {
      if (!e.altKey || e.ctrlKey || e.metaKey) return;
      const indice = Number(e.key) - 1;
      if (Number.isInteger(indice) && indice >= 0 && indice < itens.length) {
        e.preventDefault();
        setSecao(itens[indice].chave);
      }
    };
    window.addEventListener("keydown", ouvir);
    return () => window.removeEventListener("keydown", ouvir);
  }, [fusionAuth.status, itens]);

  function abrirCargaDoHistorico(idEmpresa: number, idPlanilha: number) {
    setCargaNoHistorico({ idEmpresa, idPlanilha });
    setSecao("busca");
  }

  if (fusionAuth.status !== "ready") {
    return (
      <div className="flex h-full flex-col">
        <BarraTitulo />
        {fusionAuth.status === "restaurando" && (
          <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
            <LoaderCircle className="size-4 animate-spin" /> Restaurando sessão…
          </div>
        )}
        {fusionAuth.status === "precisa-login" && (
          <JanelaCentral titulo="Entrar">
            <FusionServiceLoginForm
              loginDireto={fusionAuth.loginDireto}
              mensagemInicial={fusionAuth.mensagem}
              onSetup={fusionAuth.setupDisponivel ? fusionAuth.irParaSetup : undefined}
            />
          </JanelaCentral>
        )}
        {fusionAuth.status === "setup" && (
          <JanelaCentral titulo="Setup inicial">
            <SetupInicialForm
              cissApiUrlInicial={fusionAuth.cissApiUrl}
              fazerSetup={fusionAuth.fazerSetup}
              voltarParaLogin={fusionAuth.voltarParaLogin}
            />
          </JanelaCentral>
        )}
        <BarraStatus />
      </div>
    );
  }

  return (
    <div className="flex h-full flex-col">
      <BarraTitulo usuario={fusionAuth.user} onSair={fusionAuth.sair} />
      {!admin && <AvisoSetupPendente onSetup={fusionAuth.irParaSetup} />}
      <div className="flex min-h-0 flex-1">
        <Lateral itens={itens} ativo={atual.chave} onSelecionar={setSecao} />
        <main className="flex min-w-0 flex-1 flex-col">
          <CabecalhoPainel titulo={atual.rotulo} descricao={atual.descricao} icone={atual.icone} />
          <div className="min-h-0 flex-1 overflow-y-auto">
            <div className="flex flex-col gap-4 p-5">
              {atual.chave === "busca" && (
                <BuscaCargasSection
                  cargaPreSelecionada={cargaNoHistorico}
                  mostrarCargasCiss={mostrarCargasCiss}
                  somentePedidosSoltos={somentePedidosSoltos}
                />
              )}
              {atual.chave === "historico" &&
                (mostrarCargasCiss ? (
                  <HistoricoSection onAbrirCarga={abrirCargaDoHistorico} />
                ) : (
                  <HistoricoPreCargaSection />
                ))}
              {atual.chave === "rastreio" && <RastreioSection />}
              {atual.chave === "metricas" && <MetricasSection />}
              {atual.chave === "diagnostico" && (
                <>
                  {appConfig.data?.auto_envio_ativo ? (
                    <EnvioAutomaticoStatus />
                  ) : (
                    // O widget só olha a pausa: com o recurso desligado na
                    // configuração ele dizia "ativo" sem nada rodar.
                    <p className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm text-muted-foreground">
                      <Info className="size-4 shrink-0" />
                      Envio automático desligado nesta instalação
                      {admin ? " — ligue em Administração → Recursos." : "."}
                    </p>
                  )}
                  <DiagnosticoSection />
                </>
              )}
              {atual.chave === "ajudantes" && (
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
              {atual.chave === "administracao" && admin && <AdministracaoSection eu={fusionAuth.user.username} />}
            </div>
          </div>
        </main>
      </div>
      <BarraStatus config={appConfig.data} />
    </div>
  );
}
