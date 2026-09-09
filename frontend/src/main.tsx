import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MantineProvider, createTheme } from "@mantine/core";
import { Notifications } from "@mantine/notifications";
import { BrowserRouter } from "react-router-dom";
import { AuthProvider } from "./app/auth";
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
const theme=createTheme({primaryColor:"indigo",fontFamily:"Inter, ui-sans-serif, system-ui, sans-serif",defaultRadius:"sm"});

createRoot(document.getElementById("root")!).render(
  <StrictMode><MantineProvider theme={theme}><Notifications position="bottom-center"/><BrowserRouter><QueryClientProvider client={queryClient}><AuthProvider><App /></AuthProvider></QueryClientProvider></BrowserRouter></MantineProvider></StrictMode>,
);
