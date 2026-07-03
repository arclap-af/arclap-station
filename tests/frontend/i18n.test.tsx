import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { I18nProvider, useI18n } from "../../frontend/src/lib/i18n";

function Probe() {
  const { t, locale, setLocale } = useI18n();
  return (
    <div>
      <span data-testid="loc">{locale}</span>
      <span data-testid="home">{t("nav.home")}</span>
      <span data-testid="missing">{t("does.not.exist")}</span>
      <button onClick={() => setLocale("de")}>de</button>
      <button onClick={() => setLocale("fr")}>fr</button>
    </div>
  );
}

describe("i18n", () => {
  beforeEach(() => {
    try {
      localStorage.clear();
    } catch {
      /* jsdom always has localStorage; guard anyway */
    }
  });

  it("defaults to English and looks up keys", () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );
    expect(screen.getByTestId("loc")).toHaveTextContent("en");
    expect(screen.getByTestId("home")).toHaveTextContent("Home");
  });

  it("switches locale and re-resolves keys (de → fr)", async () => {
    const user = userEvent.setup();
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );
    await user.click(screen.getByRole("button", { name: "de" }));
    expect(screen.getByTestId("loc")).toHaveTextContent("de");
    expect(screen.getByTestId("home")).toHaveTextContent("Start");
    expect(localStorage.getItem("arclap.locale")).toBe("de");

    await user.click(screen.getByRole("button", { name: "fr" }));
    expect(screen.getByTestId("home")).toHaveTextContent("Accueil");
  });

  it("returns the key itself for an unknown message (graceful fallback)", () => {
    render(
      <I18nProvider>
        <Probe />
      </I18nProvider>,
    );
    expect(screen.getByTestId("missing")).toHaveTextContent("does.not.exist");
  });

  it("useI18n throws outside a provider", () => {
    const err = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow(/I18nProvider/);
    err.mockRestore();
  });

  it("every German and French catalog key exists in English (no orphans)", async () => {
    const { en } = await import("../../frontend/src/lib/i18n/en");
    const { de } = await import("../../frontend/src/lib/i18n/de");
    const { fr } = await import("../../frontend/src/lib/i18n/fr");
    const enKeys = new Set(Object.keys(en));
    for (const k of Object.keys(de)) expect(enKeys.has(k), `de key ${k}`).toBe(true);
    for (const k of Object.keys(fr)) expect(enKeys.has(k), `fr key ${k}`).toBe(true);
    // And every English key is translated in both (no missing coverage).
    for (const k of enKeys) {
      expect(de[k], `de missing ${k}`).toBeTruthy();
      expect(fr[k], `fr missing ${k}`).toBeTruthy();
    }
  });
});
