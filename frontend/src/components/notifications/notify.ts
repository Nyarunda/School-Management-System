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
	// A permission-denied response is always shown as such, regardless of the
	// domain-specific message the call site passed in.
	error: (message: string, error?: unknown) => {
		const text = error instanceof ApiError && error.status === 403 ? "You do not have permission to perform this action" : message;
		show("error", text);
	},
	warning: (message: string) => show("warning", message),
	info: (message: string) => show("info", message),
};
