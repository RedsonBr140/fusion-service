// Moldura de "aplicativo desktop": barra de título, navegação lateral,
// cabeçalho de painel, barra de status, e uma janela centrada pras telas
// sem sessão (login/setup). Só layout e estado de cromo — nenhuma regra
// de negócio aqui.

import { useQuery } from "@tanstack/react-query";
import {
  Cable,
  CircleCheck,
  CircleDashed,
  CircleX,
  LoaderCircle,
  LogOut,
  Monitor,
  Moon,
  PanelLeftClose,
  PanelLeftOpen,
  Pause,
  Play,
  Sun,
  Zap,
  type LucideIcon,
} from "lucide-react";
import { useState, type ReactNode } from "react";

import { getHealth } from "@/api";
import { useTema, type Tema } from "@/theme";
import type { AppConfig } from "@/types";
import {
  useEnvioAutomaticoEstado,
  useExecutarCicloAgora,
  usePausarEnvioAutomatico,
  useRetomarEnvioAutomatico,
} from "@/tools/cargas_fusion/usePreCargasFusion";

const NOME_APP = "Fusion Integration";

// --- Ambiente -------------------------------------------------------------

type Ambiente = { rotulo: string; classe: string; titulo: string };

/** Pelo hostname — o mesmo frontend roda em local, staging e produção, e
 * confundir os dois últimos já custou um deploy errado. */
export function ambienteAtual(): Ambiente {
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1" || host.startsWith("192.168.")) {
    return { rotulo: "LOCAL", classe: "bg-info/15 text-info-text", titulo: host };
  }
  if (/staging|homolog|teste/i.test(host)) {
    return { rotulo: "STAGING", classe: "bg-warning/20 text-warning-text", titulo: host };
  }
  return { rotulo: "PRODUÇÃO", classe: "bg-destructive/15 text-destructive-text", titulo: host };
}

function SeloAmbiente() {
  const ambiente = ambienteAtual();
  return (
    <span
      title={ambiente.titulo}
      className={`rounded-sm px-1.5 py-px text-[0.6875rem] font-semibold tracking-wide ${ambiente.classe}`}
    >
      {ambiente.rotulo}
    </span>
  );
}

// --- Barra de título --------------------------------------------------------

const TEMAS: { valor: Tema; icone: LucideIcon; rotulo: string }[] = [
  { valor: "sistema", icone: Monitor, rotulo: "Seguir o sistema" },
  { valor: "claro", icone: Sun, rotulo: "Claro" },
  { valor: "escuro", icone: Moon, rotulo: "Escuro" },
];

function SeletorTema() {
  const { tema, setTema } = useTema();
  return (
    <div className="flex items-center rounded-sm border border-border bg-background/60 p-px" role="radiogroup" aria-label="Tema">
      {TEMAS.map(({ valor, icone: Icone, rotulo }) => (
        <button
          key={valor}
          type="button"
          role="radio"
          aria-checked={tema === valor}
          title={rotulo}
          onClick={() => setTema(valor)}
          className={`grid size-6 place-items-center rounded-[3px] transition-colors ${
            tema === valor ? "bg-card text-foreground shadow-xs" : "text-muted-foreground hover:text-foreground"
          }`}
        >
          <Icone className="size-3.5" />
        </button>
      ))}
    </div>
  );
}

export function BarraTitulo({
  usuario,
  onSair,
}: {
  usuario?: { name: string; admin: boolean };
  onSair?: () => void;
}) {
  return (
    <header className="flex h-10 shrink-0 items-center gap-3 border-b border-border bg-chrome px-3 text-chrome-foreground select-none">
      <div className="flex items-center gap-2">
        <span className="grid size-6 place-items-center rounded-sm bg-primary text-primary-foreground">
          <Cable className="size-3.5" />
        </span>
        <span className="text-[0.8125rem] font-semibold text-foreground">{NOME_APP}</span>
        <span className="hidden text-xs text-muted-foreground sm:inline">CISSPoder ⇄ Fusion DMS</span>
      </div>
      <SeloAmbiente />
      <div className="ml-auto flex items-center gap-2">
        <SeletorTema />
        {usuario && (
          <>
            <span className="mx-1 hidden h-5 w-px bg-border sm:block" />
            <span className="hidden text-xs sm:inline">
              {usuario.name}
              {usuario.admin && <span className="ml-1 text-muted-foreground">· admin</span>}
            </span>
            <button
              type="button"
              onClick={onSair}
              title="Sair"
              className="grid size-7 place-items-center rounded-sm text-muted-foreground hover:bg-accent hover:text-foreground"
            >
              <LogOut className="size-4" />
              <span className="sr-only">Sair</span>
            </button>
          </>
        )}
      </div>
    </header>
  );
}

// --- Navegação lateral ------------------------------------------------------

export type ItemNavegacao<T extends string> = {
  chave: T;
  rotulo: string;
  icone: LucideIcon;
  grupo: string;
};

