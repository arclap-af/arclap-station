// Narrowing helpers for the API boundary.
//
// Backend responses are untyped JSON. Rather than reach for `any` (which
// switches type-checking off for everything downstream), bridge adapters
// type raw responses as `unknown` and pull values out through these
// coercers. Each returns a concrete type, so the rest of the adapter — and
// every consumer of it — stays fully type-checked. This is the one place
// the `unknown`→concrete narrowing lives, so the pattern is consistent
// across all bridge files.

export type JsonObject = Record<string, unknown>;

/** Narrow to a plain object; `{}` when the value isn't one. */
export function obj(v: unknown): JsonObject {
  return v !== null && typeof v === "object" && !Array.isArray(v)
    ? (v as JsonObject)
    : {};
}

/** Narrow to an array; `[]` when the value isn't one. */
export function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

/**
 * Stringify with a fallback. Returns `fb` for null/undefined/empty-string,
 * otherwise `String(v)`. Mirrors the `String(x ?? fb)` idiom the adapters
 * used before, so runtime output is unchanged.
 */
export function str(v: unknown, fb = "—"): string {
  return v === null || v === undefined || v === "" ? fb : String(v);
}

/** Like {@link str} but yields `null` (not a dash) when absent. */
export function strOrNull(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

/** Coerce to a finite number; `fb` otherwise. */
export function num(v: unknown, fb = 0): number {
  return typeof v === "number" && Number.isFinite(v) ? v : fb;
}

/** Like {@link num} but yields `null` when the value isn't a number. */
export function numOrNull(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

/** Truthiness coercion, spelled out so intent reads clearly at call sites. */
export function bool(v: unknown): boolean {
  return Boolean(v);
}
