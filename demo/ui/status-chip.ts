/** A green chip requires a fresh, healthy status snapshot. */
export function statusChip(
  data: unknown,
  now = Date.now(),
): { text: string; tone: "live" | "warn" | "off" } {
  if (!data || typeof data !== "object") return { text: "Status unavailable", tone: "off" };
  const value = data as {
    state?: unknown;
    generatedAt?: unknown;
    collector?: { ok?: unknown; staleAfterSeconds?: unknown };
  };
  const state =
    typeof value.state === "string" &&
    ["preparing", "running", "blocked", "review", "complete", "stale"].includes(value.state)
      ? value.state
      : null;
  if (!state) return { text: "Status unavailable", tone: "off" };
  const timestamp = typeof value.generatedAt === "string" ? Date.parse(value.generatedAt) : NaN;
  if (!Number.isFinite(timestamp)) return { text: "Status unavailable", tone: "off" };
  const seconds = value.collector?.staleAfterSeconds;
  const maxAge =
    typeof seconds === "number" && Number.isFinite(seconds) && seconds > 0
      ? seconds * 1000
      : 180000;
  if (now - timestamp > maxAge || timestamp - now > 60000 || state === "stale")
    return { text: "Status · stale", tone: "warn" };
  return {
    text: `Status · ${state}`,
    tone:
      value.collector?.ok === false || state === "blocked" || state === "review"
        ? "warn"
        : state === "running" || state === "complete"
          ? "live"
          : "off",
  };
}
