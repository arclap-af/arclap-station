import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "./test-utils";

// Hoisted so the vi.mock factory (which is itself hoisted) can reference them.
const h = vi.hoisted(() => ({
  list: vi.fn(),
  create: vi.fn(async () => ({})),
  update: vi.fn(async () => ({})),
  remove: vi.fn(async () => {}),
  setEnabled: vi.fn(async () => ({})),
}));

vi.mock("../../frontend/src/lib/bridge/schedule", () => ({
  schedule: {
    list: h.list,
    create: h.create,
    update: h.update,
    remove: h.remove,
    setEnabled: h.setEnabled,
  },
}));
vi.mock("../../frontend/src/lib/bridge/destinations", () => ({
  destinations: { list: vi.fn(async () => []) },
}));

import { SchedulePage } from "../../frontend/src/pages/Schedule";

const SCHEDULE = {
  id: "s1",
  name: "Daytime capture",
  interval_minutes: 15,
  from_time: "06:00",
  to_time: "19:00",
  days: ["mon", "tue", "wed", "thu", "fri"],
  enabled: true,
  skip_disk_full: true,
  skip_destinations_offline: true,
  destination_id: null,
  destination_label: "All destinations",
  keep_local: true,
  next_fire_at: null,
};

describe("Schedule", () => {
  beforeEach(() => {
    h.list.mockResolvedValue([SCHEDULE]);
    h.create.mockClear();
    h.setEnabled.mockClear();
  });

  it("renders configured schedules", async () => {
    renderWithProviders(<SchedulePage />);
    expect(await screen.findByText("Daytime capture")).toBeInTheDocument();
    expect(screen.getByText(/Configured · 1/)).toBeInTheDocument();
  });

  it("creates a schedule through the New-schedule editor", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SchedulePage />);
    await screen.findByText("Daytime capture");
    // Header + empty-column prompt both offer "New schedule"; either opens the editor.
    await user.click(screen.getAllByRole("button", { name: /New schedule/i })[0]);
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(h.create).toHaveBeenCalledOnce();
  });

  it("lets the operator set a custom (non-preset) interval", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SchedulePage />);
    await screen.findByText("Daytime capture");
    await user.click(screen.getAllByRole("button", { name: /New schedule/i })[0]);
    const custom = screen.getByLabelText("Custom interval in minutes");
    await user.clear(custom);
    await user.type(custom, "20");
    await user.tab(); // blur commits the value to the draft
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(h.create).toHaveBeenCalledWith(expect.objectContaining({ interval_minutes: 20 }));
  });

  it("pauses a schedule via its row toggle", async () => {
    const user = userEvent.setup();
    renderWithProviders(<SchedulePage />);
    await screen.findByText("Daytime capture");
    await user.click(screen.getAllByRole("button", { name: "Toggle" })[0]);
    expect(h.setEnabled).toHaveBeenCalledWith("s1", false);
  });
});
