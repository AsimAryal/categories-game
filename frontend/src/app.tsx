import {
  ArrowRight,
  Check,
  ChevronDown,
  Clipboard,
  Crown,
  LogOut,
  Moon,
  Play,
  Plus,
  RotateCcw,
  Share2,
  Sparkles,
  Sun,
  Trophy,
  UserRound,
  UsersRound,
  Volume2,
  VolumeX,
  Wifi,
  WifiOff,
  X,
} from 'lucide-preact'
import { useEffect, useMemo, useRef, useState } from 'preact/hooks'
import { useGame } from './game/useGame'
import type { GameState, Player, Round } from './game/types'

type GameActions = ReturnType<typeof useGame>
type DeferredInstallPrompt = Event & {
  prompt: () => Promise<void>
  userChoice: Promise<{ outcome: 'accepted' | 'dismissed' }>
}

const scoreLabels = [
  { value: 0, short: 'No', label: 'Invalid' },
  { value: 1, short: 'Same', label: 'Duplicate' },
  { value: 2, short: 'Yes', label: 'Unique' },
]

function useNow(interval = 250) {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), interval)
    return () => window.clearInterval(timer)
  }, [interval])
  return now
}

export function formatTime(milliseconds: number) {
  const seconds = Math.max(0, Math.ceil(milliseconds / 1000))
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}

function playTone(enabled: boolean, frequency = 520, length = 0.08) {
  if (!enabled) return
  try {
    const AudioContextClass = window.AudioContext
    const context = new AudioContextClass()
    const oscillator = context.createOscillator()
    const gain = context.createGain()
    oscillator.frequency.value = frequency
    gain.gain.setValueAtTime(0.08, context.currentTime)
    gain.gain.exponentialRampToValueAtTime(0.001, context.currentTime + length)
    oscillator.connect(gain)
    gain.connect(context.destination)
    oscillator.start()
    oscillator.stop(context.currentTime + length)
  } catch {
    // Sound is optional and may be blocked until a browser gesture.
  }
}

function Logo() {
  return (
    <div class="brand" aria-label="Categories">
      <span class="brand-mark" aria-hidden="true">
        <i>C</i><i>A</i><i>T</i><i>S</i>
      </span>
      <span class="brand-copy">
        <strong>Categories</strong>
        <small>Game night, sorted.</small>
      </span>
    </div>
  )
}

function AppHeader({
  state,
  theme,
  sound,
  onTheme,
  onSound,
  onEditName,
}: {
  state: GameState
  theme: 'light' | 'dark'
  sound: boolean
  onTheme: () => void
  onSound: () => void
  onEditName: () => void
}) {
  const inRoom = state.screen !== 'lobby'
  return (
    <>
      <header class="app-header">
        <Logo />
        <div class="header-actions">
          {!inRoom && state.playerName && (
            <button class="icon-button profile-button" onClick={onEditName} aria-label="Edit player name">
              <UserRound size={19} />
              <span>{state.playerName}</span>
            </button>
          )}
          <button class="icon-button" onClick={onSound} aria-label={sound ? 'Mute sounds' : 'Enable sounds'}>
            {sound ? <Volume2 size={19} /> : <VolumeX size={19} />}
          </button>
          <button class="icon-button" onClick={onTheme} aria-label={`Use ${theme === 'light' ? 'dark' : 'light'} theme`}>
            {theme === 'light' ? <Moon size={19} /> : <Sun size={19} />}
          </button>
        </div>
      </header>
      <div class={`connection-pill ${state.connection}`} role="status" aria-live="polite">
        {state.connection === 'online' ? <Wifi size={14} /> : <WifiOff size={14} />}
        {state.connection === 'online'
          ? 'Connected'
          : state.connection === 'connecting'
            ? 'Connecting…'
            : 'Reconnecting…'}
      </div>
    </>
  )
}