const CHAVE_LATERAL = "fusion_service_lateral_recolhida";

function lerRecolhida(): boolean {
  try {
    return localStorage.getItem(CHAVE_LATERAL) === "1";
  } catch {
    return false;
  }
}

export function Lateral<T extends string>({
  itens,
  ativo,
  onSelecionar,
}: {
  itens: ItemNavegacao<T>[];
  ativo: T;
  onSelecionar: (chave: T) => void;
}) {
  const [recolhida, setRecolhida] = useState(lerRecolhida);
  const alternar = () => {
    setRecolhida((r) => {
      try {
        localStorage.setItem(CHAVE_LATERAL, r ? "0" : "1");
      } catch {
        // Só esta aba.
      }
      return !r;
    });
  };

  const grupos = [...new Set(itens.map((i) => i.grupo))];

  return (
    <nav
      aria-label="Seções"
      className={`flex shrink-0 flex-col border-r border-border bg-sidebar transition-[width] duration-150 ${
        recolhida ? "w-12" : "w-12 md:w-56"
      }`}
    >
      <div className="flex-1 overflow-y-auto py-2">
        {grupos.map((grupo) => (
          <div key={grupo} className="mb-2">
            <div
              className={`px-4 pt-2 pb-1 text-[0.6875rem] font-semibold tracking-wider text-muted-foreground uppercase ${
                recolhida ? "hidden" : "hidden md:block"
              }`}
            >
              {grupo}
            </div>
            {itens
              .filter((i) => i.grupo === grupo)
              .map((item) => {
                const Icone = item.icone;
                const selecionado = item.chave === ativo;
                const atalho = itens.indexOf(item) + 1;
                return (
                  <button
                    key={item.chave}
                    type="button"
                    onClick={() => onSelecionar(item.chave)}
                    aria-current={selecionado ? "page" : undefined}
                    title={`${item.rotulo}${atalho <= 9 ? ` (Alt+${atalho})` : ""}`}
                    className={`relative mx-1.5 flex h-8 w-[calc(100%-0.75rem)] items-center gap-2.5 rounded-sm px-2.5 text-left text-[0.8125rem] transition-colors ${
                      selecionado
                        ? "bg-sidebar-active font-medium text-sidebar-active-foreground"
                        : "text-foreground/80 hover:bg-accent hover:text-foreground"
                    }`}
                  >
                    {selecionado && <span className="absolute top-1.5 bottom-1.5 left-0 w-0.5 rounded-full bg-primary" />}
                    <Icone className="size-4 shrink-0" />
                    <span className={`truncate ${recolhida ? "hidden" : "hidden md:inline"}`}>{item.rotulo}</span>
                  </button>
                );
              })}
          </div>
        ))}
      </div>
      <button
        type="button"
        onClick={alternar}
        title={recolhida ? "Expandir navegação" : "Recolher navegação"}
        className="hidden h-9 items-center gap-2 border-t border-border px-4 text-xs text-muted-foreground hover:text-foreground md:flex"
      >
        {recolhida ? <PanelLeftOpen className="size-4" /> : <PanelLeftClose className="size-4" />}
        {!recolhida && "Recolher"}
      </button>
    </nav>
  );
}

// --- Cabeçalho de painel ----------------------------------------------------

export function CabecalhoPainel({
  titulo,
  descricao,
  icone: Icone,
  acoes,
}: {
  titulo: string;
  descricao?: string;
  icone?: LucideIcon;
  acoes?: ReactNode;
}) {
  return (
    <div className="flex shrink-0 items-center gap-3 border-b border-border bg-card px-5 py-3">
      {Icone && <Icone className="size-5 text-muted-foreground" />}
      <div className="min-w-0">
        <h1 className="truncate text-base font-semibold leading-tight">{titulo}</h1>
        {descricao && <p className="truncate text-xs text-muted-foreground">{descricao}</p>}
      </div>
      {acoes && <div className="ml-auto flex shrink-0 items-center gap-2">{acoes}</div>}
    </div>
  );
}

// --- Barra de status --------------------------------------------------------

function Segmento({ children, titulo }: { children: ReactNode; titulo?: string }) {
  return (
    <span title={titulo} className="flex h-full items-center gap-1.5 border-r border-border px-2.5 last:border-r-0">
      {children}
    </span>
  );
}

function Ponto({ estado }: { estado: "ok" | "erro" | "pausa" | "off" }) {
  const cor = {
    ok: "bg-success",
    erro: "bg-destructive",
    pausa: "bg-warning",
    off: "bg-muted-foreground/40",
  }[estado];
  return <span className={`inline-block size-2 rounded-full ${cor}`} />;
}

