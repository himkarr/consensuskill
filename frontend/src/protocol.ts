/**
 * Wire protocol types. Mirrors shared/schemas.py — the server is the source
 * of truth; these describe what travels over the socket.
 */

export type Phase =
  | "LOBBY"
  | "QUESTION"
  | "DISCUSSION"
  | "VOTE"
  | "REVEAL"
  | "APPLY"
  | "GAME_OVER";

export type Choice = "A" | "B";

export interface Player {
  id: string;
  nickname: string;
  lives: number;
  alive: boolean;
  connected: boolean;
  is_host: boolean;
  has_voted: boolean;
}

export interface Question {
  id: string;
  text: string;
  option_a: string;
  option_b: string;
}

export interface ChatMessage {
  player_id: string;
  nickname: string;
  text: string;
  ts: number;
}

export interface You {
  player_id: string;
  nickname: string;
  lives: number;
  alive: boolean;
  is_host: boolean;
  has_voted: boolean;
  choice: Choice | null;
}

export interface RoundResult {
  status: "normal" | "tie" | "unanimous";
  losing_side: Choice | null;
  majority_side: Choice | null;
  minority_side: Choice | null;
  abstainers: number;
  losers: string[];
  survivors: string[];
}

export interface StateMessage {
  type: "state";
  room_code: string;
  phase: Phase;
  round_no: number;
  timer_ends_at: number | null;
  server_time: number;
  question: Question | null;
  players: Player[];
  counts: { A: number; B: number } | null;
  votes: Record<string, Choice | null> | null;
  result: RoundResult | null;
  winners: string[];
  chat_log: ChatMessage[];
  min_players: number;
  starting_lives: number;
  host_id: string;
  version: number;
  you: You | null;
}

export interface InstanceInfo {
  type: "instance_info";
  instance_id: string;
  server_time: number;
}

export interface Joined {
  type: "joined";
  conn_id: string;
  player_id: string;
  token: string;
  room_code: string;
  is_host: boolean;
  server_time: number;
}

export interface ChatEvent extends ChatMessage {
  type: "chat";
}

export interface ErrorEvent {
  type: "error";
  code: string;
  message: string;
}

export interface AckEvent {
  type: "ack";
  request: string;
  ok: boolean;
  detail: string;
}

export type ServerMessage =
  | InstanceInfo
  | Joined
  | StateMessage
  | ChatEvent
  | ErrorEvent
  | AckEvent;

export type ClientMessage =
  | { type: "create_room"; nickname: string }
  | { type: "join"; code: string; nickname: string }
  | { type: "reconnect"; token: string }
  | { type: "chat"; text: string }
  | { type: "vote"; choice: Choice }
  | { type: "host_start" }
  | { type: "host_kick"; player_id: string }
  | { type: "kill_instance" }
  | { type: "sync" }
  | { type: "leave" };

export function isState(m: ServerMessage): m is StateMessage {
  return m.type === "state";
}
