// Única porta de saída deste app — monolito, uma tool só, então não há
// razão para separar "api genérica" de "api da tool" como meta-natal fazia.
// Sessão própria (não cookie): token de `Authorization: Bearer`, persistido
// em localStorage pra sobreviver a reload de aba — a validade de ~12h é do
// próprio token (auth/tokens.py), não de uma sessão no servidor, então
// guardar e reusar é seguro. `useFusionServiceAuth` confere contra `/api/me`
// no carregamento, pra não entrar "logado" com um token expirado ou de
// usuário desativado na allow-list.

import type {
  AjudanteFusion,
  CandidatoPessoaCiss,
  CargaResumo,
  EmpresaEstoque,
  ItemFilaFusion,
  ItemFilaPreCargaFusion,
  MotoristaFusion,
  PreCargaResumo,
  PreviewCargaFusion,
  PreviewPreCargaFusion,
  ResultadoEnvioCargaFusion,
  SelecaoEnvioCargaFusion,
  SelecaoEnvioPreCargaFusion,
  VeiculoFusion,
} from "@/types";

export class ApiError extends Error {
  status: number;
  corpo: unknown;

  constructor(status: number, message: string, corpo: unknown) {
    super(message);
    this.status = status;
    this.corpo = corpo;
  }
}

const TOKEN_STORAGE_KEY = "fusion_service_token";

function lerTokenPersistido(): string | null {
  try {
    return localStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    // Storage bloqueado (aba privada, política do navegador) — sessão
    // volta a durar só a aba, como era antes.
    return null;
  }
}

let token: string | null = lerTokenPersistido();

export function getToken(): string | null {
  return token;
}

export function setToken(novoToken: string | null): void {
  token = novoToken;
  try {
    if (novoToken) localStorage.setItem(TOKEN_STORAGE_KEY, novoToken);
    else localStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // Mesmo bloqueio acima — a sessão em memória ainda funciona.
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string> | undefined),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  const response = await fetch(path, { ...init, headers });

  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new ApiError(response.status, body.error ?? "Erro inesperado.", body);
  }

  if (response.status === 204) {
    return undefined as T;
  }

  return response.json() as Promise<T>;
}

// --- Autenticação --------------------------------------------------------

