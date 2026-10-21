import { LoaderCircle, Search } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useBuscarVeiculos } from "@/tools/cargas_fusion/useCadastroFusion";
import type { VeiculoFusion } from "@/types";

interface Props {
  id: string;
  /** O veículo sugerido automaticamente (casamento por PLACATRANSP do
   * CISS) — `null` quando não há sugestão, ou a tela não tem uma (pré-carga). */
  sugestao: VeiculoFusion | null;
  /** A escolha manual, se houver — sobrepõe a sugestão. */
  escolhido: VeiculoFusion | null;
  onEscolher: (veiculo: VeiculoFusion | null) => void;
}

/** "NWY2177 - CAMINHAO 710 BRANCO" — placa primeiro, é o que identifica o
 * veículo pra quem está montando o envio; codigo_erp não aparece, ninguém
 * aqui precisa dele pra escolher. */
function rotuloVeiculo(v: VeiculoFusion): string {
  return v.descricao ? `${v.placa} - ${v.descricao}` : v.placa;
}

/** Veículo não tem cadastro local — a escolha manual busca ao vivo no
 * portal do Fusion, por placa ou descrição. */
export function VeiculoPickerFusion({ id, sugestao, escolhido, onEscolher }: Props) {
  const [termo, setTermo] = useState("");
  const buscar = useBuscarVeiculos();
  const atual = escolhido ?? sugestao;

  return (
    <div className="flex flex-col gap-1">
      <Label htmlFor={id}>Veículo</Label>
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
          placeholder="Buscar por placa ou descrição no Fusion"
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
          ? `Selecionado: ${rotuloVeiculo(atual)}${!escolhido && sugestao ? " · sugerido pelo CISS" : ""}`
          : "Nenhum veículo selecionado."}
      </p>

      {buscar.isSuccess && (
        <ul className="flex flex-col gap-1 rounded-md border border-border p-2">
          {buscar.data.length === 0 && (
            <li className="text-sm text-muted-foreground">Nenhum veículo encontrado no Fusion.</li>
          )}
          {buscar.data.map((v) => (
            <li key={v.codigo_erp} className="flex items-center justify-between text-sm">
              <span>{rotuloVeiculo(v)}</span>
              <Button type="button" variant="ghost" size="sm" onClick={() => onEscolher(v)}>
                Usar
              </Button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
