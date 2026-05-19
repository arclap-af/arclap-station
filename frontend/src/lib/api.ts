/**
 * fetch wrapper for the on-device FastAPI backend.
 *
 *  - Always sends credentials (the PIN session cookie).
 *  - JSON in, JSON out by default.
 *  - Throws an ApiError on non-2xx with the decoded body.
 *  - Centralises the URL prefix so other modules never hardcode "/api/v1".
 */

import { z } from "zod";

export const API_PREFIX = "/api/v1";

export class ApiError extends Error {
  public readonly status: number;
  public readonly body: unknown;
  public readonly url: string;

  constructor(status: number, body: unknown, url: string) {
    const detail =
      body && typeof body === "object" && body !== null && "detail" in body
        ? String((body as { detail?: unknown }).detail ?? "")
        : "";
    super(`HTTP ${status} ${url}${detail ? ` — ${detail}` : ""}`);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
    this.url = url;
  }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

interface RequestOptions {
  method?: Method;
  body?: unknown;
  signal?: AbortSignal;
  /** Skip the API_PREFIX (only the bridge clients need this for /api/setup/status). */
  raw?: boolean;
  /** Send `application/x-www-form-urlencoded` instead of JSON (PIN login). */
  form?: boolean;
}

async function decode(res: Response): Promise<unknown> {
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("application/json")) {
    try {
      return await res.json();
    } catch {
      return null;
    }
  }
  return await res.text();
}

export async function apiFetch<T = unknown>(path: string, opts: RequestOptions = {}): Promise<T> {
  const url = opts.raw ? path : `${API_PREFIX}${path}`;
  const headers: Record<string, string> = {};
  let body: BodyInit | undefined;
  if (opts.body !== undefined) {
    if (opts.form) {
      headers["content-type"] = "application/x-www-form-urlencoded";
      body = new URLSearchParams(opts.body as Record<string, string>).toString();
    } else {
      headers["content-type"] = "application/json";
      body = JSON.stringify(opts.body);
    }
  }
  const res = await fetch(url, {
    method: opts.method ?? (opts.body ? "POST" : "GET"),
    credentials: "include",
    headers,
    body,
    signal: opts.signal,
  });
  const decoded = await decode(res);
  if (!res.ok) {
    throw new ApiError(res.status, decoded, url);
  }
  return decoded as T;
}

/**
 * Convenience helper: validate the decoded JSON against a Zod schema.
 * Errors bubble up as ApiError so callers can show a friendly message.
 */
export async function apiJson<T>(
  path: string,
  schema: z.ZodType<T>,
  opts: RequestOptions = {},
): Promise<T> {
  const raw = await apiFetch<unknown>(path, opts);
  const parsed = schema.safeParse(raw);
  if (!parsed.success) {
    throw new ApiError(200, { detail: `schema mismatch: ${parsed.error.message}` }, path);
  }
  return parsed.data;
}
