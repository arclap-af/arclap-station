import { z } from "zod";
import { apiFetch, apiJson } from "../api";

export const sessionSchema = z.object({
  authenticated: z.boolean(),
  pin_set: z.boolean(),
  expires_in: z.number().int().nonnegative().nullable(),
});
export type Session = z.infer<typeof sessionSchema>;

export const auth = {
  async session(): Promise<Session> {
    return apiJson("/auth/session", sessionSchema);
  },
  async login(pin: string): Promise<Session> {
    return apiJson("/auth/login", sessionSchema, { method: "POST", form: true, body: { pin } });
  },
  async logout(): Promise<void> {
    await apiFetch("/auth/logout", { method: "POST" });
  },
};
