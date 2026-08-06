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

export type ServerEvent =
  | SessionEvent
  | TokenEvent
  | ToolCallEvent
  | ToolResultEvent
  | FinalEvent
  | ErrorEvent;

export interface UserMessage {
  type: "user_message";
  content: string;
}
