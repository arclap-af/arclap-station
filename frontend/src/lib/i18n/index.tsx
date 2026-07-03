import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { en } from "./en";
import { de } from "./de";
import { fr } from "./fr";

/**
 * Minimal, dependency-free i18n for the cockpit.
 *
 * The UI is fixed-string, so a full ICU library (react-intl / i18next,
 * ~40 KB) would be weight we just cut from the bundle. This gives the
 * essentials — nested-key lookup, {var} interpolation, English fallback,
 * persisted locale — in ~1 KB. `t()` keeps the same shape a library
 * would use, so migrating later (if plural/gender rules are needed) is a
 * drop-in. Catalogs are plain string maps keyed by dotted paths.
 */

export type Locale = "en" | "de" | "fr";

export const LOCALES: ReadonlyArray<{ code: Locale; label: string }> = [
  { code: "en", label: "English" },
  { code: "de", label: "Deutsch" },
  { code: "fr", label: "Français" },
];

const CATALOGS: Record<Locale, Record<string, string>> = { en, de, fr };
const STORAGE_KEY = "arclap.locale";

function isLocale(v: unknown): v is Locale {
  return v === "en" || v === "de" || v === "fr";
}

function detectInitial(): Locale {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (isLocale(saved)) return saved;
    const nav = (navigator.language || "en").slice(0, 2).toLowerCase();
    if (isLocale(nav)) return nav;
  } catch {
    // localStorage/navigator unavailable (SSR, locked-down browser) — fall through.
  }
  return "en";
}

export type TFunc = (key: string, vars?: Record<string, string | number>) => string;

interface I18nValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: TFunc;
}

const I18nContext = createContext<I18nValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(detectInitial);

  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const value = useMemo<I18nValue>(() => {
    const setLocale = (l: Locale) => {
      try {
        localStorage.setItem(STORAGE_KEY, l);
      } catch {
        // Persisting is best-effort; the in-memory switch still works.
      }
      setLocaleState(l);
    };
    const t: TFunc = (key, vars) => {
      const raw = CATALOGS[locale][key] ?? CATALOGS.en[key] ?? key;
      if (!vars) return raw;
      return raw.replace(/\{(\w+)\}/g, (_m, k: string) =>
        k in vars ? String(vars[k]) : `{${k}}`,
      );
    };
    return { locale, setLocale, t };
  }, [locale]);

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within an I18nProvider");
  return ctx;
}
