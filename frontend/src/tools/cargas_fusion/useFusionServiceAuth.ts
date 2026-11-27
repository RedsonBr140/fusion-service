// Login direto — único caminho de entrada deste app desde que Cargas
// Fusion saiu do Toolkit do meta-natal (não há mais sessão de lá pra
// trocar silenciosamente). O token fica em localStorage (ver api.ts) pra
// sobreviver a reload — no carregamento, "restaurando" confere esse token
// contra /api/me antes de assumir que a sessão ainda vale.
//
// Sem token, confere antes se o setup inicial está aberto (nenhum admin —
// instalação nova ou banco de antes dos admins). Aberto, a tela mostra o
// login normal e um caminho pro setup (`irParaSetup`); instalação nova sem
// ninguém na allow-list vai direto pro setup.

import { useCallback, useEffect, useState } from "react";

import {
  ApiError,
  concluirSetup,
  getMe,
  getSetupStatus,
  getToken,
  login as loginRequest,
  logout as logoutRequest,
  setToken,
} from "@/api";
import type { UsuarioSessao } from "@/types";

type DadosSetup = Parameters<typeof concluirSetup>[0];

type EstadoAuth =
  | { status: "restaurando" }
  | { status: "precisa-login"; mensagem: string | null; setupDisponivel: boolean }
  | { status: "setup"; cissApiUrl: string | null }
  | { status: "ready"; user: UsuarioSessao };

export function useFusionServiceAuth() {
  const [estado, setEstado] = useState<EstadoAuth>({ status: "restaurando" });

  useEffect(() => {
    if (estado.status !== "restaurando") return;
    let cancelado = false;
    (async () => {
      if (getToken()) {
        try {
          const resultado = await getMe();
          if (!cancelado) setEstado({ status: "ready", user: resultado.user });
          return;
        } catch {
          // Token expirado, inválido, ou usuário desativado na allow-list —
          // mesmo efeito de nunca ter logado, sem mensagem de erro.
          setToken(null);
        }
      }
      let setup = { necessario: false, ciss_api_url: null as string | null };
      try {
        setup = await getSetupStatus();
      } catch {
        // Backend fora do ar: cai no login, que mostra o erro ao tentar.
      }
      if (cancelado) return;
      setEstado(
        setup.necessario && !setup.ciss_api_url
          ? { status: "setup", cissApiUrl: null }
          : { status: "precisa-login", mensagem: null, setupDisponivel: setup.necessario }
      );
    })();
    return () => {
      cancelado = true;
    };
  }, [estado.status]);

  const loginDireto = useCallback(async (username: string, password: string) => {
    try {
      const resultado = await loginRequest(username, password);
      setToken(resultado.token);
      setEstado({ status: "ready", user: resultado.user });
    } catch (erro) {
      const mensagem = erro instanceof ApiError ? erro.message : "Serviço indisponível. Tente novamente.";
      setEstado((anterior) => ({
        status: "precisa-login",
        mensagem,
        setupDisponivel: anterior.status === "precisa-login" && anterior.setupDisponivel,
      }));
      throw erro;
    }
  }, []);

  const irParaSetup = useCallback(async () => {
    const setup = await getSetupStatus().catch(() => ({ necessario: true, ciss_api_url: null }));
    setEstado({ status: "setup", cissApiUrl: setup.ciss_api_url });
  }, []);

  const voltarParaLogin = useCallback(() => {
    setEstado({ status: "precisa-login", mensagem: null, setupDisponivel: true });
  }, []);

  /** Lança `ApiError` em falha — o formulário mostra a mensagem. */
  const fazerSetup = useCallback(async (dados: DadosSetup) => {
    const resultado = await concluirSetup(dados);
    setToken(resultado.token);
    setEstado({ status: "ready", user: resultado.user });
  }, []);

  const sair = useCallback(async () => {
    try {
      await logoutRequest();
    } catch {
      // Mesmo que a chamada falhe (token já expirado, serviço fora do
      // ar), a sessão local é descartada — logout é sempre local primeiro.
    }
    setToken(null);
    setEstado({ status: "restaurando" });
  }, []);

  return { ...estado, loginDireto, sair, irParaSetup, voltarParaLogin, fazerSetup };
}
