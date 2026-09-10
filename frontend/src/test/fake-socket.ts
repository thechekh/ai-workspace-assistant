/**
 * Stand-in for the browser's WebSocket, installed globally by setup.ts.
 *
 * happy-dom ships a real WebSocket that would try to reach ws://localhost.
 * This one records every instance so a component test can accept the
 * connection or deliver server frames through the store's real vueuse wiring.
 */
export class FakeWebSocket {
  static instances: FakeWebSocket[] = [];

  /** The socket the store opened most recently — there is exactly one live. */
  static get last(): FakeWebSocket {
    const socket = FakeWebSocket.instances.at(-1);
    if (!socket) throw new Error("no WebSocket has been opened yet");
    return socket;
  }

  static reset(): void {
    FakeWebSocket.instances = [];
  }

  readyState = 0;
  sent: string[] = [];
  onopen: ((event: Event) => void) | null = null;
  onclose: ((event: Event) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;

  constructor(
    public readonly url: string,
    public readonly protocols?: string | string[],
  ) {
    FakeWebSocket.instances.push(this);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.readyState = 3;
  }

  /** The server accepted the connection. */
  accept(): void {
    this.readyState = 1;
    this.onopen?.(new Event("open"));
  }

  /** A frame from the server. */
  deliver(frame: object): void {
    this.onmessage?.(new MessageEvent("message", { data: JSON.stringify(frame) }));
  }
}
