import { ReactNode } from "react";
import { Group } from "@mantine/core";

// Deliberately minimal: a labeled slot for server-backed filter controls, dropped into
// DataTable's `toolbar` prop. Grows once a second real consumer demonstrates what's
// actually shared (e.g. a "Clear filters" affordance) -- not before.
export function FilterBar({ children }: { children: ReactNode }) {
  // align="end" -- filters mix labeled inputs (label above the box) with unlabeled
  // ones (aria-label only) and the occasional plain button; bottom-aligning keeps
  // every control's input/button box on one line regardless of whether it has a label.
  return <Group gap="sm" wrap="wrap" align="end">{children}</Group>;
}
