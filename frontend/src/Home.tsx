import { FormEvent, useEffect, useMemo, useState } from "react";
import { codeFromUrl, PHASE_LABEL } from "./logic";
import type { Game } from "./useGame";
import { loadNickname } from "./useGame";

const CODE_LENGTH = 6;

export default function Home({ game }: { game: Game }) {
  const [nickname, setNickname] = useState(() => loadNickname());
  const [code, setCode] = useState("");
  const [joining, setJoining] = useState(false);
  const deepLink = useMemo(() => codeFromUrl(location.search), []);
  const [deepLinkUsed, setDeepLinkUsed] = useState(false);

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
    <div className="home">
      <header className="home-hero">
        <h1 className="logo">
          Consensus<span>Kill</span>
        </h1>
        <p className="tagline">
          The minority wins. Blend in with the crowd and you lose a life.
        </p>
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
  );
}
