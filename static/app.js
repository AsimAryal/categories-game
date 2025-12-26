// --- CONFIG ---
const PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = `${PROTOCOL}//${window.location.host}/ws`;

// --- SESSION STORAGE KEYS ---
const SESSION_TOKEN_KEY = 'game_session_token';
const ROOM_CODE_KEY = 'game_room_code';

// --- SOUND MANAGER ---
const Sound = {
    ctx: null,
    init: function () {
        if (!this.ctx) {
            this.ctx = new (window.AudioContext || window.webkitAudioContext)();
        }
    },
    playTone: function (freq, type, duration, vol = 0.1) {
        if (!this.ctx) this.init();
        if (this.ctx.state === 'suspended') this.ctx.resume();

        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();

        osc.type = type;
        osc.frequency.setValueAtTime(freq, this.ctx.currentTime);

        gain.gain.setValueAtTime(vol, this.ctx.currentTime);
        gain.gain.exponentialRampToValueAtTime(0.01, this.ctx.currentTime + duration);

        osc.connect(gain);
        gain.connect(this.ctx.destination);

        osc.start();
        osc.stop(this.ctx.currentTime + duration);
    },
    tick: function () { this.playTone(800, 'sine', 0.1, 0.1); },
    alert: function () {
        if (!this.ctx) this.init();
        const osc = this.ctx.createOscillator();
        const gain = this.ctx.createGain();
        osc.connect(gain);
        gain.connect(this.ctx.destination);

        osc.type = 'triangle';
        osc.frequency.setValueAtTime(600, this.ctx.currentTime);
        osc.frequency.linearRampToValueAtTime(800, this.ctx.currentTime + 0.1);
        osc.frequency.linearRampToValueAtTime(600, this.ctx.currentTime + 0.2);

        gain.gain.value = 0.2;
        gain.gain.linearRampToValueAtTime(0, this.ctx.currentTime + 0.5);

        osc.start();
        osc.stop(this.ctx.currentTime + 0.5);
    },
    start: function () {
        this.playTone(440, 'sine', 0.1);
        setTimeout(() => this.playTone(660, 'sine', 0.2), 100);
        setTimeout(() => this.playTone(880, 'sine', 0.4), 200);
    },
    end: function () {
        this.playTone(300, 'square', 0.3, 0.2);
        setTimeout(() => this.playTone(200, 'square', 0.3, 0.2), 150);
    },
    reconnect: function () {
        this.playTone(523, 'sine', 0.15);
        setTimeout(() => this.playTone(659, 'sine', 0.15), 100);
    }
};

// --- STATE ---
let socket = null;
let myPlayerId = null;
let myName = localStorage.getItem('player_name') || "";
let currentRoomCode = "";
let currentRound = null;
let timerInterval = null;
let timeLeft = 60;
let isHost = false;
let isReconnecting = false;
let gamesListInterval = null;

// --- DOM ELEMENTS ---
const screens = {
    lobby: document.getElementById('lobby-screen'),
    waiting: document.getElementById('waiting-screen'),
    game: document.getElementById('game-screen'),
    scoring: document.getElementById('scoring-screen'),
    results: document.getElementById('results-screen'),
    final: document.getElementById('final-screen')
};

// --- SESSION MANAGEMENT ---
function saveSession(token, roomCode) {
    localStorage.setItem(SESSION_TOKEN_KEY, token);
    localStorage.setItem(ROOM_CODE_KEY, roomCode);
}

function clearSession() {
    localStorage.removeItem(SESSION_TOKEN_KEY);
    localStorage.removeItem(ROOM_CODE_KEY);
    currentRoomCode = "";
    myPlayerId = null;
    isHost = false;
}

function getSavedSession() {
    return {
        token: localStorage.getItem(SESSION_TOKEN_KEY),
        roomCode: localStorage.getItem(ROOM_CODE_KEY)
    };
}