function NameDialog({
  initialName,
  onSave,
  onClose,
}: {
  initialName: string
  onSave: (name: string) => void
  onClose?: () => void
}) {
  const [name, setName] = useState(initialName)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => inputRef.current?.focus(), [])

  const save = (event: Event) => {
    event.preventDefault()
    if (!name.trim()) return
    onSave(name)
  }

  return (
    <div class="dialog-backdrop" role="presentation">
      <section class="dialog-card" role="dialog" aria-modal="true" aria-labelledby="name-title">
        {onClose && (
          <button class="dialog-close icon-button" onClick={onClose} aria-label="Close">
            <X size={20} />
          </button>
        )}
        <span class="eyebrow">First things first</span>
        <h1 id="name-title">What should we call you?</h1>
        <p>This is how everyone at the table will see you.</p>
        <form onSubmit={save}>
          <label class="field">
            <span>Player name</span>
            <input
              ref={inputRef}
              value={name}
              maxlength={24}
              autocomplete="nickname"
              placeholder="e.g. Alex"
              onInput={(event) => setName(event.currentTarget.value)}
            />
          </label>
          <button class="button primary" type="submit" disabled={!name.trim()}>
            Save my name <ArrowRight size={18} />
          </button>
        </form>
      </section>
    </div>
  )
}

function LobbyScreen({ game, sound }: { game: GameActions; sound: boolean }) {
  const { state } = game
  const [tab, setTab] = useState<'join' | 'host'>('join')
  const [code, setCode] = useState('')

  const join = (roomCode = code) => {
    if (!roomCode.trim()) return
    playTone(sound)
    game.join(roomCode)
  }

  return (
    <main class="screen lobby-screen">
      <section class="hero-card">
        <div>
          <span class="eyebrow">Ready when you are, {state.playerName}</span>
          <h1>Pick a letter.<br />Race the room.</h1>
          <p>Quick answers, questionable spelling, and very serious bragging rights.</p>
        </div>
        <div class="hero-tiles" aria-hidden="true">
          <span>A<small>Animal</small></span>
          <span>B<small>Brand</small></span>
          <span>C<small>City</small></span>
        </div>
      </section>

      <section class="panel lobby-panel">
        <div class="tabs" role="tablist" aria-label="Join or host a game">
          <button
            role="tab"
            aria-selected={tab === 'join'}
            class={tab === 'join' ? 'active' : ''}
            onClick={() => setTab('join')}
          >
            Join a room
          </button>
          <button
            role="tab"
            aria-selected={tab === 'host'}
            class={tab === 'host' ? 'active' : ''}
            onClick={() => setTab('host')}
          >
            Host a game
          </button>
        </div>

        {tab === 'join' ? (
          <div class="tab-panel" role="tabpanel">
            <form
              class="code-form"
              onSubmit={(event) => {
                event.preventDefault()
                join()
              }}
            >
              <label class="field grow">
                <span>Room code</span>
                <input
                  class="room-code-input"
                  value={code}
                  maxlength={4}
                  inputMode="text"
                  autocomplete="off"
                  autocapitalize="characters"
                  placeholder="AB12"
                  onInput={(event) => setCode(event.currentTarget.value.toUpperCase().replace(/[^A-Z0-9]/g, ''))}
                />
              </label>
              <button class="button primary compact" type="submit" disabled={code.length !== 4}>
                Join <ArrowRight size={18} />
              </button>
            </form>

            <div class="section-heading">
              <div>
                <span class="eyebrow">Nearby tables</span>
                <h2>Open rooms</h2>
              </div>
              <span class="live-dot">Live</span>
            </div>
            {state.games.length ? (
              <div class="game-list">
                {state.games.map((openGame) => (
                  <button class="game-row" key={openGame.code} onClick={() => join(openGame.code)}>
                    <span class="room-avatar">{openGame.host_name.slice(0, 1).toUpperCase()}</span>
                    <span class="game-info">
                      <strong>{openGame.host_name}’s room</strong>
                      <small><UsersRound size={14} /> {openGame.player_count}/5 players · {openGame.code}</small>
                    </span>
                    <ArrowRight size={19} />
                  </button>
                ))}
              </div>
            ) : (
              <div class="empty-state">
                <UsersRound size={28} />
                <strong>No open rooms yet</strong>
                <span>Start one and invite the table.</span>
              </div>
            )}
          </div>
        ) : (
          <div class="tab-panel host-panel" role="tabpanel">
            <span class="host-illustration"><Crown size={28} /></span>
            <h2>Your table, your rules</h2>
            <p>Create a private four-character room code. You can tune the round before everyone starts.</p>
            <button class="button primary" onClick={() => { playTone(sound, 620); game.host() }}>
              <Plus size={19} /> Create a room
            </button>
          </div>
        )}
      </section>
    </main>
  )
}

