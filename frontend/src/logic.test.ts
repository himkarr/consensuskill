import { describe, expect, it } from "vitest";
import {
  canStart,
  clockOffset,
  codeFromUrl,
  displayOrder,
  formatCountdown,
  joinLink,
  livesEmoji,
  PHASE_LABEL,
  remainingWithOffset,
  resultHeadline,
  sideLabel,
} from "./logic";
import type { Player, RoundResult, StateMessage } from "./protocol";

describe("clock sync", () => {
  it("computes offset as server minus local", () => {
    // server says 1000.5s, local clock says 999.5s -> +1s of skew
    expect(clockOffset(1000.5, 999_500)).toBe(1000);
  });

  it("counts down to the server's timer despite local skew", () => {
    const serverNow = 1_000.0;
    const localNow = 990_000; // local clock is 10s behind
    const offset = clockOffset(serverNow, localNow);
    const endsAt = serverNow + 10; // 10 server-seconds left

    expect(remainingWithOffset(endsAt, offset, localNow)).toBe(10_000);
    // 4 local seconds later -> 6s left
    expect(remainingWithOffset(endsAt, offset, localNow + 4_000)).toBe(6_000);
    // after expiry -> clamped at 0
    expect(remainingWithOffset(endsAt, offset, localNow + 60_000)).toBe(0);
  });

  it("untimed phases report 0", () => {
    expect(remainingWithOffset(null, 0, Date.now())).toBe(0);
  });

  it("handles clocks running ahead of the server", () => {
    const offset = clockOffset(1_000.0, 1_005_000); // local 5s ahead -> -5000
    const endsAt = 1_010.0; // 10 server-seconds left
    expect(remainingWithOffset(endsAt, offset, 1_005_000)).toBe(10_000);
  });
});

describe("formatCountdown", () => {
  it("formats minutes and seconds", () => {
    expect(formatCountdown(65_000)).toBe("1:05");
    expect(formatCountdown(9_000)).toBe("0:09");
    expect(formatCountdown(0)).toBe("0:00");
  });

  it("rounds up so 1ms still shows a full second", () => {
    expect(formatCountdown(1)).toBe("0:01");
    expect(formatCountdown(-500)).toBe("0:00");
  });
});

describe("room codes", () => {
  it("extracts ?code= from a URL query", () => {
    expect(codeFromUrl("?code=abc234")).toBe("ABC234");
    expect(codeFromUrl("?other=1")).toBe("");
    expect(codeFromUrl("")).toBe("");
  });

  it("builds a shareable join link", () => {
    expect(joinLink("ABC234", "https://play.example")).toBe(
      "https://play.example/?code=ABC234",
    );
  });
});

describe("displayOrder", () => {
  const host: Player = {
    id: "h",
    nickname: "Zed",
    lives: 2,
    alive: true,
    connected: true,
    is_host: true,
    has_voted: false,
  };
  const alive: Player = { ...host, id: "a", nickname: "Amy", is_host: false };
  const dead: Player = { ...host, id: "d", nickname: "Bob", alive: false, is_host: false };

  it("puts the host first, then alive, then alphabetical", () => {
    const order = displayOrder([dead, alive, host], "h").map((p) => p.id);
    expect(order).toEqual(["h", "a", "d"]);
  });
});

describe("resultHeadline", () => {
  const base: RoundResult = {
    status: "normal",
    losing_side: "A",
    majority_side: "A",
    minority_side: "B",
    abstainers: 0,
    losers: [],
    survivors: [],
  };

  it("describes a normal round", () => {
    expect(resultHeadline(base)).toContain("minority No survives");
  });

  it("describes ties and unanimous voids", () => {
    expect(resultHeadline({ ...base, status: "tie" })).toContain("no lives lost");
    expect(resultHeadline({ ...base, status: "unanimous" })).toContain("voided");
    expect(resultHeadline(null)).toBe("");
  });
});

describe("livesEmoji", () => {
  it("renders hearts and clamps at the max", () => {
    expect(livesEmoji(2)).toBe("♥♥");
    expect(livesEmoji(0)).toBe("—");
    expect(livesEmoji(9, 2)).toBe("♥♥");
  });
});

describe("canStart", () => {
  const make = (over: Partial<StateMessage>): StateMessage =>
    ({
      type: "state",
      room_code: "ABC234",
      phase: "LOBBY",
      round_no: 0,
      timer_ends_at: null,
      server_time: 0,
      question: null,
      players: [],
      counts: null,
      votes: null,
      result: null,
      winners: [],
      chat_log: [],
      min_players: 3,
      starting_lives: 2,
      host_id: "h",
      version: 1,
      you: null,
      ...over,
    }) as StateMessage;

  const players = (n: number) =>
    Array.from({ length: n }, (_, i) => ({
      id: `p${i}`,
      nickname: `p${i}`,
      lives: 2,
      alive: true,
      connected: true,
      is_host: i === 0,
      has_voted: false,
    }));

  it("only the host with enough players in LOBBY or GAME_OVER", () => {
    expect(
      canStart(
        make({
          players: players(3),
          you: {
            player_id: "p0",
            nickname: "p0",
            lives: 2,
            alive: true,
            is_host: true,
            has_voted: false,
            choice: null,
          },
        }),
      ),
    ).toBe(true);

    expect(
      canStart(
        make({
          players: players(2),
          you: {
            player_id: "p0",
            nickname: "p0",
            lives: 2,
            alive: true,
            is_host: true,
            has_voted: false,
            choice: null,
          },
        }),
      ),
    ).toBe(false);

    expect(
      canStart(
        make({
          players: players(3),
          you: {
            player_id: "p1",
            nickname: "p1",
            lives: 2,
            alive: true,
            is_host: false,
            has_voted: false,
            choice: null,
          },
        }),
      ),
    ).toBe(false);

    expect(
      canStart(
        make({
          phase: "VOTE",
          players: players(3),
          you: {
            player_id: "p0",
            nickname: "p0",
            lives: 2,
            alive: true,
            is_host: true,
            has_voted: false,
            choice: null,
          },
        }),
      ),
    ).toBe(false);
  });
});

describe("PHASE_LABEL", () => {
  it("labels every phase", () => {
    for (const phase of [
      "LOBBY",
      "QUESTION",
      "DISCUSSION",
      "VOTE",
      "REVEAL",
      "APPLY",
      "GAME_OVER",
    ] as const) {
      expect(PHASE_LABEL[phase]).toBeTruthy();
    }
  });
});

describe("sideLabel", () => {
  it("maps protocol sides to Yes/No", () => {
    expect(sideLabel("A")).toBe("Yes");
    expect(sideLabel("B")).toBe("No");
    expect(sideLabel(null)).toBe("?");
    expect(sideLabel()).toBe("?");
  });
});
