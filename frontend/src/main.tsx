import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { App } from "./App";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { I18nProvider } from "./lib/i18n";
import "./styles/base.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // On-device API is local, but a small stale time helps the WS-driven views.
      staleTime: 5_000,
      refetchOnWindowFocus: false,
      retry: 1,
    },
  },
});

const rootEl = document.getElementById("root");
if (!rootEl) {
  throw new Error("Arclap Station: missing #root in index.html");
}

createRoot(rootEl).render(
  <StrictMode>
    <ErrorBoundary>
      <I18nProvider>
        <QueryClientProvider client={queryClient}>
          <App />
        </QueryClientProvider>
      </I18nProvider>
    </ErrorBoundary>
  </StrictMode>,
);