function SettingSelect({
  label,
  hint,
  value,
  onChange,
  children,
}: {
  label: string
  hint: string
  value: string | number
  onChange: (value: string) => void
  children: preact.ComponentChildren
}) {
  return (
    <label class="setting-row">
      <span><strong>{label}</strong><small>{hint}</small></span>
      <span class="select-wrap">
        <select value={String(value)} onChange={(event) => onChange(event.currentTarget.value)}>
          {children}
        </select>
        <ChevronDown size={16} />
      </span>
    </label>
  )
}

function WaitingScreen({
  game,
  onLeave,
  sound,
}: {
  game: GameActions
  onLeave: () => void
  sound: boolean
}) {
  const { state } = game
  const connected = state.players.filter((player) => player.is_connected)
  const canStart = state.isHost && connected.length >= 2

  const shareRoom = async () => {
    const text = `Join my Categories game. Room code: ${state.roomCode}`
    if (navigator.share) {
      await navigator.share({ title: 'Categories game night', text, url: window.location.origin })
    } else {
      await navigator.clipboard.writeText(`${text}\n${window.location.origin}`)
      game.dismissError()
    }
  }

  const copyCode = async () => {
    await navigator.clipboard.writeText(state.roomCode ?? '')
    playTone(sound, 680)
  }

  return (
    <main class="screen waiting-screen">
      <div class="screen-topline">
        <button class="button ghost compact" onClick={onLeave}><LogOut size={17} /> Leave</button>
        <span class="round-chip">Lobby</span>
      </div>

      <section class="room-code-card">
        <span class="eyebrow">Your room code</span>
        <button class="room-code-display" onClick={copyCode} aria-label={`Copy room code ${state.roomCode}`}>
          {state.roomCode?.split('').map((letter, index) => <i key={`${letter}-${index}`}>{letter}</i>)}
        </button>
        <div class="inline-actions">
          <button class="button secondary compact" onClick={copyCode}><Clipboard size={17} /> Copy</button>
          <button class="button secondary compact" onClick={shareRoom}><Share2 size={17} /> Invite</button>
        </div>
      </section>

      <div class="waiting-grid">
        <section class="panel">
          <div class="section-heading">
            <div>
              <span class="eyebrow">At the table</span>
              <h2>{connected.length} of 5 players</h2>
            </div>
            <span class="pulse-ring" aria-hidden="true" />
          </div>
          <div class="player-list">
            {state.players.map((player) => (
              <div class={`player-row ${player.is_connected ? '' : 'disconnected'}`} key={player.id}>
                <span class="player-avatar">{player.name.slice(0, 1).toUpperCase()}</span>
                <span class="player-details">
                  <strong>{player.name}{player.id === state.playerId ? ' (you)' : ''}</strong>
                  <small>{player.is_connected ? 'Ready to play' : 'Reconnecting…'}</small>
                </span>
                {player.is_host && <span class="host-badge"><Crown size={13} /> Host</span>}
                {!player.is_host && player.is_connected && <Check size={18} class="ready-check" />}
              </div>
            ))}
            {Array.from({ length: Math.max(0, 2 - state.players.length) }).map((_, index) => (
              <div class="player-row placeholder" key={index}>
                <span class="player-avatar"><UserRound size={17} /></span>
                <span class="player-details"><strong>Waiting for someone</strong><small>Share the code above</small></span>
              </div>
            ))}
          </div>
        </section>

        <section class="panel settings-panel">
          <div class="section-heading">
            <div><span class="eyebrow">House rules</span><h2>Round setup</h2></div>
          </div>
          {state.isHost ? (
            <div class="settings-list">
              <SettingSelect
                label="Round length"
                hint="Time to write answers"
                value={state.settings.round_duration_seconds}
                onChange={(value) => game.updateSettings({ round_duration_seconds: Number(value) })}
              >
                <option value={30}>30 seconds</option>
                <option value={60}>60 seconds</option>
                <option value={90}>90 seconds</option>
                <option value={120}>2 minutes</option>
              </SettingSelect>
              <SettingSelect
                label="Rush timer"
                hint="After the first submission"
                value={state.settings.rush_seconds}
                onChange={(value) => game.updateSettings({ rush_seconds: Number(value) })}
              >
                <option value={5}>5 seconds</option>
                <option value={10}>10 seconds</option>
                <option value={15}>15 seconds</option>
              </SettingSelect>
              <SettingSelect
                label="Scoring timer"
                hint="Time to review answers"
                value={state.settings.scoring_timeout_seconds ?? 0}
                onChange={(value) => game.updateSettings({ scoring_timeout_seconds: Number(value) })}
              >
                <option value={0}>No limit</option>
                <option value={30}>30 seconds</option>
                <option value={60}>60 seconds</option>
                <option value={90}>90 seconds</option>
              </SettingSelect>
              <label class="setting-row">
                <span><strong>Precise scoring</strong><small>Average votes instead of rounding</small></span>
                <input
                  class="switch"
                  type="checkbox"
                  checked={state.settings.precise_scoring}
                  onChange={(event) => game.updateSettings({ precise_scoring: event.currentTarget.checked })}
                />
              </label>
            </div>
          ) : (
            <div class="rules-summary">
              <span><strong>{state.settings.round_duration_seconds}s</strong><small>Round</small></span>
              <span><strong>{state.settings.rush_seconds}s</strong><small>Rush</small></span>
              <span><strong>{state.settings.scoring_timeout_seconds ?? '∞'}</strong><small>Scoring</small></span>
            </div>
          )}
        </section>
      </div>

      <div class="sticky-action">
        {state.isHost ? (
          <>
            <button
              class="button primary large"
              disabled={!canStart}
              onClick={() => { playTone(sound, 720); game.start() }}
            >
              <Play size={19} fill="currentColor" /> Start round one
            </button>
            {!canStart && <small>At least two connected players are needed.</small>}
          </>
        ) : (
          <div class="waiting-message"><span class="spinner" /> Waiting for the host to start…</div>
        )}
      </div>
    </main>
  )
}

