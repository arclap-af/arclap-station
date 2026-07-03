import { beforeEach, describe, expect, it, vi } from "vitest";
import { screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "./test-utils";

const h = vi.hoisted(() => ({
  general: vi.fn(),
  saveGeneral: vi.fn(async () => ({})),
}));

vi.mock("../../frontend/src/lib/bridge/settings", () => ({
  settings: { general: h.general, saveGeneral: h.saveGeneral },
}));

import { General } from "../../frontend/src/pages/Settings/tabs/General";

const GENERAL = {
  station_name: "Arclap Station",
  site: "Site A",
  gps: "",
  asset_tag: "SN123",
  timezone: "UTC",
  date_format: "YYYY-MM-DD",
  language: "en",
  ntp_servers: "time.cloudflare.com",
  watermark: true,
  dedup_threshold: null,
  bandwidth_kbps: null,
  project_starts_at: null,
  project_ends_at: null,
};

describe("Settings · General", () => {
  beforeEach(() => {
    h.general.mockResolvedValue({ ...GENERAL });
    h.saveGeneral.mockClear();
  });

  it("renders the current station identity", async () => {
    renderWithProviders(<General />);
    expect(await screen.findByDisplayValue("Arclap Station")).toBeInTheDocument();
  });

  it("sends the edited station name through saveGeneral", async () => {
    const user = userEvent.setup();
    renderWithProviders(<General />);
    const nameInput = await screen.findByDisplayValue("Arclap Station");
    await user.clear(nameInput);
    await user.type(nameInput, "Tower Crane 7");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(h.saveGeneral).toHaveBeenCalledOnce();
    expect(h.saveGeneral.mock.calls[0][0].station_name).toBe("Tower Crane 7");
  });
});
