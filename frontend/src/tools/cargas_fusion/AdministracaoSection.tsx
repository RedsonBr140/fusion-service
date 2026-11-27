// Aba "Administração" (só admin): configuração da integração guardada no
// banco (configuracoes.py, no lugar do .env) e allow-list de usuários.
// Configuração vale a partir do próximo reinício — exceto a URL do CISS,
// que o login lê ao vivo.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, AlertTriangle, LoaderCircle, RotateCw } from "lucide-react";
import { useState } from "react";

import {
  ApiError,
  alterarUsuario,
  concederUsuario,
  getConfiguracoes,
  getUsuarios,
  reiniciarWorkers,
  salvarConfiguracoes,
} from "@/api";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import type { CampoConfiguracao, ConfiguracoesAdmin } from "@/types";

type Rascunho = Record<string, string | boolean | null>;

function Erro({ texto }: { texto: string }) {
  return (
    <p role="alert" className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
      <AlertCircle className="mt-0.5 size-4 shrink-0" />
      {texto}
    </p>
  );
}

function valorBool(campo: CampoConfiguracao, rascunho: Rascunho): boolean {
  if (campo.chave in rascunho) return rascunho[campo.chave] === true;
  return (campo.valor ?? campo.padrao ?? "false") === "true";
}

function CampoInput({
  campo,
  rascunho,
  setRascunho,
}: {
  campo: CampoConfiguracao;
  rascunho: Rascunho;
  setRascunho: (r: Rascunho) => void;
}) {
  const id = `cfg-${campo.chave}`;
  const editado = campo.chave in rascunho;
  const definir = (valor: string | boolean | null) => setRascunho({ ...rascunho, [campo.chave]: valor });
  const desfazer = () => {
    const { [campo.chave]: _, ...resto } = rascunho;
    setRascunho(resto);
  };

  const cabecalho = (
    <div className="flex flex-wrap items-center gap-2">
      <Label htmlFor={id}>{campo.rotulo}</Label>
      {campo.perigoso && (
        <span className="rounded bg-destructive/10 px-1.5 py-0.5 text-xs text-destructive-text">sensível</span>
      )}
      {campo.pendente_reinicio && !editado && (
        <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">aguardando reinício</span>
      )}
      {editado && (
        <button type="button" className="text-xs text-muted-foreground underline" onClick={desfazer}>
          desfazer
        </button>
      )}
    </div>
  );
  const rodape = (
    <p className="text-xs text-muted-foreground">
      {campo.descricao}
      {campo.descricao && " "}
      <span className="font-mono">{campo.chave}</span>
      {campo.atualizado_por && ` · por ${campo.atualizado_por}`}
    </p>
  );

  if (campo.tipo === "bool") {
    return (
      <div className="space-y-1">
        {cabecalho}
        <label className="flex items-center gap-2 text-sm">
          <input
            id={id}
            type="checkbox"
            className="size-4"
            checked={valorBool(campo, rascunho)}
            onChange={(e) => definir(e.target.checked)}
          />
          {valorBool(campo, rascunho) ? "Ligado" : "Desligado"}
        </label>
        {rodape}
      </div>
    );
  }

  if (campo.tipo === "segredo") {
    const apagando = editado && rascunho[campo.chave] === null;
    return (
      <div className="space-y-1">
        {cabecalho}
        <div className="flex gap-2">
          <Input
            id={id}
            type="password"
            autoComplete="new-password"
            placeholder={apagando ? "(será apagado)" : campo.definido ? "•••••••• definido — digite para trocar" : "não definido"}
            value={editado && typeof rascunho[campo.chave] === "string" ? (rascunho[campo.chave] as string) : ""}
            onChange={(e) => (e.target.value ? definir(e.target.value) : desfazer())}
          />
          {campo.definido && !apagando && (
            <Button type="button" variant="outline" size="sm" onClick={() => definir(null)}>
              Apagar
            </Button>
          )}
        </div>
        {rodape}
      </div>
    );
  }

  const atual = editado ? ((rascunho[campo.chave] as string | null) ?? "") : (campo.valor ?? "");
  return (
    <div className="space-y-1">
      {cabecalho}
      <Input
        id={id}
        inputMode={campo.tipo === "inteiro" ? "numeric" : undefined}
        placeholder={campo.tipo === "datahora" ? "AAAA-MM-DDTHH:MM:SS" : (campo.padrao ?? "")}
        value={atual}
        onChange={(e) => definir(e.target.value === "" ? null : e.target.value)}
      />
      {rodape}
    </div>
  );
}