// --- TOAST ---
function showToast(msg, type = 'info') {
    const container = document.getElementById('toast-container');
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.innerText = msg;
    container.appendChild(el);
    setTimeout(() => {
        el.style.animation = "fadeOut 0.3s forwards";
        setTimeout(() => el.remove(), 300);
    }, 3000);
}

// --- WEBSOCKET SETUP ---
function connect() {
    socket = new WebSocket(WS_URL);

    socket.onopen = () => {
        console.log("Connected to WS");
        document.getElementById('status-bar').classList.add('hidden');

        // Check for existing session to reconnect
        const session = getSavedSession();
        if (session.token) {
            isReconnecting = true;
            document.getElementById('status-bar').innerText = "Reconnecting to game...";
            document.getElementById('status-bar').classList.remove('hidden');
            send("REJOIN_GAME", { session_token: session.token });
        } else {
            showToast("Connected to server", "success");
            send("GET_GAMES", {});
            startGamesListPolling();
        }
    };

    socket.onclose = () => {
        showToast("Disconnected! Reconnecting...", "error");
        document.getElementById('status-bar').innerText = "Disconnected. Reconnecting...";
        document.getElementById('status-bar').classList.remove('hidden');
        stopGamesListPolling();
        setTimeout(connect, 3000);
    };

    socket.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
    };
}

function startGamesListPolling() {
    if (gamesListInterval) clearInterval(gamesListInterval);
    gamesListInterval = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN && !currentRoomCode) {
            send("GET_GAMES", {});
        }
    }, 2000);
}

function stopGamesListPolling() {
    if (gamesListInterval) {
        clearInterval(gamesListInterval);
        gamesListInterval = null;
    }
}

function send(type, payload) {
    if (socket && socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type, payload }));
    }
}

// --- MESSAGE HANDLING ---
function handleMessage(msg) {
    console.log("RX:", msg);
    const payload = msg.payload;

    switch (msg.type) {
        case "LOBBY_UPDATE":
            // Only switch to waiting screen if we're in lobby or already there
            // Don't disrupt active gameplay
            const currentScreen = getCurrentScreen();
            if (currentScreen === 'lobby' || currentScreen === 'waiting') {
                showScreen('waiting');
            }
            updateLobby(payload);
            break;

        case "GAMES_LIST":
            renderGamesList(payload.games);
            break;

        case "ROUND_START":
            startRound(payload);
            break;

        case "OPPONENT_SUBMITTED":
            handleOpponentSubmitted(payload);
            break;

        case "ROUND_ENDED":
            Sound.end();
            document.body.classList.remove('rush-mode');
            setupScoring(payload);
            break;

        case "ROUND_RESULTS":
            showRoundResults(payload);
            break;

        case "GAME_OVER":
            showFinalResults(payload);
            break;

        case "ERROR":
            showToast(payload.message, "error");
            // If session expired, clear it
            if (payload.code === "SESSION_EXPIRED") {
                clearSession();
                showScreen('lobby');
                send("GET_GAMES", {});
                startGamesListPolling();
            }
            isReconnecting = false;
            document.getElementById('status-bar').classList.add('hidden');
            break;

        // --- NEW: Reconnection messages ---
        case "RECONNECTED":
            handleReconnected(payload);
            break;

        case "SESSION_HIJACKED":
            showToast("Session opened in another tab", "error");
            clearSession();
            socket.close();
            break;

        case "PLAYER_DISCONNECTED":
            showToast(`${payload.player_name} disconnected`, "info");
            break;

        case "PLAYER_RECONNECTED":
            showToast(`${payload.player_name} reconnected!`, "success");
            Sound.reconnect();
            break;

        case "HOST_CHANGED":
            showToast(`${payload.new_host_name} is now the host`, "info");
            // Update our host status if we're the new host
            if (payload.new_host_id === myPlayerId) {
                isHost = true;
                showToast("You are now the host!", "success");
            } else {
                isHost = false;
            }
            // Update host controls on results screen if we're there
            updateHostControlsVisibility();
            break;

        case "SCORING_TIMEOUT":
            showToast("Scoring time almost up!", "error");
            break;
    }
}

