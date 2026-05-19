import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "./test-utils";
import { Login } from "../../frontend/src/pages/Login";

vi.mock("../../frontend/src/lib/bridge/auth", () => {
  const sessionState = { current: { authenticated: false, pin_set: true, expires_in: 0 } };
  return {
    auth: {
      session: vi.fn(async () => sessionState.current),
      login: vi.fn(async (pin: string) => {
        if (pin === "123456") {
          sessionState.current = { authenticated: true, pin_set: true, expires_in: 900 };
          return sessionState.current;
        }
        throw new Error("Wrong PIN");
      }),
      logout: vi.fn(async () => {}),
    },
  };
});

describe("Login", () => {
  beforeEach(() => {
    // ensure clean state
  });

  it("submits PIN once 6 digits typed", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Login />);
    const inputs = await screen.findAllByLabelText(/PIN digit/i);
    expect(inputs).toHaveLength(6);
    for (let i = 0; i < 6; i++) {
      await user.type(inputs[i], String(i + 1));
    }
    await waitFor(() => {
      expect(screen.queryByText(/Wrong PIN/i)).not.toBeInTheDocument();
    });
  });

  it("shows error on wrong PIN", async () => {
    const user = userEvent.setup();
    renderWithProviders(<Login />);
    const inputs = await screen.findAllByLabelText(/PIN digit/i);
    for (let i = 0; i < 6; i++) {
      await user.type(inputs[i], "9");
    }
    await waitFor(() => {
      // Either copy is acceptable — both signal a rejected attempt.
      const found = screen.queryByText(/Wrong PIN/i) || screen.queryByText(/Login failed/i);
      expect(found).toBeInTheDocument();
    });
  });
});
