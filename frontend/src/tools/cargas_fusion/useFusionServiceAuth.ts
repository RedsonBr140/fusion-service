// Login direto — único caminho de entrada deste app desde que Cargas
// Fusion saiu do Toolkit do meta-natal (não há mais sessão de lá pra
// trocar silenciosamente).

import { useCallback, useState } from "react";

import { ApiError, login as loginRequest, setToken } from "@/api";

type UsuarioFusionService = { ciss_code: string | null; name: string };

type EstadoAuth =
  | { status: "precisa-login"; mensagem: string | null }
  | { status: "ready"; user: UsuarioFusionService };

export function useFusionServiceAuth() {
  const [estado, setEstado] = useState<EstadoAuth>({ status: "precisa-login", mensagem: null });

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

  return { ...estado, loginDireto };
}
