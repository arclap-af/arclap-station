import { describe, expect, it, vi } from "vitest";
import { screen, waitFor } from "@testing-library/react";

import { renderWithProviders } from "./test-utils";
import { Home } from "../../frontend/src/pages/Home";

vi.mock("../../frontend/src/lib/bridge/home", () => ({
  home: {
    telemetry: vi.fn(async () => ({
      hostname: "arclap-test-1",
      ip: "10.0.0.5",
      serial: "RPi5-TEST-0001",
      firmware: "0.1.0",
      uptime_seconds: 12 * 3600 + 30 * 60,
      status: "online",
      last_sync_seconds_ago: 3,
      captures_today: 17,
      next_capture_seconds: 60 * 5,
      queue_pending: 2,
      queue_failed: 0,
      avg_upload_seconds: 1.4,
      storage_used_pct: 14,
      storage_free_bytes: 200 * 1e9,
      cpu_pct: 11,
      cpu_temp_c: 40.1,
      memory_used_mb: 800,
      memory_total_mb: 8000,
      network_throughput_mbps: 30,
      network_signal_dbm: -55,
      ups_pct: 100,
      ups_status: "mains",
      camera: {
        detected: true,
        model: "Canon EOS R6",
        lens: "RF 24-70",
        firmware: "1.8.1",
        battery_pct: 88,
        shutter_count: 999,
        sensor_temp_c: 33,
        usb_port: "usb:001,004",
        driver: "gphoto2 2.5.31",
      },
    })),
    activity: vi.fn(async () => [
      { ts: "12:00:00", service: "camera", level: "info", message: "capture ok" },
    ]),
  },
}));

describe("Home", () => {
  it("renders telemetry tiles", async () => {
    renderWithProviders(<Home />);
    await waitFor(() => {
      expect(screen.getByText(/Station overview/i)).toBeInTheDocument();
      expect(screen.getByText(/arclap-test-1/)).toBeInTheDocument();
      expect(screen.getByText(/17/)).toBeInTheDocument(); // captures today
      expect(screen.getByText(/Canon EOS R6/)).toBeInTheDocument();
    });
  });

  it("renders activity list", async () => {
    renderWithProviders(<Home />);
    await waitFor(() => {
      expect(screen.getByText(/capture ok/i)).toBeInTheDocument();
    });
  });
});
