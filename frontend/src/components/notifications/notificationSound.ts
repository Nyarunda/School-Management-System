const STORAGE_KEY = "school-erp-sound-enabled";

export function isSoundEnabled(): boolean {
	try {
		return localStorage.getItem(STORAGE_KEY) === "true";
	} catch {
		return false;
	}
}

export function setSoundEnabled(enabled: boolean): void {
	try {
		localStorage.setItem(STORAGE_KEY, String(enabled));
	} catch {
		// Private browsing / storage disabled -- the toggle just won't persist.
	}
}

let audioContext: AudioContext | null = null;

// A short oscillator tone rather than an embedded audio asset -- nothing to
// source, ship, or maintain. Wrapped in try/catch throughout: sound is a
// nice-to-have, never something that can throw into the UI (browsers
// routinely refuse AudioContext before any user interaction, which is an
// expected, silent no-op here, not an error).
function tone(frequency: number, durationSeconds: number): void {
	try {
		audioContext ??= new AudioContext();
		const oscillator = audioContext.createOscillator();
		const gain = audioContext.createGain();
		oscillator.frequency.value = frequency;
		gain.gain.value = 0.04;
		oscillator.connect(gain).connect(audioContext.destination);
		oscillator.start();
		gain.gain.exponentialRampToValueAtTime(0.0001, audioContext.currentTime + durationSeconds);
		oscillator.stop(audioContext.currentTime + durationSeconds);
	} catch {
		// WebAudio unavailable or blocked -- ignore.
	}
}

export function playSound(kind: "success" | "error"): void {
	if (!isSoundEnabled()) return;
	if (kind === "success") tone(880, 0.12);
	else tone(220, 0.18);
}
