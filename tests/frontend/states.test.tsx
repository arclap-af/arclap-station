import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Async, ErrorState, LoadingState } from "../../frontend/src/components/states";
import { OfflineBanner } from "../../frontend/src/components/OfflineBanner";
import { I18nProvider } from "../../frontend/src/lib/i18n";

describe("resilience states", () => {
  it("LoadingState shows its label", () => {
    render(<LoadingState label="Reading telemetry…" />);
    expect(screen.getByText("Reading telemetry…")).toBeInTheDocument();
  });

  it("ErrorState surfaces the error detail and fires onRetry", async () => {
    const onRetry = vi.fn();
    render(<ErrorState error={new Error("ECONNREFUSED")} onRetry={onRetry} label="Couldn't load" />);
    expect(screen.getByText("Couldn't load")).toBeInTheDocument();
    expect(screen.getByText("ECONNREFUSED")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("Async renders children only once resolved", () => {
    const { rerender } = render(
      <Async isLoading isError={false}>
        <div>content</div>
      </Async>,
    );
    expect(screen.queryByText("content")).not.toBeInTheDocument();

    rerender(
      <Async isLoading={false} isError={false}>
        <div>content</div>
      </Async>,
    );
    expect(screen.getByText("content")).toBeInTheDocument();
  });

  it("Async shows an error+retry instead of children when the query failed", async () => {
    const onRetry = vi.fn();
    render(
      <Async isLoading={false} isError error={new Error("boom")} onRetry={onRetry}>
        <div>content</div>
      </Async>,
    );
    expect(screen.queryByText("content")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /Retry/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("OfflineBanner is hidden until show is true", () => {
    const { rerender } = render(
      <I18nProvider>
        <OfflineBanner show={false} />
      </I18nProvider>,
    );
    expect(screen.queryByText(/Connection to the station lost/i)).not.toBeInTheDocument();
    rerender(
      <I18nProvider>
        <OfflineBanner show />
      </I18nProvider>,
    );
    expect(screen.getByText(/Connection to the station lost/i)).toBeInTheDocument();
  });
});
