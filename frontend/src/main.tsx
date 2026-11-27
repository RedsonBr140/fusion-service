import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import { App } from "@/App";
import "@/index.css";
import { aplicarTema } from "@/theme";

// Antes do primeiro render, pra não piscar claro num sistema escuro.
aplicarTema();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
