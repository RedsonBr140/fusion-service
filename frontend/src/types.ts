// Tipos desta tool, portados de meta-natal/frontend/src/types/index.ts
// (commit 0cea91e, antes da extração) — dado cru do CISS (chaves em
// maiúsculo, como o pyodbc devolve) e dado do cadastro local (snake_case,
// como o service.py monta) convivem aqui, cada um com a casing da sua
// fonte.

export interface EmpresaEstoque {
  id_empresa: number;
  nome: string;
}

export interface CargaResumo {
  IDEMPRESA: number;
  IDPLANILHA: number;
  UF: string | null;
  DESCRCIDADE: string | null;
  /** A transportadora, não quem dirigiu — na prática é quase sempre a
   * mesma em toda carga, não serve pra distinguir uma da outra na
   * listagem. Ver NOME_MOTORISTA. */
  NOMETRANSP: string | null;
  PLACATRANSP: string | null;
  IDMOTORISTA: number | null;
  /** CLIENTE_FORNECEDOR.NOME via IDMOTORISTA, junção direta no CISS — não
   * é o motorista lido ao vivo do Fusion (MotoristaFusion), só o nome
   * pra exibir na listagem sem custar uma chamada ao portal por linha. */
  NOME_MOTORISTA: string | null;
  FLAGCONFIRMADA: string | null;
  DTALTERACAO: string | null;
  DTCONFIRMACAO: string | null;
}

/** O payload do saveCarga é moldado pelo Fusion, não por nós — fica livre
 * de propósito, a tela só precisa de um punhado de campos fixos dele. */
export type PayloadCargaFusion = Record<string, unknown>;

/** Uma entrega da carga, como `get_itens_carga` devolve — cru do CISS.
 * `NUMSEQUENCIA` é a chave estável do item dentro da carga, usada pra
 * escolher quais entram no envio. */
export interface ItemCargaFusion {
  NUMSEQUENCIA: number;
  /** A pré-nota interna, não o pedido — ver IDORCAMENTO. */
  IDPLANILHADOCORI: number;
  /** O pedido real (número de orçamento) — null se a carga ainda não tem
   * orçamento vinculado no CISS, e nesse caso a linha não é enviada ao
   * Fusion nem aparece como pedido selecionável na tela. Use este pra
   * exibir/enviar ao Fusion, nunca IDPLANILHADOCORI. */
  IDORCAMENTO: number | null;
  IDSUBPRODUTO: number;
  QTDPRODUTO: number;
  /** A NF real, resolvida via PRODUTO_RETIRADO_NOTAS — pode ser null
   * quando a nota ainda não foi emitida. */
  NUMNOTA: number | null;
  SERIENOTA: string | null;
  VALTOTLIQUIDO: number | null;
  PESOBRUTO: number | null;
  /** PRODUTO.DESCRCOMPRODUTO — a descrição completa, não a resumida. */
  DESCRCOMPRODUTO: string | null;
  /** Dimensões/peso de catálogo (PRODUTO_GRADE), em cm/kg — não confundir
   * com PESOBRUTO acima, que é o peso da venda, não do produto. */
  LARGURA: number | null;
  ALTURA: number | null;
  COMPRIMENTO: number | null;
  PRODUTO_PESOBRUTO: number | null;
}

export interface SelecaoEnvioCargaFusion {
  motoristaCodigoErp?: string | null;
  veiculoCodigoErp?: string | null;
  /** NUMSEQUENCIA das entregas escolhidas; `null`/ausente = todas. */
  itens?: number[] | null;
}

export interface PreviewCargaFusion {
  payload: PayloadCargaFusion;
  /** Um elemento por pedido, com itens[] de produto — vai para o
   * saveEntregaServico, que roda antes do saveCarga (D10). */
  payload_entregas_servico: PayloadCargaFusion[];
  avisos: string[];
  itens: ItemCargaFusion[];
  /** Motorista sugerido, lido ao vivo do Fusion por IDMOTORISTA —
   * `null` se a carga não tiver um, ou ele não estiver cadastrado lá. */
  sugestao_motorista: MotoristaFusion | null;
  /** Veículo sugerido, lido ao vivo do Fusion por PLACATRANSP — mesma
   * regra do motorista. */
  sugestao_veiculo: VeiculoFusion | null;
  /** Presente se a carga já teve alguma passagem pela fila (sucesso ou
   * falha). `status: "ENVIADA"` significa que reenviar precisa de
   * `forcar: true` em `enviarCarga`, senão o backend recusa sem chamar o
   * Fusion de novo. */
  item_fila: ItemFilaFusion | null;
}