// --- RECONNECTION HANDLER ---
function handleReconnected(payload) {
    isReconnecting = false;
    document.getElementById('status-bar').classList.add('hidden');

    Sound.reconnect();
    showToast("Reconnected to game!", "success");

    // Restore state
    currentRoomCode = payload.room_code;
    myPlayerId = payload.player_id;
    isHost = payload.is_host;

    // Update players map
    if (payload.players) {
        window.playersMap = {};
        payload.players.forEach(p => {
            window.playersMap[p.id] = p;
        });
    }

    // Save session (might be same, but ensures consistency)
    saveSession(payload.session_token, payload.room_code);

    // Restore to correct screen based on game state
    switch (payload.game_state) {
        case "LOBBY":
            showScreen('waiting');
            updateLobby(payload);
            break;

        case "PLAYING":
            if (payload.round) {
                currentRound = payload.round;
                showScreen('game');
                restorePlayingState(payload);
            }
            break;

        case "SCORING":
            if (payload.round) {
                showScreen('scoring');
                restoreScoringState(payload);
            }
            break;

        case "ROUND_RESULTS":
            showScreen('results');
            showRoundResults(payload);
            break;

        case "FINAL_RESULTS":
            showScreen('final');
            showFinalResults(payload);
            break;
    }
}

function restorePlayingState(payload) {
    const roundData = payload.round;
    currentRound = roundData;

    document.getElementById('current-letter').innerText = roundData.letter;

    const container = document.getElementById('categories-container');
    container.innerHTML = '';

    roundData.categories.forEach((cat, idx) => {
        const div = document.createElement('div');
        div.className = 'category-input';

        // Restore previously entered answers if any
        const savedAnswer = payload.my_answers ? (payload.my_answers[cat] || '') : '';

        div.innerHTML = `
            <label class="category-label">${idx + 1}. ${cat}</label>
            <input type="text" data-category="${cat}" placeholder="${roundData.letter}..." autocomplete="off" value="${savedAnswer}">
        `;
        container.appendChild(div);
    });

    // Restore timer from server time
    timeLeft = payload.remaining_time || 60;
    const timerEl = document.getElementById('timer');
    timerEl.innerText = timeLeft;
    timerEl.classList.remove('warning');

    // Check if already submitted
    if (payload.my_answers && Object.keys(payload.my_answers).length > 0) {
        document.getElementById('btn-submit').innerText = "Submitted! Waiting...";
        document.getElementById('btn-submit').disabled = true;
    } else {
        document.getElementById('btn-submit').innerText = "Submit Answers";
        document.getElementById('btn-submit').disabled = false;

        // Start timer
        if (timerInterval) clearInterval(timerInterval);
        timerInterval = setInterval(() => {
            timeLeft--;
            timerEl.innerText = timeLeft;

            if (timeLeft <= 10 && timeLeft > 0) {
                Sound.tick();
                timerEl.classList.add('warning');
            }

            if (timeLeft <= 0) {
                clearInterval(timerInterval);
                submitAnswers();
            }
        }, 1000);
    }
}

