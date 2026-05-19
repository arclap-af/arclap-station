import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});

// jsdom doesn't implement matchMedia.
Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

// jsdom WebSocket stub so useWebSocket can mount without errors.
class FakeWebSocket {
  readyState = 0;
  url: string;
  onopen: ((this: WebSocket, ev: Event) => unknown) | null = null;
  onmessage: ((this: WebSocket, ev: MessageEvent) => unknown) | null = null;
  onclose: ((this: WebSocket, ev: CloseEvent) => unknown) | null = null;
  onerror: ((this: WebSocket, ev: Event) => unknown) | null = null;
  constructor(url: string) {
    this.url = url;
  }
  send() {}
  close() {
    this.readyState = 3;
    this.onclose?.call(this as unknown as WebSocket, new CloseEvent("close"));
  }
}
// @ts-expect-error stub
globalThis.WebSocket = FakeWebSocket;