export function login(
  username: string,
  password: string
): Promise<{ token: string; user: { ciss_code: string | null; name: string } }> {
  return request("/api/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
}

/** Confere se o token guardado ainda é válido (não expirou, não foi
 * desativado na allow-list) e devolve quem está logado — usado só no
 * carregamento da página, pra restaurar a sessão sem pedir login de novo. */
export function getMe(): Promise<{ user: { ciss_code: string | null; name: string } }> {
  return request("/api/me");
}

export function logout(): Promise<void> {
  return request("/api/logout", { method: "POST" });
}

// --- Cargas ------------------------------------------------------------

export function getCargasFusion(
  dataInicial: string,
  dataFinal: string,
  limite = 100,
  idEmpresa?: number | null,
  // false mostra também carga já enviada (de verdade ou marcada
  // manualmente) — omitido/true esconde, o padrão do backend.
  ocultarEnviadas = true,
  // false também mostra cargas de local de retirada sem a integração
  // Fusion marcada (ex. venda balcão/"FR CAIXA") — omitido/true esconde,
  // o padrão do backend (LOCAL_RETIRADA.FLAGUTILIZAINTEGRACAOFUSION).
  apenasFusion = true
): Promise<{ cargas: CargaResumo[] }> {
  const parametros = new URLSearchParams({
    data_inicial: dataInicial,
    data_final: dataFinal,
    limite: String(limite),
    ocultar_enviadas: ocultarEnviadas ? "1" : "0",
    apenas_fusion: apenasFusion ? "1" : "0",
  });
  if (idEmpresa != null) parametros.set("id_empresa", String(idEmpresa));
  return request(`/api/cargas?${parametros}`);
}

export function getEmpresasCargasFusion(): Promise<{ empresas: EmpresaEstoque[] }> {
  return request("/api/empresas");
}

/** Acha a carga a partir do código ("1_22144395"), do pedido (IDORCAMENTO,
 * ex. "616139") ou da NF ("166105-2") — qualquer um dos três. */
export function buscarCargaFusionPorDocumento(
  termo: string
): Promise<{ id_empresa: number; id_planilha: number }> {
  return request(`/api/cargas/busca-documento?termo=${encodeURIComponent(termo)}`);
}

export function getCargaFusionPreview(
  idEmpresa: number,
  idPlanilha: number,
  selecao: SelecaoEnvioCargaFusion = {}
): Promise<PreviewCargaFusion> {
  const parametros = new URLSearchParams();
  if (selecao.motoristaCodigoErp != null) parametros.set("motorista_codigo_erp", selecao.motoristaCodigoErp);
  if (selecao.veiculoCodigoErp != null) parametros.set("veiculo_codigo_erp", selecao.veiculoCodigoErp);
  if (selecao.itens != null) parametros.set("itens", selecao.itens.join(","));
  const query = parametros.toString();
  return request(`/api/cargas/${idEmpresa}/${idPlanilha}${query ? `?${query}` : ""}`);
}

export function enviarCargaFusion(
  idEmpresa: number,
  idPlanilha: number,
  selecao: SelecaoEnvioCargaFusion = {},
  // true reenvia mesmo com a carga já marcada como enviada — sem isso o
  // backend recusa sem chamar o Fusion de novo.
  forcar = false
): Promise<ResultadoEnvioCargaFusion> {
  return request(`/api/cargas/${idEmpresa}/${idPlanilha}/enviar`, {
    method: "POST",
    body: JSON.stringify({
      motorista_codigo_erp: selecao.motoristaCodigoErp ?? undefined,
      veiculo_codigo_erp: selecao.veiculoCodigoErp ?? undefined,
      itens: selecao.itens ?? undefined,
      forcar: forcar || undefined,
    }),
  });
}

export function marcarCargaFusionIntegrada(
  idEmpresa: number,
  idPlanilha: number
): Promise<{ fila: ItemFilaFusion }> {
  return request(`/api/cargas/${idEmpresa}/${idPlanilha}/marcar-integrada`, { method: "POST" });
}

export function getFilaCargasFusion(todas = false): Promise<{ fila: ItemFilaFusion[] }> {
  return request(`/api/fila${todas ? "?todas=1" : ""}`);
}

export function removerDaFilaCargasFusion(id: number): Promise<void> {
  return request(`/api/fila/${id}`, { method: "DELETE" });
}

// --- Pré-cargas ----------------------------------------------------------

export function getPreCargasFusion(
  dataInicial: string,
  dataFinal: string,
  limite = 100,
  idEmpresa?: number | null,
  ocultarEnviadas = true,
  apenasFusion = true
): Promise<{ pre_cargas: PreCargaResumo[] }> {
  const parametros = new URLSearchParams({
    data_inicial: dataInicial,
    data_final: dataFinal,
    limite: String(limite),
    ocultar_enviadas: ocultarEnviadas ? "1" : "0",
    apenas_fusion: apenasFusion ? "1" : "0",
  });
  if (idEmpresa != null) parametros.set("id_empresa", String(idEmpresa));
  return request(`/api/pre-cargas?${parametros}`);
}

export function getPreCargaFusionPreview(
  idEmpresa: number,
  idPrecarga: number,
  selecao: SelecaoEnvioPreCargaFusion = {}
): Promise<PreviewPreCargaFusion> {
  const parametros = new URLSearchParams();
  if (selecao.motoristaCodigoErp != null) parametros.set("motorista_codigo_erp", selecao.motoristaCodigoErp);
  if (selecao.veiculoCodigoErp != null) parametros.set("veiculo_codigo_erp", selecao.veiculoCodigoErp);
  if (selecao.itens != null) parametros.set("itens", selecao.itens.join(","));
  const query = parametros.toString();
  return request(`/api/pre-cargas/${idEmpresa}/${idPrecarga}${query ? `?${query}` : ""}`);
}

export function enviarPreCargaFusion(
  idEmpresa: number,
  idPrecarga: number,
  selecao: SelecaoEnvioPreCargaFusion = {},
  forcar = false
): Promise<ResultadoEnvioCargaFusion> {
  return request(`/api/pre-cargas/${idEmpresa}/${idPrecarga}/enviar`, {
    method: "POST",
    body: JSON.stringify({
      motorista_codigo_erp: selecao.motoristaCodigoErp ?? undefined,
      veiculo_codigo_erp: selecao.veiculoCodigoErp ?? undefined,
      itens: selecao.itens ?? undefined,
      forcar: forcar || undefined,
    }),
  });
}

export function getFilaPreCargasFusion(todas = false): Promise<{ fila: ItemFilaPreCargaFusion[] }> {
  return request(`/api/pre-cargas/fila${todas ? "?todas=1" : ""}`);
}

export function removerDaFilaPreCargasFusion(id: number): Promise<void> {
  return request(`/api/pre-cargas/fila/${id}`, { method: "DELETE" });
}

// --- Veículo e motorista: ao vivo no Fusion, sem cadastro local ------------

/** Busca ao vivo no portal do Fusion, por placa ou descrição — veículo não
 * tem cadastro local. `termo` vazio devolve lista vazia. */
export function getVeiculosFusion(termo = ""): Promise<{ veiculos: VeiculoFusion[] }> {
  return request(`/api/veiculos?termo=${encodeURIComponent(termo)}`);
}

/** Busca ao vivo no portal do Fusion, por nome — motorista não tem
 * cadastro local. `termo` vazio devolve lista vazia. */
export function getMotoristasFusion(termo = ""): Promise<{ motoristas: MotoristaFusion[] }> {
  return request(`/api/motoristas?termo=${encodeURIComponent(termo)}`);
}

// --- Ajudantes ---------------------------------------------------------

export function getAjudantesFusion(todos = false): Promise<{ ajudantes: AjudanteFusion[] }> {
  return request(`/api/ajudantes${todos ? "?todos=1" : ""}`);
}

export function criarAjudanteFusion(
  dados: Partial<AjudanteFusion>
): Promise<{ ajudante: AjudanteFusion }> {
  return request("/api/ajudantes", { method: "POST", body: JSON.stringify(dados) });
}

export function atualizarAjudanteFusion(
  id: number,
  dados: Partial<AjudanteFusion>
): Promise<{ ajudante: AjudanteFusion }> {
  return request(`/api/ajudantes/${id}`, { method: "PUT", body: JSON.stringify(dados) });
}

export function getCandidatosAjudanteFusion(
  termo: string
): Promise<{ candidatos: CandidatoPessoaCiss[] }> {
  return request(`/api/ajudantes/candidatos?termo=${encodeURIComponent(termo)}`);
}

export function sincronizarAjudanteFusion(id: number): Promise<{ ajudante: AjudanteFusion }> {
  return request(`/api/ajudantes/${id}/sincronizar`, { method: "POST" });
}
