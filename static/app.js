// --- CONFIG ---
const PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = `${PROTOCOL}//${window.location.host}/ws`;

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
        // Siren effect
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

// --- DOM ELEMENTS ---
const screens = {
    lobby: document.getElementById('lobby-screen'),
    waiting: document.getElementById('waiting-screen'),
    game: document.getElementById('game-screen'),
    scoring: document.getElementById('scoring-screen'),
    results: document.getElementById('results-screen'),
    final: document.getElementById('final-screen')
};

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
        showToast("Connected to server", "success");
        document.getElementById('status-bar').classList.add('hidden');
        console.log("Connected to WS");
        // Request games list
        send("GET_GAMES", {});

        // Poll for games list every 2 seconds (safeguard)
        setInterval(() => {
            if (socket.readyState === WebSocket.OPEN && !currentRoomCode) {
                send("GET_GAMES", {});
            }
        }, 2000);
    };

    socket.onclose = () => {
        showToast("Disconnected! Reconnecting...", "error");
        document.getElementById('status-bar').innerText = "Disconnected. Reconnecting...";
        document.getElementById('status-bar').classList.remove('hidden');
        setTimeout(connect, 3000);
    };

    socket.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
    };
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
            showScreen('waiting');
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
            document.body.classList.remove('rush-mode'); // Clear rush
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
            break;
    }
}

// --- LOGIC ---

function showScreen(screenName) {
    Object.values(screens).forEach(el => el.classList.add('hidden'));
    screens[screenName].classList.remove('hidden');
    window.scrollTo(0, 0);
}

function updateLobby(payload) {
    currentRoomCode = payload.room_code;
    const players = payload.players;

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
        if (p.name === myName) {
            span.classList.add('is-me');
            myPlayerId = p.id;
        }
        list.appendChild(span);
    });

    // Host controls
    const hostControls = document.getElementById('host-controls');
    const waitingMsg = document.getElementById('waiting-msg');

    if (isHost && players.length >= 2) {
        hostControls.classList.remove('hidden');
        waitingMsg.classList.add('hidden');
    } else {
        hostControls.classList.add('hidden');
        waitingMsg.classList.remove('hidden');
        if (players.length < 2) waitingMsg.innerText = "Waiting for Player 2...";
        else waitingMsg.innerText = "Waiting for host to start...";
    }
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
                <span style="color:#888; font-size:12px;">(${game.player_count}/2)</span>
            </div>
            <button style="width: auto; padding: 5px 10px; font-size: 12px;">Join</button>
        `;
        div.addEventListener('click', () => {
            const nameInput = document.getElementById('join-name');
            const name = nameInput.value.trim();

            if (!name) {
                showToast("Please enter your name above to join!", "error");
                nameInput.focus();
                return;
            }

            // Instant join
            document.getElementById('join-code').value = game.code;
            localStorage.setItem('player_name', name);
            myName = name; // Update global
            send("JOIN_GAME", { player_name: name, room_code: game.code });
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

        // Sound constraints: Tick every sec when < 10
        if (timeLeft <= 10 && timeLeft > 0) {
            Sound.tick();
            timerEl.classList.add('warning');
        }

        if (timeLeft <= 0) {
            clearInterval(timerInterval);
            submitAnswers(); // Auto submit
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
    document.body.classList.remove('rush-mode'); // Clear if we finished
}

function handleOpponentSubmitted(payload) {
    // payload has rush_seconds
    const rushSec = payload.rush_seconds || 5;

    // Check if we still have time
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

    // Update Subtitle with Letter
    document.getElementById('scoring-subtitle').innerText = `Rate the answers! (Letter: ${round.letter})`;

    // Identify opponent ID
    const pIds = Object.keys(players);
    const opponentId = pIds.find(id => id !== myPlayerId);

    const container = document.getElementById('scoring-container');
    container.innerHTML = '';

    round.categories.forEach((cat, idx) => {
        const row = document.createElement('div');
        row.className = 'scoring-row';

        const myAnswer = round.answers[myPlayerId] ? round.answers[myPlayerId][cat] : "";
        const oppAnswer = (opponentId && round.answers[opponentId]) ? round.answers[opponentId][cat] : "";

        const html = `
            <div class="scoring-category">${cat}</div>
            <div class="answer-comparison">
                <div class="answer-block">
                    <div>
                        <small>You</small><br>
                        <span class="answer-text">${myAnswer || "<em>(Empty)</em>"}</span>
                    </div>
                    ${renderScoreControls(cat, myPlayerId)}
                </div>
                 <div class="answer-block">
                    <div>
                        <small>${opponentId ? players[opponentId].name : "Left Game"}</small><br>
                        <span class="answer-text">${oppAnswer || "<em>(Empty)</em>"}</span>
                    </div>
                    ${opponentId ? renderScoreControls(cat, opponentId) : "<em>N/A</em>"}
                </div>
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
        html += `<tr><td>${name}</td><td>+${rTotal}</td><td>${totalScores[pid]}</td></tr>`;
    });
    html += `</tbody></table>`;
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
    // payload.final_scores, payload.history

    let html = "";
    Object.entries(payload.final_scores).forEach(([pid, score]) => {
        const name = (window.playersMap && window.playersMap[pid]) ? window.playersMap[pid].name : "Player";
        html += `<div style="text-align:center;">${name}: <span class="final-score-big">${score}</span></div>`;
    });
    document.getElementById('final-scores').innerHTML = html;
}

// --- BOOTSTRAP ---
window.addEventListener('DOMContentLoaded', () => {
    connect();

    // Auto-fill inputs
    if (myName) {
        document.getElementById('host-name').value = myName;
        document.getElementById('join-name').value = myName;
    }

    // Event Listeners
    document.getElementById('btn-host').addEventListener('click', () => {
        myName = document.getElementById('host-name').value;
        if (!myName) return showToast("Enter name", "error");
        localStorage.setItem('player_name', myName);
        send("JOIN_GAME", { player_name: myName });
    });

    document.getElementById('btn-join').addEventListener('click', () => {
        myName = document.getElementById('join-name').value;
        const code = document.getElementById('join-code').value;
        if (!myName || !code) return showToast("Enter name and code", "error");
        localStorage.setItem('player_name', myName);
        send("JOIN_GAME", { player_name: myName, room_code: code });
    });

    document.getElementById('btn-start-game').addEventListener('click', () => {
        const rush = document.getElementById('config-rush-time').value;
        send("START_GAME", { rush_seconds: rush });
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
        window.location.reload();
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