function PhaseHeader({
  state,
  title,
  detail,
  timer,
  urgent,
}: {
  state: GameState
  title: string
  detail: string
  timer?: string
  urgent?: boolean
}) {
  return (
    <div class="phase-header">
      <div>
        <span class="eyebrow">Round {state.round?.round_number ?? state.history.length + 1}</span>
        <h1>{title}</h1>
        <p>{detail}</p>
      </div>
      {timer && <span class={`timer-chip ${urgent ? 'urgent' : ''}`}>{timer}</span>}
    </div>
  )
}

function CountdownScreen({ state }: { state: GameState }) {
  const now = useNow(100)
  const remaining = Math.max(0, (state.roundStartsAt ?? now) - now)
  const count = Math.max(1, Math.ceil(remaining / 1000))
  return (
    <main class="screen countdown-screen" aria-live="assertive">
      <span class="eyebrow">Round {state.round?.round_number}</span>
      <p>Your letter is</p>
      <div class="countdown-letter">{state.round?.letter}</div>
      <strong class="countdown-number" key={count}>{count}</strong>
      <small>Get your thumbs ready</small>
    </main>
  )
}

function PlayScreen({ game, sound }: { game: GameActions; sound: boolean }) {
  const { state } = game
  const round = state.round
  const now = useNow()
  const [answers, setAnswers] = useState<Record<string, string>>({})
  const submitted = Boolean(state.playerId && state.submittedIds.includes(state.playerId))
  const autoSubmittedRef = useRef(false)
  const remaining = Math.max(0, (state.roundDeadline ?? now) - now)
  const duration = (
    state.rushActive
      ? state.settings.rush_seconds
      : state.settings.round_duration_seconds
  ) * 1000
  const progress = Math.max(0, Math.min(100, (remaining / duration) * 100))

  useEffect(() => {
    setAnswers(round?.answers?.[state.playerId ?? ''] ?? {})
    autoSubmittedRef.current = false
  }, [round, state.playerId])

  useEffect(() => {
    if (round && remaining <= 0 && !submitted && !autoSubmittedRef.current) {
      autoSubmittedRef.current = true
      game.submitAnswers(answers)
    }
  }, [answers, game, remaining, round, submitted])

  if (!round) return null

  return (
    <main class={`screen play-screen ${state.rushActive ? 'rush' : ''}`}>
      <PhaseHeader
        state={state}
        title={state.rushActive ? 'Quick — someone’s in!' : 'Think fast'}
        detail={`${state.submittedIds.length} of ${state.players.filter((player) => player.is_connected).length} submitted`}
        timer={formatTime(remaining)}
        urgent={remaining <= 10000}
      />
      <div class="timer-track" aria-hidden="true"><span style={{ width: `${progress}%` }} /></div>

      <div class="letter-banner">
        <span>Every answer starts with</span>
        <strong>{round.letter}</strong>
      </div>

      <form
        class="answer-list"
        onSubmit={(event) => {
          event.preventDefault()
          playTone(sound, 640)
          game.submitAnswers(answers)
        }}
      >
        {round.categories.map((category, index) => (
          <label class="answer-card" key={category}>
            <span class="answer-number">{index + 1}</span>
            <span class="field grow">
              <span>{category}</span>
              <input
                value={answers[category] ?? ''}
                placeholder={`${round.letter}…`}
                maxlength={64}
                autocomplete="off"
                disabled={submitted}
                onInput={(event) =>
                  setAnswers((current) => ({ ...current, [category]: event.currentTarget.value }))
                }
              />
            </span>
          </label>
        ))}
        <div class="sticky-action">
          <button class="button primary large" type="submit" disabled={submitted}>
            {submitted ? <><Check size={20} /> Answers locked in</> : <>Lock in answers <ArrowRight size={20} /></>}
          </button>
          {submitted && <small>Waiting for everyone else. You can relax now.</small>}
        </div>
      </form>
    </main>
  )
}

