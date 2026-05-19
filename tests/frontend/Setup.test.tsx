import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "./test-utils";
import { SetupWizard } from "../../frontend/src/pages/Setup";

// Setup bridge — all endpoints resolve to no-op success.
vi.mock("../../frontend/src/lib/bridge/setup", () => ({
  setup: {
    status: vi.fn(async () => ({ first_boot: true, step: "welcome", completed_steps: [] })),
    setPin: vi.fn(async () => {}),
    detectCamera: vi.fn(async () => ({
      detected: true,
      model: "Canon EOS R6",
      lens: "RF 24-70",
      firmware: "1.8.1",
      battery: 80,
      shutter_count: 100,
    })),
    station: vi.fn(async () => {}),
    network: vi.fn(async () => ({ eth: "up", wifi: "up", cell: "standby", ntp: "ok" })),
    destination: vi.fn(async () => {}),
    schedule: vi.fn(async () => {}),
    pair: vi.fn(async () => {}),
    finish: vi.fn(async () => {}),
  },
}));

vi.mock("../../frontend/src/lib/bridge/acceptance", () => ({
  acceptance: {
    start: vi.fn(async () => ({
      run_id: "run-1",
      started_at: "2026-05-19T12:00:00Z",
      finished_at: "2026-05-19T12:00:01Z",
      status: "pass" as const,
      results: Array.from({ length: 40 }, (_, i) => ({
        group: "Hardware",
        name: `check-${i}`,
        ok: true,
        detail: null,
        duration_ms: 5,
      })),
    })),
    status: vi.fn(async (id: string) => ({
      run_id: id,
      started_at: "2026-05-19T12:00:00Z",
      finished_at: "2026-05-19T12:00:01Z",
      status: "pass" as const,
      results: [],
    })),
  },
}));

describe("Setup wizard", () => {
  it("walks all 10 steps and reaches done", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SetupWizard />);

    // 1. Welcome
    await waitFor(() => expect(screen.getAllByText(/Welcome/i).length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: /Get started/i }));

    // 2. PIN
    await waitFor(() => expect(screen.getAllByText(/Set a PIN/i).length).toBeGreaterThan(0));
    const digits = screen.getAllByLabelText(/PIN digit/i);
    for (let i = 0; i < 6; i++) {
      await user.type(digits[i], String(i + 1));
    }
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    // 3. Camera (auto-detects via mock)
    await waitFor(() => expect(screen.getAllByText(/Connect camera/).length).toBeGreaterThan(0));
    await waitFor(() => expect(screen.getByText(/Canon EOS R6/)).toBeInTheDocument(), { timeout: 4000 });
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    // 4. Station name
    await waitFor(() => expect(screen.getAllByText(/Name station/).length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    // 5. Network
    await waitFor(() => expect(screen.getAllByText(/Network$/i).length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    // 6. Destination
    await waitFor(() => expect(screen.getAllByText(/Where photos go/).length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    // 7. Schedule
    await waitFor(() => expect(screen.getAllByText(/Capture schedule/).length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    // 8. Pair
    await waitFor(() => expect(screen.getAllByText(/Pair to cloud/).length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    // 9. Acceptance check
    await waitFor(() => expect(screen.getAllByText(/Acceptance check/).length).toBeGreaterThan(0));
    await user.click(screen.getByRole("button", { name: /Run all/i }));
    await waitFor(() => {
      expect(screen.getByText(/Ready to ship/i)).toBeInTheDocument();
    });
    await user.click(screen.getByRole("button", { name: /Continue/i }));

    // 10. Done
    await waitFor(() => expect(screen.getAllByText(/Setup complete/).length).toBeGreaterThan(0));
    expect(screen.getByRole("button", { name: /Open station/i })).toBeInTheDocument();
  }, 20_000);
});
