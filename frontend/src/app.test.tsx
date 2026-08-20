import { act, fireEvent, render, screen, waitFor } from '@testing-library/preact'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App, formatTime, makeDefaultVotes } from './app'
import type { Player, Round } from './game/types'

class FakeWebSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances: FakeWebSocket[] = []

  readyState = FakeWebSocket.CONNECTING
  sent: string[] = []
  onopen: (() => void) | null = null
  onclose: (() => void) | null = null
  onerror: (() => void) | null = null
  onmessage: ((event: MessageEvent<string>) => void) | null = null
  url: string

  constructor(url: string) {
    this.url = url
    FakeWebSocket.instances.push(this)
  }

  open() {
    this.readyState = FakeWebSocket.OPEN
    this.onopen?.()
  }

  send(message: string) {
    this.sent.push(message)
  }

  receive(message: Record<string, unknown>) {
    this.onmessage?.({ data: JSON.stringify(message) } as MessageEvent<string>)
  }

  close() {
    this.readyState = FakeWebSocket.CLOSED
    this.onclose?.()
  }
}

beforeEach(() => {
  FakeWebSocket.instances = []
  vi.stubGlobal('WebSocket', FakeWebSocket)
})

describe('Categories app', () => {
  it('collects and persists the player name before play', async () => {
    render(<App />)

    const input = screen.getByLabelText('Player name')
    fireEvent.input(input, { target: { value: 'Sam' } })
    fireEvent.click(screen.getByRole('button', { name: /save my name/i }))

    expect(localStorage.getItem('player_name')).toBe('Sam')
    expect(await screen.findByText(/ready when you are, sam/i)).toBeInTheDocument()
  })

  it('rejoins a saved session when the socket opens', () => {
    localStorage.setItem('player_name', 'Sam')
    localStorage.setItem('game_session_token', 'saved-token')
    render(<App />)

    const socket = FakeWebSocket.instances[0]
    act(() => socket.open())

    expect(socket.sent.map((message) => JSON.parse(message))).toContainEqual({
      type: 'REJOIN_GAME',
      payload: { session_token: 'saved-token' },
    })
  })

  it('renders player-controlled text without creating markup', async () => {
    localStorage.setItem('player_name', 'Sam')
    render(<App />)
    const socket = FakeWebSocket.instances[0]

    act(() => {
      socket.open()
      socket.receive({
        type: 'LOBBY_UPDATE',
        payload: {
          room_code: 'SAFE',
          player_id: 'one',
          is_host: true,
          session_token: 'token',
          players: [
            {
              id: 'one',
              name: '<img src=x onerror=alert(1)>',
              score: 0,
              is_host: true,
              is_connected: true,
            },
          ],
        },
      })
    })

    await waitFor(() => expect(
      screen.getByText(/^<img src=x onerror=alert\(1\)> \(you\)$/),
    ).toBeInTheDocument())
    expect(document.querySelector('img[onerror]')).toBeNull()
  })

  it('uses the server start time for the pre-round countdown', async () => {
    localStorage.setItem('player_name', 'Sam')
    render(<App />)
    const socket = FakeWebSocket.instances[0]
    const startsAt = Date.now() / 1000 + 3

    act(() => {
      socket.open()
      socket.receive({
        type: 'ROUND_START',
        payload: {
          round_number: 1,
          letter: 'B',
          categories: ['Animal'],
          answers: {},
          scores: {},
          scoring_votes: {},
          starts_at: startsAt,
          round_deadline: startsAt + 60,
          round_duration_seconds: 60,
          rush_seconds: 5,
        },
      })
    })

    expect(await screen.findByText('Get your thumbs ready')).toBeInTheDocument()
    expect(screen.getByText('B')).toBeInTheDocument()
  })

  it('sends an explicit score for every reviewed answer', async () => {
    localStorage.setItem('player_name', 'Sam')
    render(<App />)
    const socket = FakeWebSocket.instances[0]
    const players: Player[] = [
      { id: 'me', name: 'Sam', score: 0, is_host: true, is_connected: true },
      { id: 'them', name: 'Riley', score: 0, is_host: false, is_connected: true },
    ]
    const round: Round = {
      round_number: 1,
      letter: 'A',
      categories: ['Animal'],
      answers: {
        me: { Animal: 'Alpaca' },
        them: { Animal: 'Antelope' },
      },
      scores: {},
      scoring_votes: {},
    }

    act(() => {
      socket.open()
      socket.receive({
        type: 'LOBBY_UPDATE',
        payload: {
          room_code: 'VOTE',
          player_id: 'me',
          is_host: true,
          session_token: 'token',
          players,
        },
      })
    })
    act(() => socket.receive({
      type: 'ROUND_ENDED',
      payload: { round, scoring_deadline: null },
    }))

    const invalid = await screen.findByRole('button', { name: 'Invalid: 0 points' })
    fireEvent.click(invalid)
    fireEvent.click(screen.getByRole('button', { name: /submit scores/i }))

    const submitted = socket.sent
      .map((message) => JSON.parse(message))
      .reverse()
      .find((message) => message.type === 'SUBMIT_SCORES')
    expect(submitted.payload.scores).toEqual({ Animal: { them: 0 } })
  })
})

describe('game presentation helpers', () => {
  it('formats authoritative deadline durations for display', () => {
    expect(formatTime(60_000)).toBe('1:00')
    expect(formatTime(9_001)).toBe('0:10')
    expect(formatTime(-1)).toBe('0:00')
  })

  it('defaults answered scoring choices to unique and blanks to invalid', () => {
    const players: Player[] = [
      { id: 'me', name: 'Me', score: 0, is_host: true, is_connected: true },
      { id: 'them', name: 'Them', score: 0, is_host: false, is_connected: true },
    ]
    const round: Round = {
      round_number: 1,
      letter: 'A',
      categories: ['Animal', 'City'],
      answers: {
        them: { Animal: 'Antelope', City: '' },
      },
      scores: {},
      scoring_votes: {},
    }

    expect(makeDefaultVotes(round, players, 'me')).toEqual({
      Animal: { them: 2 },
      City: { them: 0 },
    })
  })
})
