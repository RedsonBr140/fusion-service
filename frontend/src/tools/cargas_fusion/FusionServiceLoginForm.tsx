import { AlertCircle, LoaderCircle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const ERRO_ID = "fusion-service-login-erro";

export function FusionServiceLoginForm({
  loginDireto,
  mensagemInicial,
}: {
  loginDireto: (username: string, password: string) => Promise<void>;
  mensagemInicial: string | null;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [mensagem, setMensagem] = useState(mensagemInicial);

  async function enviar(event: React.FormEvent) {
    event.preventDefault();
    setEnviando(true);
    setMensagem(null);
    try {
      await loginDireto(username, password);
    } catch {
      setMensagem("Usuário ou senha inválidos.");
    } finally {
      setEnviando(false);
    }
  }

  return (
    <div className="mx-auto max-w-sm py-8">
      <p className="mb-6 text-sm text-muted-foreground">
        Entre com as mesmas credenciais do CISS.
      </p>

      <form className="space-y-4" onSubmit={enviar}>
        <div className="space-y-2">
          <Label htmlFor="fusion-username">Usuário</Label>
          <Input
            id="fusion-username"
            autoComplete="username"
            autoFocus
            aria-describedby={mensagem ? ERRO_ID : undefined}
            value={username}
            onChange={(event) => setUsername(event.target.value)}
          />
        </div>

        <div className="space-y-2">
          <Label htmlFor="fusion-password">Senha</Label>
          <Input
            id="fusion-password"
            type="password"
            autoComplete="current-password"
            aria-describedby={mensagem ? ERRO_ID : undefined}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
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
          {enviando ? "Entrando…" : "Entrar"}
        </Button>
      </form>
    </div>
  );
}