export function makeDefaultVotes(round: Round, players: Player[], playerId: string | null) {
  return Object.fromEntries(
    round.categories.map((category) => [
      category,
      Object.fromEntries(
        players
          .filter((player) => player.id !== playerId)
          .map((player) => [player.id, round.answers[player.id]?.[category]?.trim() ? 2 : 0]),
      ),
    ]),
  )
}

function ScoringScreen({ game, sound }: { game: GameActions; sound: boolean }) {
  const { state } = game
  const round = state.round
  const now = useNow()
  const [votes, setVotes] = useState<Record<string, Record<string, number>>>({})
  const autoSubmittedRef = useRef(false)
  const remaining = state.scoringDeadline ? Math.max(0, state.scoringDeadline - now) : null

  useEffect(() => {
    if (round) setVotes(makeDefaultVotes(round, state.players, state.playerId))
    autoSubmittedRef.current = false
  }, [round, state.playerId, state.players])

  useEffect(() => {
    if (
      remaining !== null &&
      remaining <= 0 &&
      !state.scoresSubmitted &&
      !autoSubmittedRef.current
    ) {
      autoSubmittedRef.current = true
      game.submitScores(votes)
    }
  }, [game, remaining, state.scoresSubmitted, votes])

  if (!round) return null

  return (
    <main class="screen scoring-screen">
      <PhaseHeader
        state={state}
        title="Be the judge"
        detail="Rate each answer. Your own answers are shown for context."
        timer={remaining === null ? undefined : formatTime(remaining)}
        urgent={remaining !== null && remaining <= 10000}
      />
      <div class="score-legend" aria-label="Scoring guide">
        {scoreLabels.map((score) => <span key={score.value}><i>{score.value}</i>{score.label}</span>)}
      </div>

      <div class="scoring-list">
        {round.categories.map((category, categoryIndex) => (
          <section class="scoring-category" key={category}>
            <header><span>{categoryIndex + 1}</span><h2>{category}</h2></header>
            <div class="my-answer">
              <span>Your answer</span>
              <strong>{round.answers[state.playerId ?? '']?.[category] || 'No answer'}</strong>
            </div>
            {state.players.filter((player) => player.id !== state.playerId).map((player) => {
              const answer = round.answers[player.id]?.[category]?.trim()
              const selected = votes[category]?.[player.id] ?? (answer ? 2 : 0)
              return (
                <div class="answer-review" key={player.id}>
                  <div>
                    <span>{player.name}</span>
                    <strong>{answer || 'No answer'}</strong>
                  </div>
                  <div class="segmented-score" aria-label={`Score ${player.name}'s answer`}>
                    {scoreLabels.map((score) => (
                      <button
                        type="button"
                        class={selected === score.value ? 'selected' : ''}
                        disabled={state.scoresSubmitted || !answer}
                        aria-label={`${score.label}: ${score.value} points`}
                        aria-pressed={selected === score.value}
                        onClick={() => {
                          playTone(sound, 420 + score.value * 100, 0.04)
                          setVotes((current) => ({
                            ...current,
                            [category]: { ...current[category], [player.id]: score.value },
                          }))
                        }}
                      >
                        <b>{score.value}</b><small>{score.short}</small>
                      </button>
                    ))}
                  </div>
                </div>
              )
            })}
          </section>
        ))}
      </div>

      <div class="sticky-action">
        <button
          class="button primary large"
          disabled={state.scoresSubmitted}
          onClick={() => { playTone(sound, 660); game.submitScores(votes) }}
        >
          {state.scoresSubmitted ? <><Check size={20} /> Scores submitted</> : <>Submit scores <ArrowRight size={20} /></>}
        </button>
        {state.scoresSubmitted && <small>Waiting for the other judges.</small>}
      </div>
    </main>
  )
}

