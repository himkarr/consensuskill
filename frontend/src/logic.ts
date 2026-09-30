/**
 * Pure helpers — everything here is unit-tested in clock.test.ts.
 */

import type { Choice, Phase, Player, RoundResult, StateMessage } from "./protocol";

/** Milliseconds of clock skew between this client and the server. */
export function clockOffset(serverTime: number, localTime: number): number {
  return serverTime * 1000 - localTime;
}

/**
 * Remaining phase time in ms, clamped at 0.
 *
 * `timerEndsAt` and `serverTime` are server epoch seconds (from a state
 * message); `localTime` is `Date.now()`. `offsetMs` corrects our clock to the
 * server's (see `clockOffset`).
 */
export function remainingWithOffset(
  timerEndsAt: number | null,
  offsetMs: number,
  localTime: number,
): number {
  if (timerEndsAt === null) return 0;
  return Math.max(0, timerEndsAt * 1000 - (localTime + offsetMs));
}

/** "1:05" / "0:09" for countdown displays. */
export function formatCountdown(ms: number): string {
  const total = Math.ceil(Math.max(0, ms) / 1000);
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${seconds.toString().padStart(2, "0")}`;
}

export const PHASE_LABEL: Record<Phase, string> = {
  LOBBY: "Lobby",
  QUESTION: "Question",
  DISCUSSION: "Discussion",
  VOTE: "Vote",
  REVEAL: "Reveal",
  APPLY: "Lives",
  GAME_OVER: "Game over",
};

/** Join link for the QR code on the projector view. */
export function joinLink(roomCode: string, baseUrl?: string): string {
  const base = baseUrl ?? (typeof location !== "undefined" ? location.origin : "");
  return `${base}/?code=${roomCode}`;
}

/** Read `?code=XXXXXX` from the current URL (deep-link join). */
export function codeFromUrl(search: string): string {
  const value = new URLSearchParams(search).get("code");
  return value ? value.trim().toUpperCase() : "";
}

/** Players sorted for display: host first, then alive before spectators. */
export function displayOrder(players: Player[], hostId: string): Player[] {
  return [...players].sort((a, b) => {
    if (a.id === hostId) return -1;
    if (b.id === hostId) return 1;
    if (a.alive !== b.alive) return a.alive ? -1 : 1;
    return a.nickname.localeCompare(b.nickname);
  });
}

/** Human label for a vote side — the game is binary Yes/No. */
export function sideLabel(side?: Choice | null): string {
  if (side == null) return "?";
  return side === "A" ? "Yes" : "No";
}

/** Headline for the reveal screen. */
export function resultHeadline(result: RoundResult | null): string {
  if (!result) return "";
  switch (result.status) {
    case "unanimous":
      return "Unanimous — round voided, nobody loses a life";
    case "tie":
      return "Tie — no lives lost";
    default:
      return `Majority ${sideLabel(result.majority_side)} loses a life — minority ${sideLabel(
        result.minority_side,
      )} survives`;
  }
}

export function livesEmoji(lives: number, max = 2): string {
  return "♥".repeat(Math.max(0, Math.min(lives, max))) || "—";
}

export function canStart(state: StateMessage): boolean {
  if (!state.you?.is_host) return false;
  if (state.phase === "LOBBY" || state.phase === "GAME_OVER") {
    return state.players.length >= state.min_players;
  }
  return false;
}
