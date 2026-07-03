import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "./test-utils";

const h = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(async () => ({})),
  update: vi.fn(async () => ({})),
  remove: vi.fn(async () => {}),
  setEnabled: vi.fn(async () => ({})),
  test: vi.fn(async () => ({ ok: true, steps: [] })),
}));

vi.mock("../../frontend/src/lib/bridge/destinations", () => ({
  destinations: {
    list: h.list,
    create: h.create,
    update: h.update,
    remove: h.remove,
    setEnabled: h.setEnabled,
    test: h.test,
  },
}));

import { Destinations } from "../../frontend/src/pages/Destinations";

const DEST = {
  id: "d1",
  kind: "s3" as const,
  name: "Primary S3",
  enabled: true,
  config: { bucket: "arclap", region: "eu-central-1" },
  queue_pending: 0,
  queue_failed: 0,
  last_sync: "2026-05-19T12:00:00Z",
  bytes_today: 1024,
  retry_policy: 3,
  encrypt_in_transit: true,
};

describe("Destinations", () => {
  beforeEach(() => {
    h.setEnabled.mockClear();
  });

  it("renders configured destinations from the query", async () => {
    h.list.mockResolvedValue([DEST]);
    renderWithProviders(<Destinations />);
    expect(await screen.findByText("Primary S3")).toBeInTheDocument();
  });

  it("shows an error+retry state (not an empty state) when the list fetch fails", async () => {
    h.list.mockRejectedValue(new Error("ECONNREFUSED"));
    renderWithProviders(<Destinations />);
    expect(await screen.findByText(/Couldn't load destinations/i)).toBeInTheDocument();
    // The misleading "No destinations yet" empty state must NOT show on error.
    expect(screen.queryByText(/No destinations yet/i)).not.toBeInTheDocument();
  });

  it("shows the empty state when there are genuinely no destinations", async () => {
    h.list.mockResolvedValue([]);
    renderWithProviders(<Destinations />);
    expect(await screen.findByText(/No destinations yet/i)).toBeInTheDocument();
  });

  it("toggles a destination's enabled state via its row toggle", async () => {
    h.list.mockResolvedValue([DEST]);
    const user = userEvent.setup();
    renderWithProviders(<Destinations />);
    await screen.findByText("Primary S3");
    await user.click(screen.getByRole("button", { name: "Toggle" }));
    expect(h.setEnabled).toHaveBeenCalledWith("d1", false);
  });
});