function Scoreboard({
  state,
  scores,
  roundScores,
}: {
  state: GameState
  scores: Record<string, number>
  roundScores?: Record<string, Record<string, number>>
}) {
  const ordered = [...state.players].sort((a, b) => (scores[b.id] ?? 0) - (scores[a.id] ?? 0))
  return (
    <div class="scoreboard">
      {ordered.map((player, index) => {
        const roundTotal = roundScores
          ? Object.values(roundScores[player.id] ?? {}).reduce((total, score) => total + score, 0)
          : null
        return (
          <div class={`score-row ${index === 0 ? 'leader' : ''}`} key={player.id}>
            <span class="rank">{index === 0 ? <Crown size={18} /> : index + 1}</span>
            <span class="player-avatar">{player.name.slice(0, 1).toUpperCase()}</span>
            <span class="player-details"><strong>{player.name}</strong><small>{player.id === state.playerId ? 'That’s you' : index === 0 ? 'Current leader' : 'Still in it'}</small></span>
            {roundTotal !== null && <span class="round-gain">+{roundTotal.toFixed(0)}</span>}
            <strong class="total-score">{Number(scores[player.id] ?? player.score).toFixed(0)}</strong>
          </div>
        )
      })}
    </div>
  )
}

function ResultsScreen({ game, onLeave, sound }: { game: GameActions; onLeave: () => void; sound: boolean }) {
  const { state } = game
  const round = state.round
  const results = state.results
  const [showDetails, setShowDetails] = useState(false)
  if (!round || !results) return null

  return (
    <main class="screen results-screen">
      <div class="screen-topline">
        <span class="round-chip">Round {round.round_number} complete</span>
        <button class="button ghost compact" onClick={onLeave}><LogOut size={17} /> Leave</button>
      </div>
      <section class="results-hero">
        <span class="trophy-orbit"><Trophy size={34} /></span>
        <span class="eyebrow">{results.timeout ? 'Time called' : 'The scores are in'}</span>
        <h1>Round wrapped.</h1>
        <p>Here’s how the table stacks up.</p>
      </section>

      <section class="panel">
        <Scoreboard
          state={state}
          scores={results.cumulative_scores}
          roundScores={results.round_scores}
        />
      </section>

      <section class="panel detail-panel">
        <button class="detail-toggle" onClick={() => setShowDetails((value) => !value)} aria-expanded={showDetails}>
          <span><strong>Answer breakdown</strong><small>See every answer and point</small></span>
          <ChevronDown size={20} class={showDetails ? 'rotated' : ''} />
        </button>
        {showDetails && (
          <div class="round-breakdown">
            {round.categories.map((category) => (
              <section key={category}>
                <h3>{category}</h3>
                {state.players.map((player) => (
                  <div key={player.id}>
                    <span>
                      <b>{player.name}</b>
                      <span class="breakdown-answer">
                        {round.answers[player.id]?.[category] || 'No answer'}
                      </span>
                    </span>
                    <strong>{results.round_scores[player.id]?.[category] ?? 0}</strong>
                  </div>
                ))}
              </section>
            ))}
          </div>
        )}
      </section>

      <div class="sticky-action">
        {state.isHost ? (
          <>
            <button class="button primary large" onClick={() => { playTone(sound, 700); game.nextRound() }}>
              Next round <ArrowRight size={20} />
            </button>
            {results.is_final_round && (
              <button class="button secondary" onClick={() => game.endGame()}>
                <Trophy size={18} /> Finish the game
              </button>
            )}
          </>
        ) : (
          <div class="waiting-message"><span class="spinner" /> Waiting for the host’s call…</div>
        )}
      </div>
    </main>
  )
}

