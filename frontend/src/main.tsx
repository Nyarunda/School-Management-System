import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  ActionIcon, Alert, Badge, Button, Card, Drawer, FileInput, Menu, Modal, MultiSelect,
  NumberInput, Pagination, Paper, PasswordInput, Select, Table, Tabs, Textarea, TextInput,
  Tooltip, MantineProvider, createTheme,
} from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./app/auth";
import { DensityProvider } from "./app/density";
import { App } from "./app/App";
import "./styles.css";
import "./workflows.css";
import "./density.css";
import "./attendance.css";
import "./assessments.css";
import "@mantine/core/styles.css";
import "@mantine/notifications/styles.css";
import "./mantine-overrides.css";

const queryClient = new QueryClient({ defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } } });

// Semantic token architecture: neutral scale (gray) and status scales (indigo/teal/yellow/red)
// are overridden here so every Mantine component that resolves a color from the theme —
// borders, dimmed text, badges, alerts, buttons — inherits the ERP palette automatically,
// instead of each screen picking colors ad hoc. `other` carries tokens with no natural home
// in a 10-shade color scale (canvas/surface/sidebar tones).
const theme = createTheme({
  primaryColor: "indigo",
  primaryShade: { light: 6, dark: 6 },
  fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif",
  defaultRadius: "md",
  headings: { fontWeight: "600" },
  colors: {
    indigo: ["#EEF3FF", "#DCE6FF", "#B9CCFF", "#8FACFF", "#6B92FF", "#5B87FF", "#4F7CFF", "#3D64E0", "#2E4EBD", "#22399A"],
    teal: ["#F0FDF9", "#ECFDF5", "#D1FAE5", "#A7F3D0", "#6EE7B7", "#34D399", "#10B981", "#059669", "#047857", "#065F46"],
    yellow: ["#FFFDF5", "#FFFBEB", "#FEF3C7", "#FDE68A", "#FCD34D", "#FBBF24", "#F59E0B", "#D97706", "#B45309", "#92400E"],
    red: ["#FEF5F5", "#FEF2F2", "#FEE2E2", "#FECACA", "#FCA5A5", "#F87171", "#EF4444", "#DC2626", "#B91C1C", "#991B1B"],
    gray: ["#F8FAFC", "#F1F5F9", "#E2E8F0", "#CBD5E1", "#94A3B8", "#64748B", "#475569", "#334155", "#1E293B", "#0F172A"],
  },
  other: {
    canvas: "#F8FAFC",
    surface: "#FFFFFF",
    surfaceSubtle: "#F1F5F9",
    textPrimary: "#0F172A",
    textSecondary: "#475569",
    textMuted: "#64748B",
    borderSubtle: "#F1F5F9",
    borderDefault: "#E2E8F0",
    borderStrong: "#CBD5E1",
    // Light enterprise navigation surface (Business Central-style, not a dark admin dashboard) —
    // hierarchy comes from typography/indentation/the active indicator, not colored fills.
    sidebarBg: "oklch(98.5% 0 0)",
    sidebarForeground: "oklch(20.5% 0 0)",
    sidebarMuted: "oklch(55.6% 0 0)",
    sidebarBorder: "oklch(92.2% 0 0)",
    sidebarHover: "oklch(96.7% 0.003 264)",
    sidebarActive: "oklch(94.5% 0.018 264)",
    sidebarActiveForeground: "oklch(42% 0.16 264)",
  },
  components: {
    Button: Button.extend({ defaultProps: { size: "sm" } }),
    ActionIcon: ActionIcon.extend({ defaultProps: { size: "md" } }),
    TextInput: TextInput.extend({ defaultProps: { size: "sm" } }),
    NumberInput: NumberInput.extend({ defaultProps: { size: "sm" } }),
    Select: Select.extend({ defaultProps: { size: "sm" } }),
    MultiSelect: MultiSelect.extend({ defaultProps: { size: "sm" } }),
    Textarea: Textarea.extend({ defaultProps: { size: "sm" } }),
    PasswordInput: PasswordInput.extend({ defaultProps: { size: "sm" } }),
    FileInput: FileInput.extend({ defaultProps: { size: "sm" } }),
    Card: Card.extend({ defaultProps: { padding: "lg", radius: "md", withBorder: true } }),
    Paper: Paper.extend({ defaultProps: { radius: "md", withBorder: true } }),
    Badge: Badge.extend({ defaultProps: { radius: "sm", variant: "light" } }),
    Table: Table.extend({ defaultProps: { verticalSpacing: "sm", horizontalSpacing: "md" } }),
    Modal: Modal.extend({ defaultProps: { radius: "md", centered: true, overlayProps: { backgroundOpacity: 0.35, blur: 2 } } }),
    Drawer: Drawer.extend({ defaultProps: { padding: "lg" } }),
    Tabs: Tabs.extend({ defaultProps: { variant: "default" } }),
    Menu: Menu.extend({ defaultProps: { radius: "md", shadow: "md" } }),
    Pagination: Pagination.extend({ defaultProps: { size: "sm" } }),
    Tooltip: Tooltip.extend({ defaultProps: { openDelay: 200, radius: "sm" } }),
    Alert: Alert.extend({ defaultProps: { radius: "md", variant: "light" } }),
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode><MantineProvider theme={theme}><Notifications position="bottom-center" /><BrowserRouter><QueryClientProvider client={queryClient}><AuthProvider><DensityProvider><App /></DensityProvider></AuthProvider></QueryClientProvider></BrowserRouter></MantineProvider></StrictMode>,
);
