import { notifications } from "@mantine/notifications";
import { ApiError } from "../../api/client";
import { playSound } from "./notificationSound";

type Kind = "success" | "error" | "warning" | "info";

const COLORS: Record<Kind, string> = { success: "teal", error: "red", warning: "yellow", info: "blue" };
const AUTO_CLOSE_MS: Record<Kind, number> = { success: 4000, error: 6000, warning: 5000, info: 4000 };

function show(kind: Kind, message: string): void {
	notifications.show({ message, color: COLORS[kind], autoClose: AUTO_CLOSE_MS[kind] });
	if (kind === "success" || kind === "error") playSound(kind);
}

// The one and only place this app calls notifications.show()/plays a sound --
// every feature imports notify instead, so message/color/placement/sound stay
// centrally controlled.
export const notify = {
	success: (message: string) => show("success", message),
	// Prefers the real backend message (ApiError.message is already the
	// server's own detail, e.g. "User lacks permission: leave.request.view")
	// over the caller's generic fallback -- a blanket "You do not have
	// permission to perform this action" for every 403 was hiding genuinely
	// useful detail the backend already provided. `message` is only used
	// when there's no error object to read from.
	error: (message: string, error?: unknown) => {
		const text = error instanceof ApiError || error instanceof Error ? error.message : message;
		show("error", text);
	},
	warning: (message: string) => show("warning", message),
	info: (message: string) => show("info", message),
};