function FinalScreen({ game }: { game: GameActions }) {
  const { state } = game
  const scores = state.finalScores
  const highest = Math.max(...Object.values(scores), 0)
  const winners = state.players.filter((player) => (scores[player.id] ?? 0) === highest)

  return (
    <main class="screen final-screen">
      <section class="final-hero">
        <span class="sparkle"><Sparkles size={32} /></span>
        <span class="eyebrow">That’s the game</span>
        <h1>{winners.length > 1 ? 'A photo finish.' : `${winners[0]?.name ?? 'Someone'} takes it.`}</h1>
        <p>{winners.length > 1 ? `${winners.map((player) => player.name).join(' and ')} share the crown.` : 'Officially the quickest mind at the table.'}</p>
      </section>

      <section class="panel final-scoreboard">
        <Scoreboard state={state} scores={scores} />
      </section>

      <section class="panel history-panel">
        <div class="section-heading">
          <div>
            <span class="eyebrow">Game recap</span>
            <h2>
              {state.history.length} {state.history.length === 1 ? 'round' : 'rounds'} played
            </h2>
          </div>
        </div>
        <div class="history-list">
          {state.history.map((round) => (
            <details key={round.round_number}>
              <summary>
                <span class="history-letter">{round.letter}</span>
                <span class="history-summary-copy">
                  <strong>Round {round.round_number}</strong>
                  <small>{round.categories.join(' · ')}</small>
                </span>
                <ChevronDown size={18} />
              </summary>
              <div class="history-details">
                {round.categories.map((category) => (
                  <section class="history-category" key={category}>
                    <h3>{category}</h3>
                    <div class="history-answers">
                      {state.players.map((player) => (
                        <p class="history-answer" key={player.id}>
                          <span>{player.name}</span>
                          <strong>{round.answers[player.id]?.[category] || '—'}</strong>
                        </p>
                      ))}
                    </div>
                  </section>
                ))}
              </div>
            </details>
          ))}
        </div>
      </section>

      <div class="sticky-action">
        <button class="button primary large" onClick={game.leave}><RotateCcw size={19} /> Back to the lobby</button>
      </div>
    </main>
  )
}

function ConfirmDialog({ onConfirm, onClose }: { onConfirm: () => void; onClose: () => void }) {
  return (
    <div class="dialog-backdrop">
      <section class="dialog-card compact-dialog" role="dialog" aria-modal="true" aria-labelledby="leave-title">
        <span class="danger-icon"><LogOut size={23} /></span>
        <h2 id="leave-title">Leave this game?</h2>
        <p>Your place may be removed after a few minutes, and the host role may pass to someone else.</p>
        <div class="dialog-actions">
          <button class="button secondary" onClick={onClose}>Stay here</button>
          <button class="button danger" onClick={onConfirm}>Leave game</button>
        </div>
      </section>
    </div>
  )
}

