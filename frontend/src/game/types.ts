export type ConnectionStatus = 'connecting' | 'online' | 'offline'
export type Screen =
  | 'lobby'
  | 'waiting'
  | 'countdown'
  | 'playing'
  | 'scoring'
  | 'results'
  | 'final'

export type Player = {
  id: string
  name: string
  score: number
  is_host: boolean
  is_connected: boolean
}

export type GameSettings = {
  rush_seconds: number
  precise_scoring: boolean
  scoring_timeout_seconds: number | null
  round_duration_seconds: number
}

export type Round = {
  round_number: number
  letter: string
  categories: string[]
  answers: Record<string, Record<string, string>>
  scores: Record<string, Record<string, number>>
  scoring_votes: Record<string, Record<string, Record<string, number>>>
}

export type OpenGame = {
  code: string
  host_name: string
  player_count: number
}

export type RoundResults = {
  round_scores: Record<string, Record<string, number>>
  cumulative_scores: Record<string, number>
  is_final_round: boolean
  timeout?: boolean
}

export type GameState = {
  connection: ConnectionStatus
  screen: Screen
  playerName: string
  playerId: string | null
  roomCode: string | null
  sessionToken: string | null
  isHost: boolean
  players: Player[]
  settings: GameSettings
  games: OpenGame[]
  round: Round | null
  roundStartsAt: number | null
  roundDeadline: number | null
  rushActive: boolean
  submittedIds: string[]
  scoresSubmitted: boolean
  scoringDeadline: number | null
  results: RoundResults | null
  history: Round[]
  finalScores: Record<string, number>
  error: string | null
  sessionHijacked: boolean
}

export type ServerMessage = {
  type: string
  payload?: Record<string, unknown>
}

export const DEFAULT_SETTINGS: GameSettings = {
  rush_seconds: 5,
  precise_scoring: false,
  scoring_timeout_seconds: 60,
  round_duration_seconds: 60,
}

export const INITIAL_STATE: GameState = {
  connection: 'connecting',
  screen: 'lobby',
  playerName: localStorage.getItem('player_name') ?? '',
  playerId: null,
  roomCode: null,
  sessionToken: localStorage.getItem('game_session_token'),
  isHost: false,
  players: [],
  settings: DEFAULT_SETTINGS,
  games: [],
  round: null,
  roundStartsAt: null,
  roundDeadline: null,
  rushActive: false,
  submittedIds: [],
  scoresSubmitted: false,
  scoringDeadline: null,
  results: null,
  history: [],
  finalScores: {},
  error: null,
  sessionHijacked: false,
}
