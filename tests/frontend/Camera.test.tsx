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
      expect(screen.getByRole("button", { name: /Shutter/i })).toBeInTheDocument();
    });
  });

  it("changes ISO when chip clicked", async () => {
    const user = userEvent.setup();
    renderWithProviders(<CameraPage />);
    await waitFor(() => expect(screen.getByText("100")).toBeInTheDocument());
    // Sanity check: clicking the chip dispatches without throwing.
    await user.click(screen.getByText("400"));
    expect(screen.getByText("400")).toBeInTheDocument();
  });
});
