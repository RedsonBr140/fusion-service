// Claro/escuro: segue o sistema por padrão, com troca manual guardada no
// navegador (só conveniência — sem storage, volta a seguir o sistema).

import { useCallback, useEffect, useState } from "react";

export type Tema = "sistema" | "claro" | "escuro";

const CHAVE = "fusion_service_tema";
const consulta = () => window.matchMedia("(prefers-color-scheme: dark)");

function lerTema(): Tema {
  try {
    const valor = localStorage.getItem(CHAVE);
    if (valor === "claro" || valor === "escuro" || valor === "sistema") return valor;
  } catch {
    // Storage bloqueado: segue o sistema.
  }
  return "sistema";
}

export function aplicarTema(tema: Tema = lerTema()): void {
  const escuro = tema === "escuro" || (tema === "sistema" && consulta().matches);
  document.documentElement.classList.toggle("dark", escuro);
}

export function useTema() {
  const [tema, setTemaEstado] = useState<Tema>(lerTema);

  useEffect(() => {
    aplicarTema(tema);
    if (tema !== "sistema") return;
    const mq = consulta();
    const ouvir = () => aplicarTema("sistema");
    mq.addEventListener("change", ouvir);
    return () => mq.removeEventListener("change", ouvir);
  }, [tema]);

  const setTema = useCallback((novo: Tema) => {
    try {
      localStorage.setItem(CHAVE, novo);
    } catch {
      // Vale só pra esta aba.
    }
    setTemaEstado(novo);
  }, []);

  return { tema, setTema };
}