function restoreScoringState(payload) {
    const round = payload.round;

    document.getElementById('scoring-subtitle').innerText = `Rate the answers! (Letter: ${round.letter})`;

    if (payload.scores_submitted) {
        // Already submitted scores, just show waiting message
        document.getElementById('btn-submit-scores').innerText = "Waiting...";
        document.getElementById('btn-submit-scores').disabled = true;
    } else {
        document.getElementById('btn-submit-scores').innerText = "Submit Scores";
        document.getElementById('btn-submit-scores').disabled = false;
    }

    // Build scoring UI
    const pIds = Object.keys(window.playersMap || {});
    const opponents = pIds.filter(id => id !== myPlayerId);

    const container = document.getElementById('scoring-container');
    container.innerHTML = '';

    round.categories.forEach((cat, idx) => {
        const row = document.createElement('div');
        row.className = 'scoring-row';

        const myAnswer = round.answers[myPlayerId] ? round.answers[myPlayerId][cat] : "";

        let answersHtml = `
            <div class="answer-block" style="border-left: 5px solid var(--primary-color);">
                <div>
                    <small style="color:var(--primary-color); font-weight:bold;">You</small><br>
                    <span class="answer-text">${myAnswer || "<em>(Empty)</em>"}</span>
                </div>
                <div style="font-size:12px; color:#888;">(Your Answer)</div>
            </div>
        `;

        opponents.forEach(oppId => {
            const oppName = window.playersMap[oppId] ? window.playersMap[oppId].name : "Unknown";
            const oppAnswer = round.answers[oppId] ? round.answers[oppId][cat] : "";

            answersHtml += `
            <div class="answer-block">
                <div>
                    <small>${oppName}</small><br>
                    <span class="answer-text">${oppAnswer || "<em>(Empty)</em>"}</span>
                </div>
                ${renderScoreControls(cat, oppId)}
            </div>
            `;
        });

        row.innerHTML = `
            <div class="scoring-category">${cat}</div>
            <div class="answer-comparison">
                ${answersHtml}
            </div>
        `;
        container.appendChild(row);
    });

    // Add click handlers for score buttons
    document.querySelectorAll('.score-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const group = e.target.closest('.score-control');
            group.querySelectorAll('.score-btn').forEach(b => b.classList.remove('selected'));
            e.target.classList.add('selected');
        });
    });
}

// --- LOGIC ---

function showScreen(screenName) {
    Object.values(screens).forEach(el => el.classList.add('hidden'));
    screens[screenName].classList.remove('hidden');
    window.scrollTo(0, 0);
}

function getCurrentScreen() {
    for (const [name, el] of Object.entries(screens)) {
        if (!el.classList.contains('hidden')) {
            return name;
        }
    }
    return null;
}

function updateHostControlsVisibility() {
    // Update results screen controls
    const currentScreen = getCurrentScreen();

    if (currentScreen === 'results') {
        if (isHost) {
            document.getElementById('next-round-controls').classList.remove('hidden');
            document.getElementById('waiting-next-round').classList.add('hidden');
            document.getElementById('btn-end-game').classList.remove('hidden');
        } else {
            document.getElementById('next-round-controls').classList.add('hidden');
            document.getElementById('waiting-next-round').classList.remove('hidden');
            document.getElementById('btn-end-game').classList.add('hidden');
        }
    }

    if (currentScreen === 'waiting') {
        const hostControls = document.getElementById('host-controls');
        const waitingMsg = document.getElementById('waiting-msg');

        if (isHost) {
            hostControls.classList.remove('hidden');
            waitingMsg.classList.add('hidden');
        } else {
            hostControls.classList.add('hidden');
            waitingMsg.classList.remove('hidden');
        }
    }
}