function ConfiguracaoPainel() {
  const queryClient = useQueryClient();
  const consulta = useQuery({ queryKey: ["admin-configuracoes"], queryFn: getConfiguracoes });
  const [rascunho, setRascunho] = useState<Rascunho>({});
  const [confirmar, setConfirmar] = useState<string[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [aviso, setAviso] = useState<string | null>(null);

  const salvar = useMutation({
    mutationFn: (confirmarPerigosos: boolean) => salvarConfiguracoes(rascunho, confirmarPerigosos),
    onSuccess: (dados) => {
      queryClient.setQueryData<ConfiguracoesAdmin>(["admin-configuracoes"], dados);
      setRascunho({});
      setConfirmar(null);
      setErro(null);
      setAviso(dados.alteradas.length ? `${dados.alteradas.length} configuração(ões) salva(s).` : "Nada mudou.");
    },
    onError: (e) => {
      const corpo = e instanceof ApiError ? (e.corpo as { confirmacao_necessaria?: string[] }) : null;
      if (e instanceof ApiError && e.status === 409 && corpo?.confirmacao_necessaria) {
        setConfirmar(corpo.confirmacao_necessaria);
        return;
      }
      setConfirmar(null);
      setErro(e instanceof ApiError ? e.message : "Falha ao salvar.");
    },
  });

  const reiniciar = useMutation({
    mutationFn: reiniciarWorkers,
    onSuccess: () => {
      setAviso("Reiniciando os workers… a página volta a responder em alguns segundos.");
      window.setTimeout(() => queryClient.invalidateQueries(), 8000);
    },
    onError: (e) => setErro(e instanceof ApiError ? e.message : "Falha ao reiniciar."),
  });

  if (consulta.isPending) {
    return (
      <p className="flex items-center gap-2 text-sm text-muted-foreground">
        <LoaderCircle className="size-4 animate-spin" /> Carregando configuração…
      </p>
    );
  }
  if (consulta.isError || !consulta.data) return <Erro texto="Não consegui carregar a configuração." />;

  const dados = consulta.data;
  const rotulos = Object.fromEntries(dados.grupos.flatMap((g) => g.campos).map((c) => [c.chave, c.rotulo]));
  const alteracoes = Object.keys(rascunho).length;

  return (
    <div className="flex flex-col gap-6">
      {dados.pendente_reinicio && (
        <div className="flex flex-wrap items-center gap-3 rounded-md border border-border bg-muted px-3 py-2 text-sm">
          <AlertTriangle className="size-4 shrink-0" />
          <span className="flex-1">Há configuração salva que ainda não está em uso — vale depois de reiniciar.</span>
          {dados.reinicio_disponivel ? (
            <Button type="button" size="sm" variant="outline" disabled={reiniciar.isPending} onClick={() => reiniciar.mutate()}>
              <RotateCw className="size-4" /> Reiniciar agora
            </Button>
          ) : (
            <span className="text-muted-foreground">Reinicie o processo/pod.</span>
          )}
        </div>
      )}
      {!dados.redis_configurado && (
        <p className="text-sm text-muted-foreground">
          REDIS_URL não está definida no ambiente — envio automático e rastreio não podem ser ligados.
        </p>
      )}

      {dados.grupos.map((grupo) => (
        <fieldset key={grupo.chave} className="rounded-md border border-border p-4">
          <legend className="px-1 text-sm font-semibold">{grupo.rotulo}</legend>
          <div className="grid gap-4 sm:grid-cols-2">
            {grupo.campos.map((campo) => (
              <CampoInput key={campo.chave} campo={campo} rascunho={rascunho} setRascunho={setRascunho} />
            ))}
          </div>
        </fieldset>
      ))}

      {erro && <Erro texto={erro} />}
      {aviso && !erro && <p className="text-sm text-muted-foreground">{aviso}</p>}

      <div className="sticky bottom-0 flex justify-end gap-2 border-t border-border bg-background py-3">
        <Button type="button" variant="outline" disabled={!alteracoes || salvar.isPending} onClick={() => setRascunho({})}>
          Descartar
        </Button>
        <Button type="button" disabled={!alteracoes || salvar.isPending} onClick={() => salvar.mutate(false)}>
          {salvar.isPending && <LoaderCircle className="size-4 animate-spin" />}
          Salvar {alteracoes > 0 && `(${alteracoes})`}
        </Button>
      </div>

      <Dialog open={confirmar !== null} onOpenChange={(aberto) => !aberto && setConfirmar(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirmar mudança sensível</DialogTitle>
            <DialogDescription>
              Estas configurações mudam o que a integração faz sozinha (incluindo escrita no CISS e
              leitura destrutiva do Fusion). A mudança é registrada e avisada no Discord, e vale a
              partir do próximo reinício.
            </DialogDescription>
          </DialogHeader>
          <ul className="list-disc pl-5 text-sm">
            {(confirmar ?? []).map((chave) => (
              <li key={chave}>
                {rotulos[chave] ?? chave} <span className="font-mono text-xs text-muted-foreground">{chave}</span>
              </li>
            ))}
          </ul>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setConfirmar(null)}>
              Cancelar
            </Button>
            <Button type="button" variant="destructive" disabled={salvar.isPending} onClick={() => salvar.mutate(true)}>
              Confirmar e salvar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function UsuariosPainel({ eu }: { eu: string }) {
  const queryClient = useQueryClient();
  const consulta = useQuery({ queryKey: ["usuarios"], queryFn: getUsuarios });
  const [novo, setNovo] = useState({ username: "", ciss_code: "", name: "" });
  const [erro, setErro] = useState<string | null>(null);
  const recarregar = () => queryClient.invalidateQueries({ queryKey: ["usuarios"] });

  const conceder = useMutation({
    mutationFn: () => concederUsuario(novo),
    onSuccess: () => {
      setNovo({ username: "", ciss_code: "", name: "" });
      setErro(null);
      recarregar();
    },
    onError: (e) => setErro(e instanceof ApiError ? e.message : "Falha ao conceder."),
  });
  const alterar = useMutation({
    mutationFn: ({ username, mudanca }: { username: string; mudanca: { active?: boolean; admin?: boolean } }) =>
      alterarUsuario(username, mudanca),
    onSuccess: () => {
      setErro(null);
      recarregar();
    },
    onError: (e) => setErro(e instanceof ApiError ? e.message : "Falha ao alterar."),
  });

  return (
    <div className="flex flex-col gap-4">
      <form
        className="grid gap-3 sm:grid-cols-[1fr_1fr_1.5fr_auto] sm:items-end"
        onSubmit={(e) => {
          e.preventDefault();
          conceder.mutate();
        }}
      >
        <div className="space-y-1">
          <Label htmlFor="novo-username">Usuário do CISS</Label>
          <Input id="novo-username" value={novo.username} onChange={(e) => setNovo({ ...novo, username: e.target.value })} required />
        </div>
        <div className="space-y-1">
          <Label htmlFor="novo-codigo">Código CISS</Label>
          <Input id="novo-codigo" value={novo.ciss_code} onChange={(e) => setNovo({ ...novo, ciss_code: e.target.value })} required />
        </div>
        <div className="space-y-1">
          <Label htmlFor="novo-nome">Nome</Label>
          <Input id="novo-nome" value={novo.name} onChange={(e) => setNovo({ ...novo, name: e.target.value })} required />
        </div>
        <Button type="submit" disabled={conceder.isPending}>
          Conceder acesso
        </Button>
      </form>

      {erro && <Erro texto={erro} />}

      {consulta.isPending ? (
        <p className="text-sm text-muted-foreground">Carregando…</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="text-left text-muted-foreground">
              <tr>
                <th className="py-2 pr-3 font-medium">Usuário</th>
                <th className="py-2 pr-3 font-medium">Código</th>
                <th className="py-2 pr-3 font-medium">Nome</th>
                <th className="py-2 pr-3 font-medium">Ativo</th>
                <th className="py-2 pr-3 font-medium">Admin</th>
              </tr>
            </thead>
            <tbody>
              {(consulta.data?.usuarios ?? []).map((u) => (
                <tr key={u.username} className="border-t border-border">
                  <td className="py-2 pr-3 font-mono">
                    {u.username}
                    {u.username === eu && <span className="ml-2 text-xs text-muted-foreground">(você)</span>}
                  </td>
                  <td className="py-2 pr-3">{u.ciss_code ?? "—"}</td>
                  <td className="py-2 pr-3">{u.name}</td>
                  <td className="py-2 pr-3">
                    <input
                      type="checkbox"
                      aria-label={`Ativo: ${u.username}`}
                      checked={u.active}
                      disabled={alterar.isPending}
                      onChange={(e) => alterar.mutate({ username: u.username, mudanca: { active: e.target.checked } })}
                    />
                  </td>
                  <td className="py-2 pr-3">
                    <input
                      type="checkbox"
                      aria-label={`Admin: ${u.username}`}
                      checked={u.admin}
                      disabled={alterar.isPending}
                      onChange={(e) => alterar.mutate({ username: u.username, mudanca: { admin: e.target.checked } })}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function AdministracaoSection({ eu }: { eu: string }) {
  const [parte, setParte] = useState<"configuracao" | "usuarios">("configuracao");
  return (
    <div className="flex flex-col gap-4">
      <div className="flex gap-2">
        <Button type="button" size="sm" variant={parte === "configuracao" ? "default" : "outline"} onClick={() => setParte("configuracao")}>
          Configuração
        </Button>
        <Button type="button" size="sm" variant={parte === "usuarios" ? "default" : "outline"} onClick={() => setParte("usuarios")}>
          Usuários
        </Button>
      </div>
      {parte === "configuracao" ? <ConfiguracaoPainel /> : <UsuariosPainel eu={eu} />}
    </div>
  );
}
