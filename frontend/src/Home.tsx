import { FormEvent, useEffect, useMemo, useState } from "react";
import { codeFromUrl, PHASE_LABEL } from "./logic";
import type { Game } from "./useGame";
import { loadNickname } from "./useGame";

const CODE_LENGTH = 6;

const NOTES = [
  "Two lives. Loud opinions are a liability.",
  "The crowd is always wrong — that's the point.",
  "Nobody here votes honestly. That's the fun.",
  "Quiet players win loud rounds.",
  "Majority loses a life. Blend in at your own risk.",
  "Chat is a weapon. Lies are allowed.",
];

/** Margin-doodle: a little crowd where exactly one figure stands apart. */
function CrowdDoodle() {
  return (
    <svg className="doodle" viewBox="0 0 210 62" fill="none" aria-hidden="true">
      <g transform="translate(30 5) rotate(-4)">
        <circle cx="0" cy="7" r="6" />
        <path d="M0 13 L1 31" />
        <path d="M0 17 L-8 21" />
        <path d="M0 17 L8 20" />
        <path d="M1 31 L-5 43" />
        <path d="M1 31 L7 43" />
      </g>
      <g transform="translate(78 4) rotate(3)">
        <circle cx="0" cy="7" r="6" />
        <path d="M0 13 L-1 31" />
        <path d="M0 17 L-8 13" />
        <path d="M0 17 L8 22" />
        <path d="M-1 31 L-7 44" />
        <path d="M-1 31 L6 43" />
      </g>
      <g className="minority" transform="translate(126 5) rotate(-2)">
        <circle cx="0" cy="7" r="6" />
        <path d="M0 13 L1 31" />
        <path d="M0 17 L-7 10" />
        <path d="M0 17 L7 10" />
        <path d="M1 31 L-5 44" />
        <path d="M1 31 L7 43" />
      </g>
      <path className="spark" d="M117 0 L119 4" />
      <path className="spark" d="M134 0 L133 4" />
      <g transform="translate(174 6) rotate(4)">
        <circle cx="0" cy="7" r="6" />
        <path d="M0 13 L1 31" />
        <path d="M0 17 L-8 20" />
        <path d="M0 17 L8 21" />
        <path d="M1 31 L-5 43" />
        <path d="M1 31 L7 44" />
      </g>
      <path className="ground" d="M8 56 Q54 53 104 56 T202 55" />
    </svg>
  );
}

/** Hand-drawn underline to sit beneath the wordmark. */
function Squiggle() {
  return (
    <svg className="squiggle" viewBox="0 0 156 12" fill="none" aria-hidden="true">
      <path
        d="M4 7 q14 -5 28 0 t28 0 t28 0 t28 0 t28 0"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
      />
    </svg>
  );
}

/** Top-left margin doodle: tally marks counting the rounds you lost. */
function TallyDoodle() {
  return (
    <svg className="decor-item decor-tally" viewBox="0 0 110 80" fill="none" aria-hidden="true">
      <g className="ink">
        <path d="M14 18 L17 58" />
        <path d="M30 16 L31 57" />
        <path d="M46 19 L44 58" />
        <path d="M62 17 L61 56" />
        <path d="M8 54 L68 21" />
      </g>
      <g className="ink-mint">
        <path d="M90 14 L90 32" />
        <path d="M81 23 L99 23" />
        <path d="M83 16 L97 30" />
        <path d="M97 16 L83 30" />
      </g>
    </svg>
  );
}

/** Right margin: a scribbled arrow pointing at the form. */
function ArrowNote() {
  return (
    <svg className="decor-item decor-arrow" viewBox="0 0 175 105" fill="none" aria-hidden="true">
      <text className="decor-label" x="172" y="22" textAnchor="end">
        start here
      </text>
      <path className="ink-mint" d="M160 40 C128 48 86 58 54 86" />
      <path className="ink-mint" d="M54 86 L74 83" />
      <path className="ink-mint" d="M54 86 L61 68" />
    </svg>
  );
}