function updateLobby(payload) {
    currentRoomCode = payload.room_code;
    const players = payload.players;

    // Save session token if provided
    if (payload.session_token) {
        saveSession(payload.session_token, payload.room_code);
    }

    document.getElementById('display-room-code').innerText = currentRoomCode;

    const list = document.getElementById('player-list');
    list.innerHTML = '';

    if (payload.is_host !== undefined) {
        isHost = payload.is_host;
    }

    const me = players.find(p => p.name === myName);
    if (me && me.is_host) isHost = true;

    players.forEach(p => {
        const span = document.createElement('span');
        span.className = 'player-tag';
        span.innerText = p.name;

        // Show connection status
        if (!p.is_connected) {
            span.classList.add('disconnected');
            span.innerText += ' (...)';
        }

        if (p.name === myName) {
            span.classList.add('is-me');
            myPlayerId = p.id;
        }
        list.appendChild(span);
    });

    // Sync settings if they are in payload
    if (payload.settings) {
        const rushInput = document.getElementById('config-rush-time');
        const preciseInput = document.getElementById('config-precise-scoring');

        if (payload.settings.rush_seconds !== undefined && document.activeElement !== rushInput) {
            rushInput.value = payload.settings.rush_seconds;
        }
        if (payload.settings.precise_scoring !== undefined) {
            preciseInput.checked = payload.settings.precise_scoring;
        }
    }

    // Host controls logic
    const hostControls = document.getElementById('host-controls');
    const waitingMsg = document.getElementById('waiting-msg');
    const connectedCount = players.filter(p => p.is_connected).length;

    if (isHost && connectedCount >= 2) {
        hostControls.classList.remove('hidden');
        waitingMsg.classList.add('hidden');
    } else {
        hostControls.classList.add('hidden');
        waitingMsg.classList.remove('hidden');
        if (connectedCount < 2) {
            waitingMsg.innerText = `Waiting for players... (${connectedCount}/5)`;
        } else {
            waitingMsg.innerText = `Waiting for host to start... (${connectedCount}/5)`;
        }
    }

    // Stop polling when in a room
    stopGamesListPolling();
}

function renderGamesList(games) {
    const list = document.getElementById('games-list');
    const container = document.getElementById('games-list-container');
    list.innerHTML = '';

    if (games.length === 0) {
        container.classList.add('hidden');
        return;
    }

    container.classList.remove('hidden');
    games.forEach(game => {
        const div = document.createElement('div');
        div.className = 'game-item';
        div.innerHTML = `
            <div class="game-info">
                <span class="game-host">${game.host_name}'s Game</span>
                <span style="color:#888; font-size:12px;">(${game.player_count}/5)</span>
            </div>
            <button style="width: auto; padding: 5px 10px; font-size: 12px;">Join</button>
        `;
        div.addEventListener('click', () => {
            if (!myName) {
                showToast("Please enter your name first!", "error");
                document.getElementById('name-modal').classList.remove('hidden');
                return;
            }

            document.getElementById('join-code').value = game.code;
            send("JOIN_GAME", { player_name: myName, room_code: game.code });
        });
        list.appendChild(div);
    });
}


function startRound(roundData) {
    Sound.start();
    document.body.classList.remove('rush-mode');
    currentRound = roundData;
    showScreen('game');

    document.getElementById('current-letter').innerText = roundData.letter;

    const container = document.getElementById('categories-container');
    container.innerHTML = '';

    roundData.categories.forEach((cat, idx) => {
        const div = document.createElement('div');
        div.className = 'category-input';
        div.innerHTML = `
            <label class="category-label">${idx + 1}. ${cat}</label>
            <input type="text" data-category="${cat}" placeholder="${roundData.letter}..." autocomplete="off">
        `;
        container.appendChild(div);
    });

    // Reset submit button
    document.getElementById('btn-submit').innerText = "Submit Answers";
    document.getElementById('btn-submit').disabled = false;

    // Timer
    timeLeft = 60;
    const timerEl = document.getElementById('timer');
    timerEl.innerText = timeLeft;
    timerEl.classList.remove('warning');

    // Start interval
    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(() => {
        timeLeft--;
        timerEl.innerText = timeLeft;

        if (timeLeft <= 10 && timeLeft > 0) {
            Sound.tick();
            timerEl.classList.add('warning');
        }

        if (timeLeft <= 0) {
            clearInterval(timerInterval);
            submitAnswers();
        }
    }, 1000);
}

function submitAnswers() {
    if (timerInterval) clearInterval(timerInterval);

    const inputs = document.querySelectorAll('#categories-container input');
    const answers = {};
    inputs.forEach(inp => {
        answers[inp.dataset.category] = inp.value.trim();
    });

    send("SUBMIT_ANSWERS", { answers });

    document.getElementById('btn-submit').innerText = "Submitted! Waiting...";
    document.getElementById('btn-submit').disabled = true;
    document.body.classList.remove('rush-mode');
}

