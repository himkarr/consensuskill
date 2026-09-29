import { useEffect, useState } from "react";
import GameShell from "./game/GameShell";
import Home from "./Home";
import { loadNickname, useGame } from "./useGame";

export default function App() {
  const game = useGame();
  const [booting, setBooting] = useState(() => Boolean(loadNickname()));

  // Cold load with a stored nickname? The socket reconnect is already in
  // flight from useGame; give it a moment before showing the home screen.
  useEffect(() => {
    const timer = setTimeout(() => setBooting(false), 1500);
    return () => clearTimeout(timer);
  }, []);

  let screen;
  if (game.session && game.state) {
    screen = <GameShell game={game} />;
  } else if (booting && game.status !== "closed") {
    screen = (
      <div className="centered">
        <div className="logo">ConsensusKill</div>
        <div className="muted">Reconnecting…</div>
      </div>
    );
  } else {
    screen = <Home game={game} />;
  }

  return (
    <div className="app">
      {screen}
      {game.notice && (
        <div className={`notice notice-${game.notice.kind}`} role="alert">
          <span>{game.notice.text}</span>
          <button onClick={game.dismissNotice} aria-label="Dismiss">
            ×
          </button>
        </div>
      )}
    </div>
  );
}
