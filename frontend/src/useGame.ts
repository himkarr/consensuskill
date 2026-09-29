/**
 * useGame — the single WebSocket session for the app.
 *
 * Handles: connect, handshake, auto-reconnect with the player token, clock
 * offset tracking, and normalizing the server's message stream into state the
 * views can render.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ChatMessage,
  ClientMessage,
  ServerMessage,
  StateMessage,
} from "./protocol";

export type ConnectionStatus = "connecting" | "open" | "closed";

export interface Session {
  playerId: string;
  token: string;
  roomCode: string;
  isHost: boolean;
}

const TOKEN_KEY = "ck.token";
const NICK_KEY = "ck.nickname";

function wsUrl(): string {
  const explicit = import.meta.env.VITE_WS_URL as string | undefined;
  if (explicit) return explicit;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.host}/ws`;
}

function loadToken(): string {
  try {
    return localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveToken(token: string): void {
  try {
    token ? localStorage.setItem(TOKEN_KEY, token) : localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* private mode */
  }
}

export function loadNickname(): string {
  try {
    return localStorage.getItem(NICK_KEY) ?? "";
  } catch {
    return "";
  }
}

function saveNickname(nick: string): void {
  try {
    localStorage.setItem(NICK_KEY, nick);
  } catch {
    /* private mode */
  }
}

export interface Notice {
  id: number;
  kind: "error" | "info";
  text: string;
}

export function useGame() {
  const [status, setStatus] = useState<ConnectionStatus>("connecting");
  const [instanceId, setInstanceId] = useState<string>("");
  const [session, setSession] = useState<Session | null>(null);
  const [state, setState] = useState<StateMessage | null>(null);
  const [chats, setChats] = useState<ChatMessage[]>([]);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [pending, setPending] = useState<ClientMessage | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const offsetRef = useRef(0);
  const noticeId = useRef(0);
  const pendingRef = useRef<ClientMessage | null>(null);
  const joinedOnce = useRef(false);

  pendingRef.current = pending;

  const pushNotice = useCallback((kind: "error" | "info", text: string) => {
    noticeId.current += 1;
    setNotice({ id: noticeId.current, kind, text });
  }, []);

  const dismissNotice = useCallback(() => setNotice(null), []);

  const send = useCallback((message: ClientMessage) => {
    const ws = wsRef.current;
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(message));
      return true;
    }
    setPending(message); // flushed on open
    return false;
  }, []);

  const handleServerMessage = useCallback(
    (message: ServerMessage) => {
      switch (message.type) {
        case "instance_info":
          setInstanceId(message.instance_id);
          offsetRef.current = message.server_time * 1000 - Date.now();
          break;
        case "joined": {
          const next: Session = {
            playerId: message.player_id,
            token: message.token,
            roomCode: message.room_code,
            isHost: message.is_host,
          };
          setSession(next);
          saveToken(message.token);
          joinedOnce.current = true;
          break;
        }
        case "state":
          offsetRef.current = message.server_time * 1000 - Date.now();
          setState(message);
          setChats(message.chat_log);
          break;
        case "chat":
          setChats((prev) => {
            const next = [...prev, message];
            return next.slice(Math.max(0, next.length - 50));
          });
          break;
        case "error":
          if (
            message.code === "invalid_token" ||
            message.code === "kicked" ||
            message.code === "room_not_found"
          ) {
            const wasPlaying = joinedOnce.current;
            saveToken("");
            setSession(null);
            setState(null);
            joinedOnce.current = false;
            // A stale token on cold load is not worth an error banner.
            if (message.code === "invalid_token" && !wasPlaying) break;
          }
          pushNotice("error", message.message || message.code);
          break;
        case "ack":
          if (!message.ok && message.request === "vote") {
            // First vote wins; duplicates are silently ignored by design.
            break;
          }
          if (!message.ok) {
            pushNotice("error", `${message.request}: ${message.detail || "rejected"}`);
          }
          break;
      }
    },
    [pushNotice],
  );

  // -- connection lifecycle -------------------------------------------------
  useEffect(() => {
    let disposed = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const connect = () => {
      if (disposed) return;
      setStatus("connecting");
      let ws: WebSocket;
      try {
        ws = new WebSocket(wsUrl());
      } catch {
        scheduleRetry();
        return;
      }
      wsRef.current = ws;

      ws.onopen = () => {
        if (disposed) return;
        setStatus("open");
        retry = 0;
        const token = loadToken();
        if (token) {
          ws.send(JSON.stringify({ type: "reconnect", token }));
        }
        const queued = pendingRef.current;
        if (queued) {
          ws.send(JSON.stringify(queued));
          setPending(null);
        }
      };
      ws.onmessage = (event) => {
        if (disposed) return;
        try {
          handleServerMessage(JSON.parse(String(event.data)));
        } catch {
          /* ignore malformed frame */
        }
      };
      ws.onerror = () => {
        /* onclose follows */
      };
      ws.onclose = () => {
        if (disposed) return;
        setStatus("closed");
        wsRef.current = null;
        // Keep session/state: the UI shows a reconnect banner and the token
        // is replayed on the next open. Only a server-side rejection clears it.
        scheduleRetry();
      };
    };

    const scheduleRetry = () => {
      retry += 1;
      const delay = Math.min(500 * 2 ** Math.min(retry, 5), 8000);
      timer = setTimeout(connect, delay);
    };

    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      const ws = wsRef.current;
      wsRef.current = null;
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [handleServerMessage]);

  // -- actions --------------------------------------------------------------
  const actions = useMemo(
    () => ({
      createRoom: (nickname: string) => {
        saveNickname(nickname);
        saveToken(""); // a fresh room must not race an old reconnect
        setSession(null);
        setState(null);
        send({ type: "create_room", nickname });
      },
      joinRoom: (code: string, nickname: string) => {
        saveNickname(nickname);
        saveToken("");
        setSession(null);
        setState(null);
        send({ type: "join", code: code.toUpperCase(), nickname });
      },
      reconnect: () => {
        const token = loadToken();
        if (token) send({ type: "reconnect", token });
      },
      sendChat: (text: string) => send({ type: "chat", text }),
      vote: (choice: "A" | "B") => send({ type: "vote", choice }),
      hostStart: () => send({ type: "host_start" }),
      hostKick: (playerId: string) => send({ type: "host_kick", player_id: playerId }),
      killInstance: () => send({ type: "kill_instance" }),
      sync: () => send({ type: "sync" }),
      leave: () => {
        send({ type: "leave" });
        saveToken("");
        setSession(null);
        setState(null);
        joinedOnce.current = false;
      },
      clearToken: () => {
        saveToken("");
        setSession(null);
        setState(null);
        joinedOnce.current = false;
      },
    }),
    [send],
  );

  return {
    status,
    instanceId,
    session,
    state,
    chats,
    notice,
    dismissNotice,
    offsetRef,
    actions,
  };
}

export type Game = ReturnType<typeof useGame>;
