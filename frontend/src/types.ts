/** Mirror of the backend WS protocol (src/assistant/api/schemas.py) and of
 *  the REST payloads the UI reads (src/assistant/api/routes.py). */

/** `UserMessage.content` max_length on the server; the composer stops there too. */
export const MAX_MESSAGE_CHARS = 8000;

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
  /** True when the turn ended in an error; the `error` frame has the message. */
  failed: boolean;
}

export type AuditEventType = "tool_call" | "tool_result" | "final" | "error";

/** One row of a turn's audit timeline (TurnAuditEvent). Fields are per-kind:
 *  tool_call carries tool+arguments, tool_result carries tool+result_chars,
 *  final carries chars, error carries message — the rest arrive as null. */
export interface AuditEvent {
  ms: number;
  type: AuditEventType;
  tool: string | null;
  arguments: string | null;
  result_chars: number | null;
  chars: number | null;
  message: string | null;
}

/** A replayable turn (TurnRecord): the same stats the `turn` frame carries,
 *  plus the timeline. GET /api/sessions/{id}/turns/{turn_id}. */
export interface AuditTurn extends Omit<TurnEvent, "type"> {
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

export interface SessionList {
  sessions: SessionSummary[];
}

/** A tool invocation as stored in history (ToolCall on the backend). */
export interface StoredToolCall {
  id: string;
  name: string;
  /** Raw JSON string, exactly as the model produced it. */
  arguments: string;
}

/** One stored message (ChatMessage on the backend). */
export interface StoredMessage {
  role: "system" | "user" | "assistant" | "tool";
  content: string;
  /** Assistant turns only. */
  tool_calls?: StoredToolCall[] | null;
  /** Tool turns only. */
  tool_call_id?: string | null;
}

/** A stored transcript (GET /api/sessions/{id}/messages). */
export interface SessionMessages {
  session_id: string;
  messages: StoredMessage[];
}

/** One document in the knowledge base (GET /api/documents). */
export interface IndexedDocument {
  source: string;
  chunks: number;
}

export interface DocumentList {
  documents: IndexedDocument[];
  total_chunks: number;
}

/** What POST /api/documents indexed. Re-uploading a source replaces it. */
export interface DocumentUploadResult {
  indexed: IndexedDocument[];
  chunks: number;
  skipped: string[];
}

/** GET /api/info — what the UI needs before it can talk. */
export interface PlatformInfo {
  backends: string[];
  default_backend: string;
  llm_provider: string;
  embedding_provider: string;
  retrieval_mode: string;
  collection: string;
  auth_required: boolean;
}

export interface HealthComponent {
  status: string;
  [detail: string]: unknown;
}

/** GET /api/health — one entry per dependency, each with its own status. */
export interface HealthInfo {
  status: "ok" | "degraded";
  components: Record<string, HealthComponent>;
}

// --- the transcript ---------------------------------------------------------
// The store's view of the frames above: what the chat window renders.

interface ItemBase {
  /** Stable and monotonic for the page's lifetime — the v-for key. */
  id: number;
  /** When the item appeared here (ms since epoch). Absent for restored
   *  history: the server keeps no per-message timing, and a restore time
   *  would be a lie. */
  at?: number;
}

export interface UserItem extends ItemBase {
  kind: "user";
  text: string;
}

export interface AssistantItem extends ItemBase {
  kind: "assistant";
  text: string;
  streaming: boolean;
  /** Answer cut short by Stop — rendered with a "stopped" marker. */
  cancelled?: boolean;
  /** Attached when the post-final `turn` frame arrives. */
  stats?: TurnEvent;
}

export interface ToolItem extends ItemBase {
  kind: "tool";
  tool: string;
  args: string;
  result: string | null;
}

export interface ErrorItem extends ItemBase {
  kind: "error";
  text: string;
}

export type ChatItem = UserItem | AssistantItem | ToolItem | ErrorItem;

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;

/** A ChatItem before the store stamps it with its `id` (and `at`). */
export type ChatItemDraft = DistributiveOmit<ChatItem, "id" | "at">;