function hora(iso: string | null): string {
  if (!iso) return "nunca";
  const data = new Date(iso);
  return Number.isNaN(data.getTime())
    ? iso
    : data.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

function SegmentoEnvioAutomatico() {
  const estado = useEnvioAutomaticoEstado();
  const pausar = usePausarEnvioAutomatico();
  const retomar = useRetomarEnvioAutomatico();
  const executar = useExecutarCicloAgora();
  if (!estado.data) {
    return (
      <Segmento>
        <Ponto estado="off" /> Envio automático
      </Segmento>
    );
  }
  const d = estado.data;
  const ocupado = pausar.isPending || retomar.isPending;
  return (
    <Segmento
      titulo={`Último ciclo: ${d.ultima_execucao_em ?? "nunca"} — ${d.ultima_execucao_candidatas ?? 0} candidata(s), ${d.ultima_execucao_enviadas ?? 0} enviada(s), ${d.ultima_execucao_falhas ?? 0} falha(s)`}
    >
      <Ponto estado={d.pausado ? "pausa" : (d.ultima_execucao_falhas ?? 0) > 0 ? "erro" : "ok"} />
      Envio automático: {d.pausado ? "pausado" : "ativo"}
      <span className="text-muted-foreground">· {hora(d.ultima_execucao_em)}</span>
      <button
        type="button"
        disabled={ocupado}
        onClick={() => (d.pausado ? retomar.mutate() : pausar.mutate())}
        title={d.pausado ? "Retomar" : "Pausar"}
        className="grid size-5 place-items-center rounded-sm hover:bg-accent disabled:opacity-50"
      >
        {ocupado ? <LoaderCircle className="size-3 animate-spin" /> : d.pausado ? <Play className="size-3" /> : <Pause className="size-3" />}
      </button>
      <button
        type="button"
        disabled={executar.isPending}
        onClick={() => executar.mutate()}
        title="Executar um ciclo agora"
        className="grid size-5 place-items-center rounded-sm hover:bg-accent disabled:opacity-50"
      >
        {executar.isPending ? <LoaderCircle className="size-3 animate-spin" /> : <Zap className="size-3" />}
      </button>
    </Segmento>
  );
}

function Integracao({ nome, ok }: { nome: string; ok: boolean }) {
  return (
    <span className="flex items-center gap-1" title={ok ? `${nome}: configurado` : `${nome}: não configurado`}>
      {ok ? <CircleCheck className="size-3 text-success-text" /> : <CircleDashed className="size-3 text-muted-foreground" />}
      {nome}
    </span>
  );
}

export function BarraStatus({ config }: { config?: AppConfig }) {
  const saude = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    refetchInterval: 30_000,
    retry: false,
  });
  const conectado = saude.isSuccess;

  return (
    <footer className="flex h-6 shrink-0 items-center overflow-x-auto border-t border-border bg-chrome text-[0.6875rem] whitespace-nowrap text-chrome-foreground select-none">
      <Segmento titulo={conectado ? "Backend respondendo" : "Backend não responde"}>
        {saude.isPending ? (
          <LoaderCircle className="size-3 animate-spin" />
        ) : conectado ? (
          <Ponto estado="ok" />
        ) : (
          <CircleX className="size-3 text-destructive-text" />
        )}
        {conectado ? "Conectado" : saude.isPending ? "Conectando…" : "Sem conexão"}
      </Segmento>
      {config && (
        <>
          <Segmento>
            <Integracao nome="CISS DB2" ok={config.ciss_db_configurado} />
            <Integracao nome="Fusion" ok={config.fusion_configurado} />
            <Integracao nome="Portal" ok={config.fusion_portal_configurado} />
          </Segmento>
          {config.auto_envio_ativo ? (
            <SegmentoEnvioAutomatico />
          ) : (
            <Segmento titulo="AUTO_ENVIO_PRE_CARGA_ATIVO desligado na configuração">
              <Ponto estado="off" /> Envio automático: desligado
            </Segmento>
          )}
          <Segmento titulo="Rastreio getIntErp → CISS">
            <Ponto estado={config.rastreio_automatico ? "ok" : config.rastreio_ativo ? "pausa" : "off"} />
            Rastreio: {config.rastreio_automatico ? "automático" : config.rastreio_ativo ? "manual" : "desligado"}
          </Segmento>
        </>
      )}
      <span className="ml-auto px-2.5 text-muted-foreground">{window.location.host}</span>
    </footer>
  );
}

// --- Janela (login/setup) ---------------------------------------------------

export function JanelaCentral({ titulo, children }: { titulo: string; children: ReactNode }) {
  return (
    <div className="flex min-h-0 flex-1 overflow-y-auto bg-background p-4">
      <div className="m-auto w-full max-w-md overflow-hidden rounded-md border border-border bg-card shadow-lg">
        <div className="flex h-8 items-center gap-2 border-b border-border bg-chrome px-3 text-xs text-chrome-foreground select-none">
          <Cable className="size-3.5" />
          {titulo}
        </div>
        <div className="px-6">{children}</div>
      </div>
    </div>
  );
}
