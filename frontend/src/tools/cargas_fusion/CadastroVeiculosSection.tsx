import { LoaderCircle, Plus, RefreshCw, Search } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { ApiError } from "@/api";
import {
  useCandidatosVeiculo,
  useCriarVeiculo,
  useSincronizarVeiculo,
  useVeiculos,
} from "@/tools/cargas_fusion/useCadastroFusion";
import type { CandidatoVeiculoCiss, VeiculoFusion } from "@/types";

function mensagemDeErro(erro: unknown): string | null {
  return erro instanceof ApiError ? erro.message : erro ? "Erro inesperado." : null;
}

const FORM_VAZIO = {
  placa: "",
  codigo_erp: "",
  descricao: "",
  modelo: "",
  ano_modelo: "",
  ano_fabricacao: "",
  peso_max_entregas: "",
  volume_max_entregas: "",
  qtd_pallets_veiculo: "",
};

export function CadastroVeiculosSection() {
  const [termo, setTermo] = useState("");
  const [form, setForm] = useState(FORM_VAZIO);

  const veiculos = useVeiculos();
  const candidatos = useCandidatosVeiculo();
  const criar = useCriarVeiculo();
  const sincronizar = useSincronizarVeiculo();

  const erro =
    mensagemDeErro(veiculos.error) ??
    mensagemDeErro(candidatos.error) ??
    mensagemDeErro(criar.error) ??
    mensagemDeErro(sincronizar.error);

  function usarCandidato(c: CandidatoVeiculoCiss) {
    setForm({
      placa: c.PLACA,
      codigo_erp: String(c.IDVEICULO),
      descricao: c.DESCRVEICULO,
      modelo: c.MODELO ?? "",
      ano_modelo: c.ANOMODELO ?? "",
      ano_fabricacao: c.ANOFABRICACAO ?? "",
      peso_max_entregas: String(c.PESOMAXIMO ?? ""),
      volume_max_entregas: String(c.VOLUME ?? ""),
      qtd_pallets_veiculo: "",
    });
  }

  function salvar() {
    if (!form.placa.trim() || !form.codigo_erp.trim()) return;
    criar.mutate(
      {
        placa: form.placa,
        codigo_erp: form.codigo_erp,
        descricao: form.descricao || undefined,
        modelo: form.modelo || undefined,
        ano_modelo: form.ano_modelo ? Number(form.ano_modelo) : undefined,
        ano_fabricacao: form.ano_fabricacao ? Number(form.ano_fabricacao) : undefined,
        peso_max_entregas: form.peso_max_entregas ? Number(form.peso_max_entregas) : undefined,
        volume_max_entregas: form.volume_max_entregas ? Number(form.volume_max_entregas) : undefined,
        qtd_pallets_veiculo: form.qtd_pallets_veiculo ? Number(form.qtd_pallets_veiculo) : undefined,
      },
      { onSuccess: () => setForm(FORM_VAZIO) }
    );
  }

  return (
    <div className="flex flex-col gap-6">
      {erro && (
        <p className="rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text">
          {erro}
        </p>
      )}

      <div className="flex flex-col gap-2">
        <Label htmlFor="busca-veiculo-ciss">Buscar veículo no CISS (opcional, só para preencher o formulário)</Label>
        <div className="flex gap-2">
          <Input
            id="busca-veiculo-ciss"
            value={termo}
            onChange={(e) => setTermo(e.target.value)}
            placeholder="placa ou descrição"
            className="max-w-xs"
          />
          <Button type="button" variant="secondary" onClick={() => candidatos.mutate(termo)} disabled={!termo.trim()}>
            {candidatos.isPending ? <LoaderCircle className="animate-spin" /> : <Search />}
            Buscar
          </Button>
        </div>
        {candidatos.isSuccess && (
          <ul className="flex flex-col gap-1 rounded-md border border-border p-2">
            {candidatos.data.length === 0 && (
              <li className="text-sm text-muted-foreground">Nenhum veículo encontrado.</li>
            )}
            {candidatos.data.map((c) => (
              <li key={c.IDVEICULO} className="flex items-center justify-between text-sm">
                <span>
                  {c.DESCRVEICULO} — {c.PLACA}
                </span>
                <Button type="button" variant="ghost" size="sm" onClick={() => usarCandidato(c)}>
                  Usar
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="grid grid-cols-2 gap-3 rounded-md border border-border p-4 sm:grid-cols-4">
        <Campo label="Placa *" value={form.placa} onChange={(v) => setForm({ ...form, placa: v })} />
        <Campo label="codigo_erp *" value={form.codigo_erp} onChange={(v) => setForm({ ...form, codigo_erp: v })} />
        <Campo label="Descrição" value={form.descricao} onChange={(v) => setForm({ ...form, descricao: v })} />
        <Campo label="Modelo" value={form.modelo} onChange={(v) => setForm({ ...form, modelo: v })} />
        <Campo label="Ano modelo" value={form.ano_modelo} onChange={(v) => setForm({ ...form, ano_modelo: v })} />
        <Campo label="Ano fabricação" value={form.ano_fabricacao} onChange={(v) => setForm({ ...form, ano_fabricacao: v })} />
        <Campo label="Peso máx. (kg)" value={form.peso_max_entregas} onChange={(v) => setForm({ ...form, peso_max_entregas: v })} />
        <Campo label="Volume máx." value={form.volume_max_entregas} onChange={(v) => setForm({ ...form, volume_max_entregas: v })} />
        <div className="col-span-2 flex items-end sm:col-span-4">
          <Button type="button" onClick={salvar} disabled={criar.isPending}>
            {criar.isPending ? <LoaderCircle className="animate-spin" /> : <Plus />}
            Cadastrar / atualizar
          </Button>
        </div>
      </div>

      {veiculos.isPending ? (
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <LoaderCircle className="animate-spin size-4" /> Carregando...
        </p>
      ) : (
        <ListaVeiculos veiculos={veiculos.data ?? []} onSincronizar={(id) => sincronizar.mutate(id)} sincronizando={sincronizar.isPending} />
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

function ListaVeiculos({
  veiculos,
  onSincronizar,
  sincronizando,
}: {
  veiculos: VeiculoFusion[];
  onSincronizar: (id: number) => void;
  sincronizando: boolean;
}) {
  if (veiculos.length === 0) {
    return <p className="text-sm text-muted-foreground">Nenhum veículo cadastrado ainda.</p>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[32rem] text-sm">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground">
            <th scope="col" className="py-2 pr-3 font-medium">Placa</th>
            <th scope="col" className="py-2 pr-3 font-medium">codigo_erp</th>
            <th scope="col" className="py-2 pr-3 font-medium">Descrição</th>
            <th scope="col" className="py-2 pr-3 font-medium">
              <span className="sr-only">Ações</span>
            </th>
          </tr>
        </thead>
        <tbody>
          {veiculos.map((v) => (
            <tr key={v.id} className="border-b border-border/50">
              <td className="py-2 pr-3 whitespace-nowrap">{v.placa}</td>
              <td className="py-2 pr-3 whitespace-nowrap">{v.codigo_erp}</td>
              <td className="py-2 pr-3">{v.descricao}</td>
              <td className="py-2 pr-3 whitespace-nowrap">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  disabled={sincronizando}
                  onClick={() => onSincronizar(v.id)}
                  title="Consulta o Fusion pela placa; se existir, usa o dado de lá, senão envia este cadastro"
                >
                  <RefreshCw /> Sincronizar
                </Button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
