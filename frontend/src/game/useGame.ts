import { useCallback, useEffect, useRef, useState } from 'preact/hooks'
import {
  DEFAULT_SETTINGS,
  INITIAL_STATE,
  type GameSettings,
  type GameState,
  type Player,
  type Round,
  type RoundResults,
  type Screen,
  type ServerMessage,
} from './types'

const toMillis = (value: unknown): number | null =>
  typeof value === 'number' ? value * 1000 : null

const getScreen = (gameState: unknown): Screen => {
  const screens: Record<string, Screen> = {
    LOBBY: 'waiting',
    PLAYING: 'playing',
    SCORING: 'scoring',
    ROUND_RESULTS: 'results',
    FINAL_RESULTS: 'final',
  }
  return screens[String(gameState)] ?? 'lobby'
}

export function useGame() {
  const [state, setState] = useState<GameState>(INITIAL_STATE)
  const socketRef = useRef<WebSocket | null>(null)
  const reconnectRef = useRef<number | null>(null)

  const send = useCallback((type: string, payload: Record<string, unknown> = {}) => {
    const socket = socketRef.current
    if (socket?.readyState !== WebSocket.OPEN) {
      setState((current) => ({ ...current, error: 'Still reconnecting. Try again in a moment.' }))
      return false
    }
    socket.send(JSON.stringify({ type, payload }))
    return true
  }, [])

  const handleMessage = useCallback((message: ServerMessage) => {
    const payload = message.payload ?? {}

    if (message.type === 'GAMES_LIST') {
      setState((current) => ({ ...current, games: (payload.games ?? []) as GameState['games'] }))
      return
    }

    if (message.type === 'LOBBY_UPDATE') {
      const token = typeof payload.session_token === 'string' ? payload.session_token : null
      if (token) {
        localStorage.setItem('game_session_token', token)
        localStorage.setItem('game_room_code', String(payload.room_code ?? ''))
      }
      setState((current) => {
        const playerId =
          typeof payload.player_id === 'string' ? payload.player_id : current.playerId
        const players = (payload.players ?? current.players) as Player[]
        const me = players.find((player) => player.id === playerId)
        return {
          ...current,
          screen: token || current.screen === 'lobby' || current.screen === 'waiting'
            ? 'waiting'
            : current.screen,
          roomCode: typeof payload.room_code === 'string' ? payload.room_code : current.roomCode,
          playerId,
          sessionToken: token ?? current.sessionToken,
          isHost:
            typeof payload.is_host === 'boolean'
              ? payload.is_host
              : (me?.is_host ?? current.isHost),
          players,
          settings: { ...current.settings, ...(payload.settings as Partial<GameSettings> ?? {}) },
        }
      })
      return
    }

    if (message.type === 'ROUND_START') {
      const startsAt = toMillis(payload.starts_at ?? payload.server_time)
      setState((current) => ({
        ...current,
        screen: startsAt && startsAt > Date.now() ? 'countdown' : 'playing',
        round: payload as unknown as Round,
        roundStartsAt: startsAt,
        roundDeadline:
          toMillis(payload.round_deadline) ??
          (startsAt
            ? startsAt + Number(payload.round_duration_seconds ?? 60) * 1000
            : Date.now() + Number(payload.round_duration_seconds ?? 60) * 1000),
        settings: {
          ...current.settings,
          rush_seconds: Number(payload.rush_seconds ?? current.settings.rush_seconds),
          round_duration_seconds: Number(
            payload.round_duration_seconds ?? current.settings.round_duration_seconds,
          ),
        },
        submittedIds: [],
        scoresSubmitted: false,
        rushActive: false,
        results: null,
      }))
      return
    }

    if (message.type === 'OPPONENT_SUBMITTED') {
      setState((current) => ({
        ...current,
        rushActive: true,
        roundDeadline: toMillis(payload.round_deadline) ?? current.roundDeadline,
        submittedIds: Array.isArray(payload.submitted_ids)
          ? payload.submitted_ids as string[]
          : current.submittedIds,
      }))
      navigator.vibrate?.(60)
      return
    }

    if (message.type === 'ROUND_ENDED') {
      setState((current) => ({
        ...current,
        screen: 'scoring',
        round: (payload.round as Round) ?? current.round,
        scoringDeadline: toMillis(payload.scoring_deadline),
        scoresSubmitted: false,
        rushActive: false,
      }))
      return
    }

    if (message.type === 'SCORING_UPDATE') {
      setState((current) => ({
        ...current,
        submittedIds: Array.isArray(payload.submitted_ids)
          ? payload.submitted_ids as string[]
          : current.submittedIds,
      }))
      return
    }

    if (message.type === 'ROUND_RESULTS') {
      setState((current) => ({
        ...current,
        screen: 'results',
        results: payload as unknown as RoundResults,
        scoringDeadline: null,
        players: current.players.map((player) => ({
          ...player,
          score: Number(
            (payload.cumulative_scores as Record<string, number> | undefined)?.[player.id] ??
              player.score,
          ),
        })),
      }))
      navigator.vibrate?.([40, 40, 80])
      return
    }

    if (message.type === 'GAME_OVER') {
      setState((current) => ({
        ...current,
        screen: 'final',
        history: (payload.history ?? []) as Round[],
        finalScores: (payload.final_scores ?? {}) as Record<string, number>,
      }))
      return
    }

    if (message.type === 'RECONNECTED') {
      const token = String(payload.session_token ?? '')
      localStorage.setItem('game_session_token', token)
      localStorage.setItem('game_room_code', String(payload.room_code ?? ''))
      setState((current) => {
        const nextScreen = getScreen(payload.game_state)
        const startsAt = toMillis(payload.starts_at)
        return {
          ...current,
          screen:
            nextScreen === 'playing' && startsAt && startsAt > Date.now()
              ? 'countdown'
              : nextScreen,
          roomCode: String(payload.room_code ?? ''),
          playerId: String(payload.player_id ?? ''),
          sessionToken: token,
          isHost: Boolean(payload.is_host),
          players: (payload.players ?? []) as Player[],
          settings: { ...DEFAULT_SETTINGS, ...(payload.settings as Partial<GameSettings> ?? {}) },
          round: (payload.round as Round | undefined) ?? null,
          roundStartsAt: startsAt,
          roundDeadline:
            toMillis(payload.round_deadline) ??
            (typeof payload.remaining_time === 'number'
              ? Date.now() + payload.remaining_time * 1000
              : null),
          scoringDeadline:
            toMillis(payload.scoring_deadline) ??
            (typeof payload.scoring_remaining === 'number'
              ? Date.now() + payload.scoring_remaining * 1000
              : null),
          submittedIds: (payload.submitted_ids ?? []) as string[],
          scoresSubmitted: Boolean(payload.scores_submitted),
          results:
            nextScreen === 'results'
              ? {
                  round_scores: (payload.round_scores ?? {}) as Record<string, Record<string, number>>,
                  cumulative_scores: (payload.cumulative_scores ?? {}) as Record<string, number>,
                  is_final_round: Boolean(payload.is_final_round),
                }
              : null,
          history: (payload.history ?? []) as Round[],
          finalScores: (payload.final_scores ?? {}) as Record<string, number>,
        }
      })
      return
    }

    if (message.type === 'PLAYER_DISCONNECTED' || message.type === 'PLAYER_RECONNECTED') {
      const playerId = String(payload.player_id ?? '')
      setState((current) => ({
        ...current,
        players: current.players.map((player) =>
          player.id === playerId
            ? { ...player, is_connected: message.type === 'PLAYER_RECONNECTED' }
            : player,
        ),
        submittedIds: Array.isArray(payload.submitted_ids)
          ? payload.submitted_ids as string[]
          : current.submittedIds,
      }))
      return
    }

    if (message.type === 'HOST_CHANGED') {
      const hostId = String(payload.new_host_id ?? '')
      setState((current) => ({
        ...current,
        isHost: current.playerId === hostId,
        players: current.players.map((player) => ({ ...player, is_host: player.id === hostId })),
      }))
      return
    }

    if (message.type === 'SESSION_HIJACKED') {
      setState((current) => ({ ...current, sessionHijacked: true }))
      return
    }

    if (message.type === 'ERROR') {
      if (payload.code === 'SESSION_EXPIRED') {
        localStorage.removeItem('game_session_token')
        localStorage.removeItem('game_room_code')
        setState((current) => ({
          ...INITIAL_STATE,
          connection: current.connection,
          playerName: current.playerName,
          sessionToken: null,
          error: String(payload.message ?? 'Your previous game has expired.'),
        }))
      } else {
        setState((current) => ({
          ...current,
          error: String(payload.message ?? 'Something went wrong.'),
        }))
      }
    }
  }, [])

  useEffect(() => {
    let stopped = false
    let attempt = 0

    const connect = () => {
      if (stopped || socketRef.current?.readyState === WebSocket.OPEN) return
      setState((current) => ({ ...current, connection: 'connecting' }))
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      const socket = new WebSocket(`${protocol}//${window.location.host}/ws`)
      socketRef.current = socket

      socket.onopen = () => {
        attempt = 0
        setState((current) => ({ ...current, connection: 'online' }))
        const token = localStorage.getItem('game_session_token')
        if (token) socket.send(JSON.stringify({ type: 'REJOIN_GAME', payload: { session_token: token } }))
        socket.send(JSON.stringify({ type: 'GET_GAMES', payload: {} }))
      }

      socket.onmessage = (event) => {
        try {
          handleMessage(JSON.parse(event.data) as ServerMessage)
        } catch {
          setState((current) => ({ ...current, error: 'Received an invalid server response.' }))
        }
      }

      socket.onclose = () => {
        if (socketRef.current === socket) socketRef.current = null
        if (stopped) return
        setState((current) => ({ ...current, connection: 'offline' }))
        attempt += 1
        reconnectRef.current = window.setTimeout(connect, Math.min(1000 * 2 ** attempt, 8000))
      }

      socket.onerror = () => socket.close()
    }

    connect()
    const onVisible = () => {
      if (document.visibilityState === 'visible' && !socketRef.current) connect()
    }
    document.addEventListener('visibilitychange', onVisible)
    return () => {
      stopped = true
      document.removeEventListener('visibilitychange', onVisible)
      if (reconnectRef.current) window.clearTimeout(reconnectRef.current)
      socketRef.current?.close()
    }
  }, [handleMessage])

  useEffect(() => {
    if (state.screen !== 'countdown' || !state.roundStartsAt) return
    const wait = Math.max(0, state.roundStartsAt - Date.now())
    const timer = window.setTimeout(
      () => setState((current) => ({ ...current, screen: 'playing' })),
      wait,
    )
    return () => window.clearTimeout(timer)
  }, [state.screen, state.roundStartsAt])

  useEffect(() => {
    if (state.screen !== 'lobby' || state.connection !== 'online') return
    send('GET_GAMES')
    const timer = window.setInterval(() => send('GET_GAMES'), 5000)
    return () => window.clearInterval(timer)
  }, [send, state.connection, state.screen])

  const setPlayerName = (name: string) => {
    const cleanName = name.trim().slice(0, 24)
    localStorage.setItem('player_name', cleanName)
    setState((current) => ({ ...current, playerName: cleanName }))
  }

  const join = (roomCode?: string) =>
    send('JOIN_GAME', {
      player_name: state.playerName,
      room_code: roomCode?.trim().toUpperCase() || null,
    })

  const host = () =>
    send('JOIN_GAME', {
      player_name: state.playerName,
      room_code: null,
      precise_scoring: state.settings.precise_scoring,
    })

  const updateSettings = (settings: Partial<GameSettings>) => {
    setState((current) => ({
      ...current,
      settings: { ...current.settings, ...settings },
    }))
    send('UPDATE_SETTINGS', settings as Record<string, unknown>)
  }

  const submitAnswers = (answers: Record<string, string>) => {
    if (!send('SUBMIT_ANSWERS', { answers })) return
    setState((current) => ({
      ...current,
      submittedIds: current.playerId
        ? [...new Set([...current.submittedIds, current.playerId])]
        : current.submittedIds,
    }))
  }

  const submitScores = (scores: Record<string, Record<string, number>>) => {
    if (!send('SUBMIT_SCORES', { scores })) return
    setState((current) => ({ ...current, scoresSubmitted: true }))
  }

  const leave = () => {
    send('LEAVE_GAME')
    localStorage.removeItem('game_session_token')
    localStorage.removeItem('game_room_code')
    setState((current) => ({
      ...INITIAL_STATE,
      connection: current.connection,
      playerName: current.playerName,
      sessionToken: null,
    }))
  }

  return {
    state,
    setPlayerName,
    join,
    host,
    updateSettings,
    start: () => send('START_GAME'),
    submitAnswers,
    submitScores,
    nextRound: () => send('NEXT_ROUND'),
    endGame: () => send('END_GAME'),
    leave,
    dismissError: () => setState((current) => ({ ...current, error: null })),
    dismissHijack: () => setState((current) => ({ ...current, sessionHijacked: false })),
  }
}