function handleOpponentSubmitted(payload) {
    const rushSec = payload.rush_seconds || 5;

    if (timeLeft > rushSec) {
        Sound.alert();
        timeLeft = rushSec;
        document.getElementById('timer').innerText = timeLeft;
        document.body.classList.add('rush-mode');
        showToast(`Opponent submitted! ${rushSec}s RUSH!`, "error");
    }
}

function setupScoring(payload) {
    showScreen('scoring');
    const round = payload.round;
    const players = payload.players;

    // Update players map
    window.playersMap = players;

    document.getElementById('scoring-subtitle').innerText = `Rate the answers! (Letter: ${round.letter})`;

    // Reset submit button
    document.getElementById('btn-submit-scores').innerText = "Submit Scores";
    document.getElementById('btn-submit-scores').disabled = false;

    const pIds = Object.keys(players);
    const opponents = pIds.filter(id => id !== myPlayerId);

    const container = document.getElementById('scoring-container');
    container.innerHTML = '';

    round.categories.forEach((cat, idx) => {
        const row = document.createElement('div');
        row.className = 'scoring-row';

        const myAnswer = round.answers[myPlayerId] ? round.answers[myPlayerId][cat] : "";

        let answersHtml = `
            <div class="answer-block" style="border-left: 5px solid var(--primary-color);">
                <div>
                    <small style="color:var(--primary-color); font-weight:bold;">You</small><br>
                    <span class="answer-text">${myAnswer || "<em>(Empty)</em>"}</span>
                </div>
                <div style="font-size:12px; color:#888;">(Your Answer)</div>
            </div>
        `;

        opponents.forEach(oppId => {
            const oppName = players[oppId] ? players[oppId].name : "Unknown";
            const oppAnswer = (round.answers[oppId]) ? round.answers[oppId][cat] : "";

            answersHtml += `
            <div class="answer-block">
                <div>
                    <small>${oppName}</small><br>
                    <span class="answer-text">${oppAnswer || "<em>(Empty)</em>"}</span>
                </div>
                ${renderScoreControls(cat, oppId)}
            </div>
            `;
        });

        const html = `
            <div class="scoring-category">${cat}</div>
            <div class="answer-comparison">
                ${answersHtml}
            </div>
        `;
        row.innerHTML = html;
        container.appendChild(row);
    });

    document.querySelectorAll('.score-btn').forEach(btn => {
        btn.addEventListener('click', (e) => {
            const group = e.target.closest('.score-control');
            group.querySelectorAll('.score-btn').forEach(b => b.classList.remove('selected'));
            e.target.classList.add('selected');
        });
    });
}

function renderScoreControls(category, targetPlayerId) {
    return `
        <div class="score-control" data-cat="${category}" data-target="${targetPlayerId}">
            <button class="score-btn" data-val="0">0</button>
            <button class="score-btn" data-val="1">1</button>
            <button class="score-btn selected" data-val="2">2</button>
        </div>
    `;
}

function submitScores() {
    const scores = {};
    document.querySelectorAll('.score-control').forEach(ctrl => {
        const cat = ctrl.dataset.cat;
        const target = ctrl.dataset.target;
        const val = parseInt(ctrl.querySelector('.selected').dataset.val);
        if (!scores[cat]) scores[cat] = {};
        scores[cat][target] = val;
    });

    send("SUBMIT_SCORES", { scores });
    document.getElementById('btn-submit-scores').innerText = "Waiting...";
    document.getElementById('btn-submit-scores').disabled = true;
}

