import { LoaderCircle, Search } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useBuscarMotoristas } from "@/tools/cargas_fusion/useCadastroFusion";
import type { MotoristaFusion } from "@/types";

interface Props {
  id: string;
  /** O motorista sugerido automaticamente (casamento por IDMOTORISTA do
   * CISS) — `null` quando não há sugestão, ou a tela não tem uma (pré-carga). */
  sugestao: MotoristaFusion | null;
  /** A escolha manual, se houver — sobrepõe a sugestão. */
  escolhido: MotoristaFusion | null;
  onEscolher: (motorista: MotoristaFusion | null) => void;
}

/** Motorista não tem cadastro local (2026-10-20) — a escolha manual busca
 * ao vivo no portal do Fusion, por nome. */
export function MotoristaPickerFusion({ id, sugestao, escolhido, onEscolher }: Props) {
  const [termo, setTermo] = useState("");
  const buscar = useBuscarMotoristas();
  const atual = escolhido ?? sugestao;

  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>Motorista</Label>
      <div className="flex gap-2">
        <Input
          id={id}
          value={termo}
          onChange={(e) => setTermo(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && termo.trim()) {
              e.preventDefault();
              buscar.mutate(termo);
            }
          }}
          placeholder="Buscar por nome no Fusion"
          className="max-w-xs"
        />
        <Button
          type="button"
          variant="secondary"
          size="sm"
          onClick={() => buscar.mutate(termo)}
          disabled={!termo.trim()}
        >
          {buscar.isPending ? <LoaderCircle className="animate-spin" /> : <Search />}
          Buscar
        </Button>
        {escolhido && (
          <Button type="button" variant="ghost" size="sm" onClick={() => onEscolher(null)}>
            {sugestao ? "Usar sugestão" : "Limpar"}
          </Button>
        )}
      </div>

      <p className="text-sm text-muted-foreground">
        {atual
          ? `Selecionado: ${atual.nome} (${atual.codigo_erp})${!escolhido && sugestao ? " · sugerido pelo CISS" : ""}`
          : "Nenhum motorista selecionado."}
      </p>

      {buscar.isSuccess && (
        <ul className="flex flex-col gap-1 rounded-md border border-border p-2">
          {buscar.data.length === 0 && (
            <li className="text-sm text-muted-foreground">Ninguém encontrado no Fusion.</li>
          )}
          {buscar.data.map((m) => (
            <li key={m.codigo_erp} className="flex items-center justify-between text-sm">
              <span>
                {m.nome} ({m.codigo_erp})
              </span>
              <Button type="button" variant="ghost" size="sm" onClick={() => onEscolher(m)}>
                Usar
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
