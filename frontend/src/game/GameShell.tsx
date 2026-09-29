import { QRCodeSVG } from "qrcode.react";
import type { ChatMessage } from "../protocol";
import { formatCountdown, joinLink, livesEmoji, remainingWithOffset } from "../logic";
import type { Game } from "../useGame";
import { useAutoScroll, useTicker } from "./hooks";
import { useState } from "react";

type ViewMode = "projector" | "player";

export default function GameShell({ game }: { game: Game }) {
  const state = game.state!;
  const you = state.you;
  const [view, setView] = useState<ViewMode>(() =>
    you?.is_host ? "projector" : "player",
  );

  if (!state) return null;

  return (
    <div className={`shell view-${view}`}>
      <TopBar game={game} view={view} onView={setView} />
      {game.status !== "open" && (
        <div className="banner">Connection lost — reconnecting…</div>
      )}
      <div className="shell-body">
        <main className="stage">
          <PhaseView game={game} view={view} />
        </main>
        <aside className="side">
          <PlayerGrid game={game} />
          <ChatPanel game={game} />
        </aside>
      </div>
    </div>
  );
}

function TopBar({
  game,
  view,
  onView,
}: {
  game: Game;
  view: ViewMode;
  onView: (v: ViewMode) => void;
}) {
  const state = game.state!;
  const now = useTicker();
  const remaining = remainingWithOffset(state.timer_ends_at, game.offsetRef.current, now);
  const untimed = state.timer_ends_at === null;
  const { copied, copy } = useCopyFeedback();

  return (
    <header className="topbar">
      <div className="topbar-left">
        <button
          className={`room-code${copied ? " copied" : ""}`}
          title="Copy room code"
          onClick={() => copy(state.room_code)}
        >
          {state.room_code}
        </button>
        <span className="phase-pill" data-phase={state.phase}>
          {state.phase.replace("_", " ")}
        </span>
        {state.round_no > 0 && <span className="round">Round {state.round_no}</span>}
      </div>
      <div className="topbar-right">
        {!untimed && (
          <span className={`countdown ${remaining < 3000 ? "urgent" : ""}`}>
            {formatCountdown(remaining)}
          </span>
        )}
        <button className="ghost" onClick={() => onView(view === "projector" ? "player" : "projector")}>
          {view === "projector" ? "Phone view" : "Projector view"}
        </button>
        <span className="muted small instance" title="Gateway instance">
          {game.instanceId}
        </span>
        <button className="ghost" onClick={game.actions.leave}>
          Leave
        </button>
      </div>
    </header>
  );
}

/** Copy text to the clipboard and show a transient "Copied" chip. */
function useCopyFeedback() {
  const [copied, setCopied] = useState(false);
  const copy = (text: string) => {
    navigator.clipboard?.writeText(text).catch(() => {});
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };
  return { copied, copy };
}

function PhaseView({ game, view }: { game: Game; view: ViewMode }) {
  const state = game.state!;
  switch (state.phase) {
    case "LOBBY":
      return <LobbyView game={game} view={view} />;
    case "QUESTION":
    case "DISCUSSION":
      return <QuestionView game={game} view={view} />;
    case "VOTE":
      return <VoteView game={game} view={view} />;
    case "REVEAL":
    case "APPLY":
      return <RevealView game={game} view={view} />;
    case "GAME_OVER":
      return <GameOverView game={game} view={view} />;
  }
}