function showRoundResults(payload) {
    showScreen('results');
    const roundScores = payload.round_scores;
    const totalScores = payload.cumulative_scores;

    document.getElementById('btn-submit').disabled = false;
    document.getElementById('btn-submit').innerText = "Submit Answers";
    document.getElementById('btn-submit-scores').disabled = false;
    document.getElementById('btn-submit-scores').innerText = "Submit Scores";

    let html = `<table class="result-table"><thead><tr><th>Player</th><th>Round Score</th><th>Total</th></tr></thead><tbody>`;
    Object.keys(totalScores).forEach(pid => {
        let rTotal = 0;
        if (roundScores[pid]) {
            rTotal = Object.values(roundScores[pid]).reduce((a, b) => a + b, 0);
        }
        const name = (window.playersMap && window.playersMap[pid]) ? window.playersMap[pid].name : (pid === myPlayerId ? "You" : "Opponent");
        // Format score with 1 decimal if needed
        const displayRTotal = Number.isInteger(rTotal) ? rTotal : rTotal.toFixed(1);
        const displayTotal = Number.isInteger(totalScores[pid]) ? totalScores[pid] : totalScores[pid].toFixed(1);
        html += `<tr><td>${name}</td><td>+${displayRTotal}</td><td>${displayTotal}</td></tr>`;
    });
    html += `</tbody></table>`;

    if (payload.timeout) {
        html += `<p style="color: var(--warning-color); text-align: center; margin-top: 10px;">⏱️ Scoring completed due to timeout</p>`;
    }

    document.getElementById('round-summary').innerHTML = html;

    if (isHost) {
        document.getElementById('next-round-controls').classList.remove('hidden');
        document.getElementById('waiting-next-round').classList.add('hidden');

        if (payload.is_final_round) {
            document.getElementById('btn-end-game').classList.remove('hidden');
        }
    } else {
        document.getElementById('next-round-controls').classList.add('hidden');
        document.getElementById('waiting-next-round').classList.remove('hidden');
    }
}

function showFinalResults(payload) {
    showScreen('final');

    // Clear session since game is over
    clearSession();

    let html = "";
    Object.entries(payload.final_scores).forEach(([pid, score]) => {
        const name = (window.playersMap && window.playersMap[pid]) ? window.playersMap[pid].name : "Player";
        const displayScore = Number.isInteger(score) ? score : score.toFixed(1);
        html += `<div style="text-align:center;">${name}: <span class="final-score-big">${displayScore}</span></div>`;
    });
    document.getElementById('final-scores').innerHTML = html;
}

