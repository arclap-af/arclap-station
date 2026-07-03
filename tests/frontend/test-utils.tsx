import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";

import { I18nProvider } from "../../frontend/src/lib/i18n";

interface Options extends RenderOptions {
  route?: string;
}

export function renderWithProviders(ui: ReactElement, opts: Options = {}) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const initialEntries = opts.route ? [opts.route] : ["/"];
  // Mirror the real provider stack in main.tsx so any component that
  // calls useI18n (nav, Home, Settings…) renders under test.
  return render(
    <I18nProvider>
      <MemoryRouter initialEntries={initialEntries}>
        <QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>
      </MemoryRouter>
    </I18nProvider>,
    opts,
  );
}
