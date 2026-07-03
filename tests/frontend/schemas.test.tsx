import { describe, expect, it, vi } from "vitest";

import {
  destinationListSchema,
  telemetrySchema,
  warnOnDrift,
} from "../../frontend/src/lib/bridge/schemas";

describe("warnOnDrift", () => {
  it("stays silent when the response matches the schema", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    warnOnDrift(
      [{ id: "d1", name: "Primary S3", type: "s3", enabled: true, queue_pending: 0 }],
      destinationListSchema,
      "destinations.list",
    );
    expect(warn).not.toHaveBeenCalled();
    warn.mockRestore();
  });

  it("warns (but does not throw) when a field drifts to the wrong type", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    // Backend regression: queue_pending sent as a string instead of a number.
    expect(() =>
      warnOnDrift([{ id: "d1", queue_pending: "seventeen" }], destinationListSchema, "destinations.list"),
    ).not.toThrow();
    expect(warn).toHaveBeenCalledOnce();
    expect(String(warn.mock.calls[0][0])).toContain("destinations.list");
    warn.mockRestore();
  });

  it("warns when a known field has the wrong type in telemetry", () => {
    const warn = vi.spyOn(console, "warn").mockImplementation(() => {});
    warnOnDrift({ hostname: 123 }, telemetrySchema, "home.telemetry");
    expect(warn).toHaveBeenCalledOnce();
    warn.mockRestore();
  });
});
