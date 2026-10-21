import type { UseMutationResult, UseQueryResult } from "@tanstack/react-query";
import { LoaderCircle, Plus, Search, Send } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/api";
import type { AjudanteFusion, CandidatoPessoaCiss } from "@/types";

function mensagemDeErro(erro: unknown): string | null {
  return erro instanceof ApiError ? erro.message : erro ? "Erro inesperado." : null;
}

// Motorista deixou de ter cadastro local (2026-10-20) — este componente
// genérico agora só serve ajudante, mas mantém a forma genérica (o nome
// "PessoaFusion" e o `<T>`) porque é dele que vem a distinção
// mostrarCamposExtras, ainda documentada abaixo pro caso de um novo papel
// de pessoa local aparecer.
type PessoaFusion = AjudanteFusion;

interface Props<T extends PessoaFusion> {
  tipo: string;
  listar: () => UseQueryResult<T[], unknown>;
  criar: () => UseMutationResult<unknown, unknown, Partial<T>>;
  atualizar: () => UseMutationResult<unknown, unknown, { id: number; dados: Partial<T> }>;
  candidatos: () => UseMutationResult<CandidatoPessoaCiss[], unknown, string>;
  sincronizar: () => UseMutationResult<unknown, unknown, number>;
  /** Motorista tem cpf/telefone/endereco/uf/cidade/cep; ajudante não. */
  mostrarCamposExtras: boolean;
}

const FORM_VAZIO = {
  idclifor_ciss: "",
  codigo_erp: "",
  nome: "",
  cpf: "",
  telefone: "",
  endereco: "",
  uf: "",
  cidade: "",
  cep: "",
};

