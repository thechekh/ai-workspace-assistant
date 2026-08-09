/** Mirror of the backend WS protocol (src/assistant/api/schemas.py). */

export interface SessionEvent {
  type: "session";
  session_id: string;
}

export interface TokenEvent {
  type: "token";
  content: string;
}

export interface ToolCallEvent {
  type: "tool_call";
  tool: string;
  arguments: Record<string, unknown>;
}

export interface ToolResultEvent {
  type: "tool_result";
  tool: string;
  result: string;
}

export interface FinalEvent {
  type: "final";
  content: string;
}

export interface ErrorEvent {
  type: "error";
  message: string;
}

/** Per-turn stats, sent right after `final` (TurnSummary on the backend). */
export interface TurnEvent {
  type: "turn";
  turn_id: string;
  backend: string;
  duration_ms: number;
  first_token_ms: number | null;
  llm_steps: number;
  tool_calls: string[];
  prompt_tokens: number;
  completion_tokens: number;
  usage_estimated: boolean;
  /** Indicative spend at listed pay-per-token prices; 0 for fake/unknown models. */
  cost_usd: number;
  /** True when the user pressed Stop: partial answer, real (partial) cost. */
  cancelled: boolean;
}

/** One row of a turn's audit timeline (GET /api/sessions/{id}/turns). */
export interface AuditEvent {
  ms: number;
  type: string; // tool_call | tool_result | final | error
  tool?: string;
  arguments?: string;
  result_chars?: number;
  chars?: number;
  message?: string;
}

export interface AuditTurn {
  turn_id: string;
  backend: string;
  duration_ms: number;
  tool_calls: string[];
  events: AuditEvent[];
}

export type ServerEvent =
  SessionEvent | TokenEvent | ToolCallEvent | ToolResultEvent | FinalEvent | ErrorEvent | TurnEvent;

export interface UserMessage {
  type: "user_message";
  content: string;
}

/** Stops the turn in flight; ignored server-side when nothing is running. */
export interface CancelRequest {
  type: "cancel";
}

export type ClientMessage = UserMessage | CancelRequest;

/** One row of the conversations panel (GET /api/sessions). */
export interface SessionSummary {
  session_id: string;
  /** Unix seconds of the last message — the panel's sort order. */
  updated_at: number;
  messages: number;
  preview: string;
}

/** A stored transcript (GET /api/sessions/{id}/messages). */
export interface StoredMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
}

/** One document in the knowledge base (GET /api/documents). */
export interface IndexedDocument {
  source: string;
  chunks: number;
}
