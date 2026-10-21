// Login direto — único caminho de entrada deste app desde que Cargas
// Fusion saiu do Toolkit do meta-natal (não há mais sessão de lá pra
// trocar silenciosamente). O token fica em localStorage (ver api.ts) pra
// sobreviver a reload — no carregamento, "restaurando" confere esse token
// contra /api/me antes de assumir que a sessão ainda vale.

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  getMe,
  getToken,
  login as loginRequest,
  logout as logoutRequest,
  setToken,
} from "@/api";

type UsuarioFusionService = { ciss_code: string | null; name: string };

type EstadoAuth =
  | { status: "restaurando" }
  | { status: "precisa-login"; mensagem: string | null }
  | { status: "ready"; user: UsuarioFusionService };

export function useFusionServiceAuth() {
  const [estado, setEstado] = useState<EstadoAuth>(
    getToken() ? { status: "restaurando" } : { status: "precisa-login", mensagem: null }
  );

  useEffect(() => {
    if (estado.status !== "restaurando") return;
    getMe()
      .then((resultado) => setEstado({ status: "ready", user: resultado.user }))
      .catch(() => {
        // Token expirado, inválido, ou usuário desativado na allow-list —
        // mesmo efeito de nunca ter logado, sem mensagem de erro (não foi
        // uma tentativa de login que falhou).
        setToken(null);
        setEstado({ status: "precisa-login", mensagem: null });
      });
  }, [estado.status]);

  const loginDireto = useCallback(async (username: string, password: string) => {
    try {
      const resultado = await loginRequest(username, password);
      setToken(resultado.token);
      setEstado({ status: "ready", user: resultado.user });
    } catch (erro) {
      const mensagem = erro instanceof ApiError ? erro.message : "Serviço indisponível. Tente novamente.";
      setEstado({ status: "precisa-login", mensagem });
      throw erro;
    }
  }, []);

  const sair = useCallback(async () => {
    try {
      await logoutRequest();
    } catch {
      // Mesmo que a chamada falhe (token já expirado, serviço fora do
      // ar), a sessão local é descartada — logout é sempre local primeiro.
    }
    setToken(null);
    setEstado({ status: "precisa-login", mensagem: null });
  }, []);

  return { ...estado, loginDireto, sair };
}