export interface ResultadoEnvioCargaFusion {
  status: "enviada" | "pendente" | "ja_enviada";
  resultado?: unknown;
  motivo?: string;
  avisos?: string[];
  fila?: ItemFilaFusion;
}

export interface ItemFilaFusion {
  id: number;
  id_empresa: number;
  id_planilha: number;
  status: "PENDENTE" | "ENVIADA";
  motivo: string | null;
  avisos: string[];
  tentativas: number;
  enviado_em: string | null;
  /** true quando a carga foi marcada como enviada sem chamar o Fusion
   * (já tinha sido integrada por fora desta tool) — ver marcarCargaIntegrada. */
  integrada_manualmente: boolean;
}

// --- Pré-cargas Fusion ------------------------------------------------------
// Addendum à spec 2026-10-14-cargas-fusion-design.md: DBA.PRE_CARGA é o
// estágio anterior à carga (DBA.RETIRADA_PRODUTOS), sem motorista/veículo
// do CISS ainda — quem envia escolhe os dois na tela.

export interface PreCargaResumo {
  IDPRECARGA: number;
  IDEMPRESA: number;
  DESCRPRECARGA: string | null;
  DTMOVIMENTO: string | null;
  DTALTERACAO: string | null;
  FLAGCONFERIDA: string | null;
  IDSTATUS: number;
}

/** Mesmo shape de ItemCargaFusion, mas sem NUMNOTA/SERIENOTA (a NF ainda
 * não existe antes da carga ser formada) e NUMSEQUENCIA sintético. */
export interface ItemPreCargaFusion {
  NUMSEQUENCIA: number;
  IDPLANILHADOCORI: number;
  IDORCAMENTO: number | null;
  IDSUBPRODUTO: number;
  QTDPRODUTO: number;
  VALTOTLIQUIDO: number | null;
  PESOBRUTO: number | null;
  DESCRCOMPRODUTO: string | null;
  LARGURA: number | null;
  ALTURA: number | null;
  COMPRIMENTO: number | null;
  PRODUTO_PESOBRUTO: number | null;
}

export interface SelecaoEnvioPreCargaFusion {
  motoristaCodigoErp?: string | null;
  veiculoCodigoErp?: string | null;
  itens?: number[] | null;
}

export interface PreviewPreCargaFusion {
  payload: PayloadCargaFusion;
  payload_entregas_servico: PayloadCargaFusion[];
  avisos: string[];
  itens: ItemPreCargaFusion[];
  /** O motorista escolhido, lido ao vivo do Fusion por `motorista_codigo_erp`. */
  motorista: MotoristaFusion | null;
  /** O veículo escolhido, lido ao vivo do Fusion por `veiculo_codigo_erp`. */
  veiculo: VeiculoFusion | null;
  item_fila: ItemFilaPreCargaFusion | null;
}

export interface ItemFilaPreCargaFusion {
  id: number;
  id_empresa: number;
  id_precarga: number;
  codigo_erp: string;
  motorista_codigo_erp: string | null;
  motorista_nome: string | null;
  veiculo_codigo_erp: string | null;
  veiculo_descricao: string | null;
  status: "PENDENTE" | "ENVIADA";
  motivo: string | null;
  avisos: string[];
  tentativas: number;
  enviado_em: string | null;
}

/** Lido ao vivo do portal do Fusion (fusion-py), não de cadastro local —
 * sem `id` nem `ativo`; `codigo_erp` é o identificador. Sem
 * `peso_max_entregas`/`volume_max_entregas`/`qtd_pallets_veiculo`: a grade
 * do portal não traz esses campos (nunca teve fonte melhor que placeholder
 * pra eles, nem no antigo cadastro local). */
export interface VeiculoFusion {
  codigo_erp: string;
  placa: string;
  descricao: string | null;
  modelo: string | null;
  ano_modelo: number | null;
  ano_fabricacao: number | null;
}

/** Lido ao vivo do portal do Fusion (fusion-py), não de cadastro local
 * (2026-10-20) — sem `id` nem `ativo`; `codigo_erp` é o identificador. */
export interface MotoristaFusion {
  codigo_erp: string;
  nome: string;
  cpf: string | null;
  telefone: string | null;
  endereco: string | null;
  uf: string | null;
  cidade: string | null;
  cep: string | null;
}

export interface AjudanteFusion {
  id: number;
  idclifor_ciss: number | null;
  codigo_erp: string;
  nome: string;
  ativo: boolean;
}

export interface CandidatoPessoaCiss {
  IDCLIFOR: number;
  NOME: string;
  CNPJCPF: string | null;
  TELEFONE: string | null;
  ENDERECO: string | null;
  UF: string | null;
  DESCRCIDADE: string | null;
  CEP: string | null;
}
