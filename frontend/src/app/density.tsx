import { createContext, ReactNode, useContext, useState } from "react";

export type Density = "comfortable" | "compact";
const STORAGE_KEY = "school-erp-density";
const DensityContext = createContext<{ density: Density; toggleDensity: () => void } | null>(null);

export function DensityProvider({ children }: { children: ReactNode }) {
  const [density, setDensity] = useState<Density>(() => localStorage.getItem(STORAGE_KEY) === "compact" ? "compact" : "comfortable");
  function toggleDensity() {
    const next: Density = density === "comfortable" ? "compact" : "comfortable";
    localStorage.setItem(STORAGE_KEY, next);
    setDensity(next);
  }
  return <DensityContext.Provider value={{ density, toggleDensity }}>{children}</DensityContext.Provider>;
}

export function useDensity() {
  const value = useContext(DensityContext);
  if (!value) throw new Error("DensityProvider is missing");
  return value;
}
