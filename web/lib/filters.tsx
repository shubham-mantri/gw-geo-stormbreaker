"use client";

import {
  createContext,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

/**
 * Shared dashboard filter store: the currently-selected **brand**, **date
 * range**, and **engine**. These are the three controls in the top bar and
 * every screen reads them so filtering is consistent app-wide.
 *
 * Tenant is deliberately NOT here — it is derived from the auth token by the
 * backend and is read-only on the client (ui-spec §5, see `lib/auth.ts`).
 */

/** Default date range on first load (matches the top-bar default). */
export const DEFAULT_RANGE = "30d";
/** Sentinel engine value meaning "no engine filter" (show all engines). */
export const ALL_ENGINES = "all";

export type FiltersState = {
  /** Selected brand id, or null until one is chosen / auto-selected. */
  brandId: string | null;
  /** Date-range token, e.g. "7d" | "30d" | "90d" | "qtd". */
  range: string;
  /** Engine filter token, e.g. "all" | "chatgpt" | "perplexity" | … */
  engine: string;
};

export type FiltersContextValue = FiltersState & {
  setBrandId: (brandId: string | null) => void;
  setRange: (range: string) => void;
  setEngine: (engine: string) => void;
};

const FiltersContext = createContext<FiltersContextValue | null>(null);

export type FiltersProviderProps = {
  children: ReactNode;
  /** Optional seed values (used by tests and for deep-links). */
  initialBrandId?: string | null;
  initialRange?: string;
  initialEngine?: string;
};

export function FiltersProvider({
  children,
  initialBrandId = null,
  initialRange = DEFAULT_RANGE,
  initialEngine = ALL_ENGINES,
}: FiltersProviderProps) {
  const [brandId, setBrandId] = useState<string | null>(initialBrandId);
  const [range, setRange] = useState<string>(initialRange);
  const [engine, setEngine] = useState<string>(initialEngine);

  const value = useMemo<FiltersContextValue>(
    () => ({ brandId, range, engine, setBrandId, setRange, setEngine }),
    [brandId, range, engine],
  );

  return (
    <FiltersContext.Provider value={value}>{children}</FiltersContext.Provider>
  );
}

/** Read + update the shared brand / date-range / engine filters. */
export function useFilters(): FiltersContextValue {
  const ctx = useContext(FiltersContext);
  if (ctx === null) {
    throw new Error("useFilters must be used within a <FiltersProvider>");
  }
  return ctx;
}