/** Bottom-right margin: a hand-drawn ballot with option B ticked. */
function BallotDoodle() {
  return (
    <svg className="decor-item decor-ballot" viewBox="0 0 130 64" fill="none" aria-hidden="true">
      <path
        className="ink"
        d="M8 10 h44 q6 0 6 6 v32 q0 6 -6 6 h-46 q-6 0 -6 -6 v-32 q0 -6 6 -6 z"
      />
      <text className="decor-key" x="30" y="40" textAnchor="middle">
        A
      </text>
      <path
        className="ink"
        d="M78 10 h44 q6 0 6 6 v32 q0 6 -6 6 h-44 q-6 0 -6 -6 v-32 q0 -6 6 -6 z"
      />
      <path className="ink-mint" d="M92 30 l7 9 l17 -20" />
    </svg>
  );
}

export default function Home({ game }: { game: Game }) {
  const [nickname, setNickname] = useState(() => loadNickname());
  const [code, setCode] = useState("");
  const [joining, setJoining] = useState(false);
  const deepLink = useMemo(() => codeFromUrl(location.search), []);
  const [deepLinkUsed, setDeepLinkUsed] = useState(false);
  const [note] = useState(() => NOTES[Math.floor(Math.random() * NOTES.length)]);

  useEffect(() => {
    if (deepLink && !deepLinkUsed) {
      setCode(deepLink);
      setJoining(true);
      setDeepLinkUsed(true);
    }
  }, [deepLink, deepLinkUsed]);

  const nicknameOk = nickname.trim().length > 0 && nickname.trim().length <= 16;
  const codeOk = code.trim().length === CODE_LENGTH;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!nicknameOk) return;
    if (joining) {
      if (codeOk) game.actions.joinRoom(code, nickname.trim());
    } else {
      game.actions.createRoom(nickname.trim());
    }
  };

  return (
    <div className="home-page">
      <div className="decor" aria-hidden="true">
        <TallyDoodle />
        <ArrowNote />
        <div className="decor-item decor-crowd">
          <CrowdDoodle />
        </div>
        <BallotDoodle />
      </div>
      <div className="home">
        <header className="home-hero">
          <h1 className="logo">
            Consensus<span>Kill</span>
          </h1>
          <p className="tagline">
            The minority wins. Blend in with the crowd and you lose a life.
          </p>
          <Squiggle />
          <p className="home-note">{note}</p>
        </header>

        <form className="card home-form" onSubmit={submit}>
          <label className="field">
            <span>Nickname</span>
            <input
              value={nickname}
              onChange={(e) => setNickname(e.target.value)}
              placeholder="e.g. Fox"
              maxLength={16}
              autoFocus
              autoComplete="nickname"
            />
          </label>

          <div className="mode-switch">
            <button
              type="button"
              className={!joining ? "active" : ""}
              onClick={() => setJoining(false)}
            >
              Create room
            </button>
            <button
              type="button"
              className={joining ? "active" : ""}
              onClick={() => setJoining(true)}
            >
              Join room
            </button>
          </div>

          {joining && (
            <label className="field">
              <span>Room code</span>
              <input
                value={code}
                onChange={(e) => setCode(e.target.value.toUpperCase().slice(0, CODE_LENGTH))}
                placeholder="ABC234"
                className="code-input"
                maxLength={CODE_LENGTH}
                autoCapitalize="characters"
                spellCheck={false}
              />
            </label>
          )}

          <button
            className="primary"
            type="submit"
            disabled={!nicknameOk || (joining && !codeOk)}
          >
            {joining ? "Join" : "Create"}
          </button>

          <p className="muted small">{game.status === "open" ? "Connected" : "Connecting…"}</p>
          {deepLink && (
            <p className="muted small">
              Invited to room <strong>{deepLink}</strong> — enter a nickname and join.
            </p>
          )}
        </form>

        <footer className="home-foot muted small">
          {Object.entries(PHASE_LABEL)
            .slice(0, 6)
            .map(([key, label]) => (
              <span key={key}>{label}</span>
            ))}
        </footer>
      </div>
    </div>
  );
}
