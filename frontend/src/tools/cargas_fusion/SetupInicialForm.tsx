import { AlertCircle, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { ApiError } from "@/api";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const ERRO_ID = "setup-inicial-erro";

/** Primeiro admin da instalação. O token de uso único fica num arquivo no
 * volume do backend (nunca no log) — ver auth/setup.py. */
export function SetupInicialForm({
  cissApiUrlInicial,
  fazerSetup,
  voltarParaLogin,
}: {
  cissApiUrlInicial: string | null;
  fazerSetup: (dados: {
    token: string;
    ciss_api_url: string;
    ciss_ca_bundle?: string;
    username: string;
    password: string;
    name?: string;
  }) => Promise<void>;
  voltarParaLogin: () => void;
}) {
  const [token, setTokenSetup] = useState("");
  const [cissApiUrl, setCissApiUrl] = useState(cissApiUrlInicial ?? "");
  const [caBundle, setCaBundle] = useState("");
  const [nome, setNome] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [mensagem, setMensagem] = useState<string | null>(null);

  async function enviar(event: React.FormEvent) {
    event.preventDefault();
    setEnviando(true);
    setMensagem(null);
    try {
      await fazerSetup({
        token: token.trim(),
        ciss_api_url: cissApiUrl.trim(),
        ciss_ca_bundle: caBundle.trim() || undefined,
        username: username.trim(),
        password,
        name: nome.trim() || undefined,
      });
    } catch (erro) {
      setMensagem(erro instanceof ApiError ? erro.message : "Serviço indisponível. Tente novamente.");
    } finally {
      setEnviando(false);
    }
  }

  const descricao = mensagem ? ERRO_ID : undefined;

  return (
    <div className="mx-auto max-w-md py-8">
      <h2 className="mb-2 text-lg font-semibold">Setup inicial</h2>
      <p className="mb-6 text-sm text-muted-foreground">
        Esta instalação ainda não tem administrador. Quem concluir o setup vira o primeiro
        admin e configura o resto na aba Administração. O token está no volume do backend:
      </p>
      <pre className="mb-6 overflow-x-auto rounded-md bg-muted px-3 py-2 text-xs">
        kubectl exec deploy/fusion-service-backend -- cat /app/data/setup-token.txt
      </pre>

      <form className="space-y-4" onSubmit={enviar}>
        <div className="space-y-2">
          <Label htmlFor="setup-token">Token de setup</Label>
          <Input
            id="setup-token"
            autoComplete="off"
            autoFocus
            aria-describedby={descricao}
            value={token}
            onChange={(e) => setTokenSetup(e.target.value)}
            required
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="setup-ciss-url">URL da API do CISS</Label>
          <Input
            id="setup-ciss-url"
            type="url"
            placeholder="https://..."
            value={cissApiUrl}
            onChange={(e) => setCissApiUrl(e.target.value)}
            required
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="setup-ca">CA bundle (opcional)</Label>
          <Input
            id="setup-ca"
            placeholder="/caminho/no/pod/ca.pem"
            value={caBundle}
            onChange={(e) => setCaBundle(e.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="setup-nome">Seu nome (opcional)</Label>
          <Input id="setup-nome" value={nome} onChange={(e) => setNome(e.target.value)} />
        </div>

        <div className="space-y-2">
          <Label htmlFor="setup-username">Usuário do CISS</Label>
          <Input
            id="setup-username"
            autoComplete="username"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="setup-password">Senha do CISS</Label>
          <Input
            id="setup-password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </div>

        {mensagem && (
          <p
            id={ERRO_ID}
            role="alert"
            className="flex items-start gap-2 rounded-md bg-destructive/10 px-3 py-2.5 text-sm text-destructive-text"
          >
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            {mensagem}
          </p>
        )}

        <Button type="submit" className="w-full" disabled={enviando}>
          {enviando && <LoaderCircle className="size-4 animate-spin" />}
          {enviando ? "Concluindo…" : "Concluir setup"}
        </Button>
        {cissApiUrlInicial && (
          <Button type="button" variant="ghost" className="w-full" onClick={voltarParaLogin}>
            Voltar ao login
          </Button>
        )}
      </form>
    </div>
  );
}