export function CadastroPessoaSection<T extends PessoaFusion>({
  tipo,
  listar,
  criar,
  atualizar,
  candidatos,
  sincronizar,
  mostrarCamposExtras,
}: Props<T>) {
  const [termo, setTermo] = useState("");
  const [form, setForm] = useState(FORM_VAZIO);

  const lista = listar();
  const candidatosMut = candidatos();
  const criarMut = criar();
  const atualizarMut = atualizar();
  const sincronizarMut = sincronizar();

  const erro =
    mensagemDeErro(lista.error) ??
    mensagemDeErro(candidatosMut.error) ??
    mensagemDeErro(criarMut.error) ??
    mensagemDeErro(atualizarMut.error) ??
    mensagemDeErro(sincronizarMut.error);

  function usarCandidato(c: CandidatoPessoaCiss) {
    setForm({
      idclifor_ciss: String(c.IDCLIFOR),
      codigo_erp: String(c.IDCLIFOR),
      nome: c.NOME,
      cpf: c.CNPJCPF ?? "",
      telefone: c.TELEFONE ?? "",
      endereco: c.ENDERECO ?? "",
      uf: c.UF ?? "",
      cidade: c.DESCRCIDADE ?? "",
      cep: c.CEP ?? "",
    });
  }

  function salvar() {
    if (!form.codigo_erp.trim() || !form.nome.trim()) return;
    const dados: Partial<PessoaFusion> = {
      codigo_erp: form.codigo_erp,
      nome: form.nome,
      idclifor_ciss: form.idclifor_ciss ? Number(form.idclifor_ciss) : undefined,
    };
    if (mostrarCamposExtras) {
      Object.assign(dados, {
        cpf: form.cpf || undefined,
        telefone: form.telefone || undefined,
        endereco: form.endereco || undefined,
        uf: form.uf || undefined,
        cidade: form.cidade || undefined,
        cep: form.cep || undefined,
      });
    }
    criarMut.mutate(dados as Partial<T>, { onSuccess: () => setForm(FORM_VAZIO) });
  }

  return (
    <div className="flex flex-col gap-6">
      {erro && (
        <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
          {erro}
        </p>
      )}

      <div className="flex flex-col gap-2">
        <Label htmlFor={`busca-${tipo}-ciss`}>
          Buscar pessoa no CISS (opcional, só para preencher o formulário)
        </Label>
        <div className="flex gap-2">
          <Input
            id={`busca-${tipo}-ciss`}
            value={termo}
            onChange={(e) => setTermo(e.target.value)}
            placeholder="nome ou código"
            className="max-w-xs"
          />
          <Button
            type="button"
            variant="secondary"
            onClick={() => candidatosMut.mutate(termo)}
            disabled={!termo.trim()}
          >
            {candidatosMut.isPending ? <LoaderCircle className="animate-spin" /> : <Search />}
            Buscar
          </Button>
        </div>
        {candidatosMut.isSuccess && (
          <ul className="flex flex-col gap-1 rounded-md border border-border p-2">
            {candidatosMut.data.length === 0 && (
              <li className="text-sm text-muted-foreground">Ninguém encontrado.</li>
            )}
            {candidatosMut.data.map((c) => (
              <li key={c.IDCLIFOR} className="flex items-center justify-between text-sm">
                <span>{c.NOME}</span>
                <Button type="button" variant="ghost" size="sm" onClick={() => usarCandidato(c)}>
                  Usar
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 rounded-md border border-border p-4 sm:grid-cols-4">
        <Campo label="codigo_erp *" value={form.codigo_erp} onChange={(v) => setForm({ ...form, codigo_erp: v })} />
        <Campo label="Nome *" value={form.nome} onChange={(v) => setForm({ ...form, nome: v })} />
        {mostrarCamposExtras && (
          <>
            <Campo label="CPF" value={form.cpf} onChange={(v) => setForm({ ...form, cpf: v })} />
            <Campo label="Telefone" value={form.telefone} onChange={(v) => setForm({ ...form, telefone: v })} />
            <Campo label="Endereço" value={form.endereco} onChange={(v) => setForm({ ...form, endereco: v })} />
            <Campo label="UF" value={form.uf} onChange={(v) => setForm({ ...form, uf: v })} />
            <Campo label="Cidade" value={form.cidade} onChange={(v) => setForm({ ...form, cidade: v })} />
            <Campo label="CEP" value={form.cep} onChange={(v) => setForm({ ...form, cep: v })} />
          </>
        )}
        <div className="col-span-2 flex items-end sm:col-span-4">
          <Button type="button" onClick={salvar} disabled={criarMut.isPending}>
            {criarMut.isPending ? <LoaderCircle className="animate-spin" /> : <Plus />}
            Cadastrar / atualizar
          </Button>
        </div>
      </div>

      {lista.isPending ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="animate-spin size-4" /> Carregando...
        </p>
      ) : (
        <ListaPessoas
          pessoas={lista.data ?? []}
          tipo={tipo}
          onSincronizar={(id) => sincronizarMut.mutate(id)}
          sincronizando={sincronizarMut.isPending}
          onAlternarAtivo={(id, ativo) =>
            atualizarMut.mutate({ id, dados: { ativo } as Partial<T> })
          }
        />
      )}
    </div>
  );
}

function Campo({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex flex-col gap-1">
      <Label>{label}</Label>
      <Input value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}

function ListaPessoas({
  pessoas,
  tipo,
  onSincronizar,
  sincronizando,
  onAlternarAtivo,
}: {
  pessoas: PessoaFusion[];
  tipo: string;
  onSincronizar: (id: number) => void;
  sincronizando: boolean;
  onAlternarAtivo: (id: number, ativo: boolean) => void;
}) {
  if (pessoas.length === 0) {
    return <p className="text-sm text-muted-foreground">Nenhum(a) {tipo} cadastrado(a) ainda.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[28rem] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground">
            <th scope="col" className="py-2 pr-3 font-medium">Nome</th>
            <th scope="col" className="py-2 pr-3 font-medium">codigo_erp</th>
            <th scope="col" className="py-2 pr-3 text-center font-medium">Ativo</th>
            <th scope="col" className="py-2 pr-3 font-medium">
              <span className="sr-only">Ações</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {pessoas.map((p) => (
            <tr key={p.id} className="border-b border-border/50">
              <td className="py-2 pr-3">{p.nome}</td>
              <td className="py-2 pr-3 whitespace-nowrap">{p.codigo_erp}</td>
              <td className="py-2 pr-3 text-center">
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  checked={p.ativo}
                  onChange={() => onAlternarAtivo(p.id, !p.ativo)}
                />
              </td>
              <td className="py-2 pr-3 whitespace-nowrap">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={sincronizando}
                  onClick={() => onSincronizar(p.id)}
                  title="Envia este cadastro ao Fusion (sendMotoristas) — não há como ler de volta antes"
                >
                  <Send /> Enviar ao Fusion
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