export function App() {
  const game = useGame()
  const { state } = game
  const [editingName, setEditingName] = useState(false)
  const [confirmLeave, setConfirmLeave] = useState(false)
  const [theme, setTheme] = useState<'light' | 'dark'>(() =>
    (localStorage.getItem('categories_theme') as 'light' | 'dark' | null) ??
    (matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'),
  )
  const [sound, setSound] = useState(localStorage.getItem('categories_sound') !== 'off')
  const [installPrompt, setInstallPrompt] = useState<DeferredInstallPrompt | null>(null)
  const [installDismissed, setInstallDismissed] = useState(false)
  const isIosInstall =
    /iPad|iPhone|iPod/.test(navigator.userAgent) &&
    !window.matchMedia('(display-mode: standalone)').matches

  useEffect(() => {
    document.documentElement.dataset.theme = theme
    localStorage.setItem('categories_theme', theme)
  }, [theme])

  useEffect(() => {
    const onInstallPrompt = (event: Event) => {
      event.preventDefault()
      setInstallPrompt(event as DeferredInstallPrompt)
    }
    window.addEventListener('beforeinstallprompt', onInstallPrompt)
    return () => window.removeEventListener('beforeinstallprompt', onInstallPrompt)
  }, [])

  const toggleSound = () => {
    setSound((current) => {
      localStorage.setItem('categories_sound', current ? 'off' : 'on')
      playTone(!current, 600)
      return !current
    })
  }

  const screen = useMemo(() => {
    if (state.screen === 'lobby') return <LobbyScreen game={game} sound={sound} />
    if (state.screen === 'waiting') return <WaitingScreen game={game} sound={sound} onLeave={() => setConfirmLeave(true)} />
    if (state.screen === 'countdown') return <CountdownScreen state={state} />
    if (state.screen === 'playing') return <PlayScreen game={game} sound={sound} />
    if (state.screen === 'scoring') return <ScoringScreen game={game} sound={sound} />
    if (state.screen === 'results') return <ResultsScreen game={game} sound={sound} onLeave={() => setConfirmLeave(true)} />
    return <FinalScreen game={game} />
  }, [game, sound, state])

  return (
    <div class={`app-shell phase-${state.screen}`}>
      <AppHeader
        state={state}
        theme={theme}
        sound={sound}
        onTheme={() => setTheme((current) => current === 'light' ? 'dark' : 'light')}
        onSound={toggleSound}
        onEditName={() => setEditingName(true)}
      />
      {screen}

      {(!state.playerName || editingName) && (
        <NameDialog
          initialName={state.playerName}
          onSave={(name) => { game.setPlayerName(name); setEditingName(false) }}
          onClose={state.playerName ? () => setEditingName(false) : undefined}
        />
      )}
      {confirmLeave && (
        <ConfirmDialog
          onClose={() => setConfirmLeave(false)}
          onConfirm={() => { setConfirmLeave(false); game.leave() }}
        />
      )}
      {state.error && (
        <div class="toast error" role="alert">
          <span>{state.error}</span>
          <button class="icon-button" onClick={game.dismissError} aria-label="Dismiss"><X size={18} /></button>
        </div>
      )}
      {state.sessionHijacked && (
        <div class="dialog-backdrop">
          <section class="dialog-card compact-dialog" role="alertdialog" aria-modal="true">
            <span class="danger-icon"><WifiOff size={23} /></span>
            <h2>Game opened elsewhere</h2>
            <p>This player session is active in another tab or device, so this copy has paused.</p>
            <button class="button primary" onClick={game.dismissHijack}>Got it</button>
          </section>
        </div>
      )}
      {!installDismissed && (installPrompt || isIosInstall) && state.screen === 'lobby' && (
        <aside class="install-banner">
          <span class="brand-mark small" aria-hidden="true"><i>C</i><i>A</i><i>T</i><i>S</i></span>
          <span>
            <strong>Add Categories to your phone</strong>
            <small>{isIosInstall ? 'In Safari: Share, then Add to Home Screen.' : 'Faster launches, full-screen game night.'}</small>
          </span>
          {installPrompt && (
            <button
              class="button secondary compact"
              onClick={async () => {
                await installPrompt.prompt()
                await installPrompt.userChoice
                setInstallPrompt(null)
                setInstallDismissed(true)
              }}
            >
              Install
            </button>
          )}
          <button class="icon-button" onClick={() => setInstallDismissed(true)} aria-label="Dismiss install prompt"><X size={17} /></button>
        </aside>
      )}
    </div>
  )
}
