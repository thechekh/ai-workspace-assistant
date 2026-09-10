import { vi } from "vitest";

import { FakeWebSocket } from "./fake-socket";
import { confirmMock, fetchMock } from "./mocks";

// Component tests run the real store: no network, no dialogs, no sockets.
vi.stubGlobal("WebSocket", FakeWebSocket);
vi.stubGlobal("fetch", fetchMock);
vi.stubGlobal("confirm", confirmMock);
