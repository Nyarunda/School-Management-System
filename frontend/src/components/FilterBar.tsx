import { ReactNode } from "react";
import { Group } from "@mantine/core";

// Deliberately minimal: a labeled slot for server-backed filter controls, dropped into
// DataTable's `toolbar` prop. Grows once a second real consumer demonstrates what's
// actually shared (e.g. a "Clear filters" affordance) -- not before.
export function FilterBar({ children }: { children: ReactNode }) {
  return <Group gap="sm" wrap="wrap">{children}</Group>;
}