// ---------------------------------------------------------------------------
// Lobby
// ---------------------------------------------------------------------------
function LobbyView({ game, view }: { game: Game; view: ViewMode }) {
  const state = game.state!;
  const you = state.you;
  const short = state.players.length < state.min_players;
  const { copied, copy } = useCopyFeedback();
  const joinUrl = joinLink(state.room_code);

  return (
    <div className="phase lobby">
      <div className="lobby-hero">
        <p className="muted">Room code</p>
        <button
          className={`big-code${copied ? " copied" : ""}`}
          title="Copy room code"
          onClick={() => copy(state.room_code)}
        >
          {state.room_code}
        </button>
        <div className="qr-card">
          <QRCodeSVG value={joinUrl} size={view === "projector" ? 200 : 132} bgColor="#f4f6f8" fgColor="#0e1013" level="M" />
        </div>
        <p className="join-url">{joinUrl}</p>
      </div>

      <div className="lobby-panel card">
        <h2>
          {state.players.length}/{state.min_players}+ players
        </h2>
        {short && <p className="muted">Need {state.min_players - state.players.length} more to start.</p>}

        {you?.is_host ? (
          <button
            className="primary"
            disabled={short}
            onClick={game.actions.hostStart}
          >
            {short ? "Waiting for players…" : "Start game"}
          </button>
        ) : (
          <p className="muted">Waiting for the host to start…</p>
        )}

        <ul className="lobby-list">
          {state.players.map((p) => (
            <li key={p.id}>
              <span className={p.connected ? "dot on" : "dot"} />
              <span className="avatar" aria-hidden="true">
                {p.nickname.slice(0, 1)}
              </span>
              <span className="name">
                {p.nickname}
                {p.id === you?.player_id ? " (you)" : ""}
              </span>
              {p.is_host && <span className="badge">host</span>}
              {you?.is_host && !p.is_host && (
                <button
                  className="ghost tiny"
                  onClick={() => game.actions.hostKick(p.id)}
                >
                  kick
                </button>
              )}
            </li>
          ))}
        </ul>

        {you?.is_host && (
          <button className="danger ghost" onClick={() => game.actions.killInstance()}>
            Simulate gateway crash
          </button>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Question / discussion
// ---------------------------------------------------------------------------
function QuestionView({ game, view }: { game: Game; view: ViewMode }) {
  const state = game.state!;
  const question = state.question;
  const big = view === "projector";

  return (
    <div className={`phase question ${big ? "big" : ""}`}>
      <p className="muted">
        {state.phase === "QUESTION" ? "Get ready to choose…" : "Discuss. Bluff. Persuade."}
      </p>
      <h1 className="question-text">{question?.text ?? "…"}</h1>
      <div className="options">
        <div className="option a">
          <span className="opt-key">A</span>
          <span>{question?.option_a}</span>
        </div>
        <div className="option b">
          <span className="opt-key">B</span>
          <span>{question?.option_b}</span>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Vote
// ---------------------------------------------------------------------------
function VoteView({ game, view }: { game: Game; view: ViewMode }) {
  const state = game.state!;
  const you = state.you;
  const question = state.question;
  const big = view === "projector";

  if (!you?.alive) {
    return (
      <div className="phase vote">
        <h2>Spectating</h2>
        <p className="muted">You are out of lives — watch the drama unfold.</p>
        <Countdown game={game} />
      </div>
    );
  }

  const voted = you.has_voted;
  const eligible = state.players.filter((p) => p.alive).length;
  const cast = state.players.filter((p) => p.has_voted).length;

  return (
    <div className={`phase vote ${big ? "big" : ""}`}>
      <h2>Cast your vote</h2>
      <p className="muted small">
        {cast}/{eligible} voted
      </p>
      <div className="vote-buttons">
        <button
          className={`vote-btn a ${voted && you.choice === "A" ? "chosen" : ""}`}
          disabled={voted}
          onClick={() => game.actions.vote("A")}
        >
          <span className="opt-key">A</span>
          {question?.option_a}
        </button>
        <button
          className={`vote-btn b ${voted && you.choice === "B" ? "chosen" : ""}`}
          disabled={voted}
          onClick={() => game.actions.vote("B")}
        >
          <span className="opt-key">B</span>
          {question?.option_b}
        </button>
      </div>
      {voted ? (
        <p className="vote-locked">
          Vote locked: <strong>{you.choice}</strong> — no take-backs.
        </p>
      ) : (
        <p className="muted small">Only your first vote counts. No take-backs.</p>
      )}
      <Countdown game={game} />
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reveal / apply
// ---------------------------------------------------------------------------
function RevealView({ game, view }: { game: Game; view: ViewMode }) {
  const state = game.state!;
  const counts = state.counts ?? { A: 0, B: 0 };
  const total = counts.A + counts.B || 1;
  const result = state.result;
  const big = view === "projector";
  const eliminated = new Set(
    state.players.filter((p) => !p.alive).map((p) => p.id),
  );

  return (
    <div className={`phase reveal ${big ? "big" : ""}`}>
      <h2 className="reveal-headline">
        {result?.status === "unanimous"
          ? "Unanimous — round voided"
          : result?.status === "tie"
            ? "Tie — no lives lost"
            : `Minority ${result?.minority_side} survives!`}
      </h2>

      <div className="bars">
        <Bar label="A" value={counts.A} total={total} winner={result?.minority_side === "A"} />
        <Bar label="B" value={counts.B} total={total} winner={result?.minority_side === "B"} />
      </div>

      {state.phase === "APPLY" && (
        <ul className="casualties">
          {state.players.map((p) => (
            <li
              key={p.id}
              className={`${eliminated.has(p.id) ? "dead" : ""} ${
                result?.losers?.includes(p.id) ? "lost-life" : ""
              }`}
            >
              {p.nickname}
              <span className="lives">{livesEmoji(p.lives)}</span>
            </li>
          ))}
        </ul>
      )}

      <Countdown game={game} />
    </div>
  );
}

function Bar({
  label,
  value,
  total,
  winner,
}: {
  label: string;
  value: number;
  total: number;
  winner: boolean;
}) {
  const pct = Math.round((value / total) * 100);
  return (
    <div className={`bar-row ${winner ? "winner" : ""}`}>
      <span className="opt-key">{label}</span>
      <div className="bar-track">
        <div className={`bar-fill ${label.toLowerCase()}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="bar-count">
        {value} ({pct}%)
      </span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Game over
// ---------------------------------------------------------------------------
function GameOverView({ game, view }: { game: Game; view: ViewMode }) {
  const state = game.state!;
  const winners = new Set(state.winners);
  const winningPlayers = state.players.filter((p) => winners.has(p.id));
  const you = state.you;

  return (
    <div className={`phase gameover ${view === "projector" ? "big" : ""}`}>
      <h1 className="logo">
        {state.winners.length === 0 ? "Draw!" : "Game over"}
      </h1>
      {winningPlayers.length > 0 && (
        <p className="winners">
          {winningPlayers.map((p) => p.nickname).join(", ")}{" "}
          {winningPlayers.length === 1 ? "wins" : "win"}
          {you && winners.has(you.player_id) ? " — including you!" : ""}
        </p>
      )}
      {state.winners.length === 0 && (
        <p className="muted">Nobody survived. The crowd ate itself.</p>
      )}

      <div className="final-list">
        {state.players.map((p) => (
          <li key={p.id} className={winners.has(p.id) ? "champ" : ""}>
            <span>{p.nickname}</span>
            <span className="lives">{livesEmoji(p.lives)}</span>
          </li>
        ))}
      </div>

      {you?.is_host && (
        <button className="primary" onClick={game.actions.hostStart}>
          Play again
        </button>
      )}
      {!you?.is_host && <p className="muted">Waiting for the host…</p>}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Sidebar
// ---------------------------------------------------------------------------
function PlayerGrid({ game }: { game: Game }) {
  const state = game.state!;
  const you = state.you;
  const sorted = [...state.players].sort((a, b) => {
    if (a.is_host !== b.is_host) return a.is_host ? -1 : 1;
    if (a.alive !== b.alive) return a.alive ? -1 : 1;
    return a.nickname.localeCompare(b.nickname);
  });

  return (
    <section className="players card">
      <h3>Players</h3>
      <ul>
        {sorted.map((p) => (
          <li
            key={p.id}
            className={`${p.alive ? "" : "out"} ${p.id === you?.player_id ? "me" : ""}`}
            title={p.connected ? "Online" : "Disconnected"}
          >
            <span className="avatar" aria-hidden="true">
              {p.nickname.slice(0, 1)}
            </span>
            <span className={p.connected ? "dot on" : "dot"} />
            <span className="name">{p.nickname}</span>
            {p.is_host && <span className="badge">host</span>}
            {p.has_voted && state.phase === "VOTE" && <span className="badge voted">✓</span>}
            <span className="lives">{livesEmoji(p.lives)}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

function ChatPanel({ game }: { game: Game }) {
  const state = game.state!;
  const [draft, setDraft] = useState("");
  const ref = useAutoScroll<HTMLUListElement>(game.chats.length);
  const chatEnabled = state.phase !== "VOTE";

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text) return;
    game.actions.sendChat(text);
    setDraft("");
  };

  return (
    <section className="chat card">
      <h3>Chat</h3>
      <ul ref={ref} className="chat-log">
        {game.chats.map((m: ChatMessage, i) => (
          <li
            key={`${m.ts}-${i}`}
            className={m.player_id === state.you?.player_id ? "mine" : ""}
          >
            <span className="who">{m.nickname}</span> {m.text}
          </li>
        ))}
        {game.chats.length === 0 && <li className="muted">Say something bold…</li>}
      </ul>
      <form onSubmit={submit} className="chat-form">
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={chatEnabled ? "Chat…" : "Chat locked while voting"}
          maxLength={200}
          disabled={!chatEnabled}
        />
        <button className="primary" type="submit" disabled={!chatEnabled || !draft.trim()}>
          Send
        </button>
      </form>
    </section>
  );
}

function Countdown({ game }: { game: Game }) {
  const state = game.state!;
  const now = useTicker();
  const remaining = remainingWithOffset(state.timer_ends_at, game.offsetRef.current, now);
  if (state.timer_ends_at === null) return null;
  return (
    <div className={`countdown-lg ${remaining < 3000 ? "urgent" : ""}`}>
      {formatCountdown(remaining)}
    </div>
  );
}
