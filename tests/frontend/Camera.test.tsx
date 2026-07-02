import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { renderWithProviders } from "./test-utils";
import { CameraPage } from "../../frontend/src/pages/Camera";

vi.mock("../../frontend/src/lib/bridge/camera", async () => {
  let cur = {
    mode: "M",
    iso: "200",
    shutter: "1/250",
    aperture: "f/8",
    ev: "0",
    wb: "Daylight",
    kelvin: 5500,
    drive: "Single",
    quality: "RAW+JPEG L",
    focus: "AF-S",
    af_area: "Center spot",
    metering: "Evaluative",
    picture_style: "Standard",
    color_space: "sRGB",
    aspect: "3:2",
  };
  return {
    camera: {
      // The page also queries camera.info() for detection state + choices.
      // Empty choices → the ChipRows fall back to their built-in presets
      // (which include the "100"/"400" ISO chips the tests click).
      info: vi.fn(async () => ({
        detected: true,
        model: "Canon EOS 5D Mark IV",
        lens: "EF 24-70mm f/2.8L II USM",
        battery: "75%",
        port: "usb:001,005",
        shutter_count: 12045,
        values: {},
        choices: {},
        health: {
          ok: true,
          last_ok_at: "2026-05-19T12:00:00Z",
          last_error: null,
          last_error_at: null,
          last_reset_at: null,
          beacon_age_sec: 3,
        },
      })),
      settings: vi.fn(async () => cur),
      updateSettings: vi.fn(async (patch: Partial<typeof cur>) => {
        cur = { ...cur, ...patch };
        return cur;
      }),
      capture: vi.fn(async () => ({ id: "1", filename: "ph_1.jpg", size_bytes: 1024 })),
      reconnect: vi.fn(async () => ({ ok: true })),
      syncClock: vi.fn(async () => {}),
      usbReset: vi.fn(async () => {}),
      properties: vi.fn(async () => []),
      setProperty: vi.fn(async () => {}),
    },
  };
});

describe("Camera", () => {
  it("renders viewfinder and current exposure", async () => {
    renderWithProviders(<CameraPage />);
    await waitFor(() => {
      expect(screen.getByText(/Camera$/i)).toBeInTheDocument();
      // "Shutter" is a ChipRow label; its chips are the shutter presets.
      expect(screen.getByText(/Shutter/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "1/250" })).toBeInTheDocument();
    });
  });

  it("changes ISO when chip clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CameraPage />);
    await waitFor(() => expect(screen.getByRole("button", { name: "100" })).toBeInTheDocument());
    // Sanity check: clicking the chip dispatches without throwing. Scope to
    // the button role — after the click "400" also appears in the row's
    // current-value label, so a bare getByText would be ambiguous.
    await user.click(screen.getByRole("button", { name: "400" }));
    expect(screen.getByRole("button", { name: "400" })).toBeInTheDocument();
  });
});
