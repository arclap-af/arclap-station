/**
 * useWebSocket — minimal, reconnecting WebSocket subscription.
 *
 *  - One ws per URL per component instance.
 *  - Reconnects with exponential backoff capped at 30s.
 *  - Pauses (closes) when the tab is hidden, reconnects when it comes back.
 *  - Caller decides what to do with messages via onMessage.
 *  - Returns a status string so the UI can show a "live" / "reconnecting" pill.
 */

import { useEffect, useRef, useState } from "react";

export type WsStatus = "connecting" | "open" | "closed" | "error";

interface UseWebSocketOptions {
  /** Set false to leave the socket disconnected (e.g. on login screen). */
  enabled?: boolean;
  /** Convert the incoming MessageEvent before handing to onMessage. */
  binaryType?: "blob" | "arraybuffer";
}

export function useWebSocket(
  url: string | null,
  onMessage: (ev: MessageEvent) => void,
  opts: UseWebSocketOptions = {},
): { status: WsStatus; send: (data: string | ArrayBufferView | Blob) => void } {
  const { enabled = true, binaryType } = opts;
  const [status, setStatus] = useState<WsStatus>("closed");
  const wsRef = useRef<WebSocket | null>(null);
  const attemptRef = useRef(0);
  const onMessageRef = useRef(onMessage);
  onMessageRef.current = onMessage;

  useEffect(() => {
    if (!enabled || !url) {
      return;
    }
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (!alive) return;
      const fullUrl = url.startsWith("ws") ? url : wsUrl(url);
      setStatus("connecting");
      let ws: WebSocket;
      try {
        ws = new WebSocket(fullUrl);
      } catch {
        setStatus("error");
        scheduleReconnect();
        return;
      }
      if (binaryType) ws.binaryType = binaryType;
      wsRef.current = ws;

      ws.onopen = () => {
        attemptRef.current = 0;
        setStatus("open");
      };
      ws.onmessage = (ev) => onMessageRef.current(ev);
      ws.onerror = () => {
        setStatus("error");
      };
      ws.onclose = () => {
        wsRef.current = null;
        setStatus("closed");
        scheduleReconnect();
      };
    };

    const scheduleReconnect = () => {
      if (!alive) return;
      if (document.hidden) return; // resume on visibility change
      attemptRef.current += 1;
      const backoff = Math.min(30_000, 500 * 2 ** Math.min(attemptRef.current, 6));
      timer = setTimeout(connect, backoff);
    };

    const onVisibility = () => {
      if (document.hidden) {
        wsRef.current?.close();
      } else if (wsRef.current === null) {
        connect();
      }
    };
    document.addEventListener("visibilitychange", onVisibility);

    connect();

    return () => {
      alive = false;
      document.removeEventListener("visibilitychange", onVisibility);
      if (timer) clearTimeout(timer);
      wsRef.current?.close();
      wsRef.current = null;
    };
  }, [url, enabled, binaryType]);

  const send = (data: string | ArrayBufferView | Blob) => {
    wsRef.current?.send(data);
  };

  return { status, send };
}

/** Convert a path like `/ws/home` into a full `ws://…` URL using the current origin. */
export function wsUrl(path: string): string {
  const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${proto}//${window.location.host}${path}`;
}