// --- BOOTSTRAP ---
window.addEventListener('DOMContentLoaded', () => {
    connect();

    // --- NAME MODAL LOGIC ---
    const nameModal = document.getElementById('name-modal');
    const modalNameInput = document.getElementById('modal-name-input');
    const displayPlayerName = document.getElementById('display-player-name');

    if (!myName) {
        nameModal.classList.remove('hidden');
    } else {
        displayPlayerName.innerText = myName;
    }

    document.getElementById('btn-modal-save').addEventListener('click', () => {
        const name = modalNameInput.value.trim();
        if (!name) return showToast("Please enter your name", "error");
        localStorage.setItem('player_name', name);
        myName = name;
        nameModal.classList.add('hidden');
        displayPlayerName.innerText = myName;
    });

    modalNameInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            document.getElementById('btn-modal-save').click();
        }
    });

    document.getElementById('btn-change-name').addEventListener('click', () => {
        modalNameInput.value = myName;
        nameModal.classList.remove('hidden');
        modalNameInput.focus();
    });

    // --- TAB SWITCHING LOGIC ---
    document.querySelectorAll('.tab-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
            document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
            btn.classList.add('active');
            document.getElementById(`tab-${btn.dataset.tab}`).classList.add('active');
        });
    });

    // --- GAME EVENT LISTENERS ---
    document.getElementById('btn-host').addEventListener('click', () => {
        if (!myName) {
            showToast("Please enter your name first", "error");
            nameModal.classList.remove('hidden');
            return;
        }
        send("JOIN_GAME", { player_name: myName });
    });

    document.getElementById('btn-join').addEventListener('click', () => {
        const code = document.getElementById('join-code').value.trim().toUpperCase();
        if (!myName) {
            showToast("Please enter your name first", "error");
            nameModal.classList.remove('hidden');
            return;
        }
        if (!code) return showToast("Enter a room code", "error");
        send("JOIN_GAME", { player_name: myName, room_code: code });
    });

    document.getElementById('join-code').addEventListener('keypress', (e) => {
        if (e.key === 'Enter') {
            document.getElementById('btn-join').click();
        }
    });

    document.getElementById('btn-start-game').addEventListener('click', () => {
        send("START_GAME", {}); // Server now uses stored settings
    });

    document.getElementById('config-rush-time').addEventListener('change', (e) => {
        if (isHost) {
            send("UPDATE_SETTINGS", { rush_seconds: e.target.value });
        }
    });

    document.getElementById('config-precise-scoring').addEventListener('change', (e) => {
        if (isHost) {
            send("UPDATE_SETTINGS", { precise_scoring: e.target.checked });
        }
    });

    document.getElementById('btn-submit').addEventListener('click', submitAnswers);
    document.getElementById('btn-submit-scores').addEventListener('click', submitScores);

    document.getElementById('btn-next-round').addEventListener('click', () => {
        send("NEXT_ROUND", {});
    });

    document.getElementById('btn-end-game').addEventListener('click', () => {
        send("END_GAME", {});
    });

    document.getElementById('btn-play-again').addEventListener('click', () => {
        clearSession();
        window.location.reload();
    });

    document.getElementById('btn-leave-game').addEventListener('click', () => {
        if (confirm("Are you sure you want to leave this game?")) {
            send("LEAVE_GAME", {});  // Tell server we're leaving
            clearSession();
            currentRoomCode = "";
            myPlayerId = null;
            isHost = false;
            showToast("Left the game", "info");
            showScreen('lobby');
            send("GET_GAMES", {});
            startGamesListPolling();
        }
    });

    // Leave button on results screen (between rounds)
    document.getElementById('btn-leave-results').addEventListener('click', () => {
        if (confirm("Are you sure you want to leave this game?")) {
            send("LEAVE_GAME", {});
            clearSession();
            currentRoomCode = "";
            myPlayerId = null;
            isHost = false;
            showToast("Left the game", "info");
            showScreen('lobby');
            send("GET_GAMES", {});
            startGamesListPolling();
        }
    });

    // --- DARK MODE LOGIC ---
    const themeBtn = document.getElementById('theme-toggle');
    const body = document.body;

    if (localStorage.getItem('theme') === 'dark') {
        body.classList.add('dark-mode');
        themeBtn.innerText = "☀️";
    }

    themeBtn.addEventListener('click', () => {
        body.classList.toggle('dark-mode');
        if (body.classList.contains('dark-mode')) {
            localStorage.setItem('theme', 'dark');
            themeBtn.innerText = "☀️";
        } else {
            localStorage.setItem('theme', 'light');
            themeBtn.innerText = "🌙";
        }
    });

    // Audio context resume on first interaction
    document.body.addEventListener('click', () => {
        if (Sound.ctx && Sound.ctx.state === 'suspended') Sound.ctx.resume();
    }, { once: true });

    // --- MOBILE VISIBILITY CHANGE (Background tab recovery) ---
    document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'visible') {
            if (socket.readyState !== WebSocket.OPEN) {
                console.log("Tab visible, reconnecting...");
                connect();
            }
        }
    });

    // --- PREVENT ACCIDENTAL NAVIGATION ---
    window.addEventListener('beforeunload', (e) => {
        if (currentRoomCode) {
            e.preventDefault();
            e.returnValue = '';
        }
    });
});

// Helper for player map
const originalUpdateLobby = updateLobby;
window.playersMap = {};
updateLobby = function (payload) {
    payload.players.forEach(p => {
        window.playersMap[p.id] = p;
    });
    originalUpdateLobby(payload);
}
