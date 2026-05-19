import { wsUrl } from "../ws";

// Terminal WebSocket — bytes-in / bytes-out, no JSON framing.
// Backend wraps a restricted PTY; the route is gated by the same PIN session.
export const terminal = {
  url(): string {
    return wsUrl("/api/v1/terminal/ws");
  },
};
