import { z } from "zod";
import { apiFetch, apiJson } from "../api";

// Matches backend StatusResponse (api/auth.py).
export const sessionSchema = z.object({
  logged_in: z.boolean(),
  pin_set: z.boolean(),
  lockout_seconds_remaining: z.number().int().nonnegative(),
});
export type Session = z.infer<typeof sessionSchema>;

// Matches backend LoginResponse (api/auth.py).
const loginResponseSchema = z.object({
  ok: z.boolean(),
  session: z.string(),
});

export const auth = {
  async session(): Promise<Session> {
    // Backend route is /api/auth/status (there is no /auth/session).
    return apiJson("/auth/status", sessionSchema);
  },
  async login(pin: string): Promise<Session> {
    // Backend expects JSON body { pin }; form-urlencoded would 422.
    await apiJson("/auth/login", loginResponseSchema, {
      method: "POST",
      body: { pin },
    });
    // The cookie is set by the server on the login response; re-read the
    // session state so the caller gets the same shape regardless of which
    // call they made.
    return apiJson("/auth/status", sessionSchema);
  },
  async logout(): Promise<void> {
    await apiFetch("/auth/logout", { method: "POST" });
  },
  async changePin(currentPin: string, newPin: string): Promise<void> {
    await apiFetch("/auth/change-pin", {
      method: "POST",
      body: { current_pin: currentPin, new_pin: newPin },
    });
  },
};
