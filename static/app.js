// --- CONFIG ---
const PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
const WS_URL = `${PROTOCOL}//${window.location.host}/ws`;

// --- STATE ---
let socket = null;
let myPlayerId = null; // Will need to infer or get from server (server doesn't explicitly send ID in JOIN response but sends player list. We match name or infer from order? Better if server sent my ID. I'll infer from context or add to payload)
// Actually server sends players list. I need to know which one is ME.
// I will rely on the local name I typed used to simple match or add logic.
// UPDATE: I'll modify the client to just store its local name and try to match, 
// OR better, I'll update the server to send "your_id" in the LOBBY_UPDATE or JOIN response.
// Let's assume standard behavior for now: I'll infer "is_me" from the payload if possible.
// Wait, I designed `LOBBY_UPDATE` to send strictly the room state.
// I'll rely on the server sending generic messages. I'll add a heuristic: 
// The server sends `send_personal_message` for LOBBY_UPDATE properly on join.
// I'll update the logic to store "myName" and use that to highlight.

let myName = "";
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

// --- WEBSOCKET SETUP ---
function connect() {
    socket = new WebSocket(WS_URL);

    socket.onopen = () => {
        document.getElementById('status-bar').classList.add('hidden');
        console.log("Connected to WS");
    };

    socket.onclose = () => {
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
        case "ROUND_START":
            startRound(payload);
            break;
        case "OPPONENT_SUBMITTED":
            handleOpponentSubmitted();
            break;
        case "ROUND_ENDED":
            // Transition to Scoring
            setupScoring(payload);
            break;
        case "ROUND_RESULTS":
            showRoundResults(payload);
            break;
        case "GAME_OVER":
            showFinalResults(payload);
            break;
        case "ERROR":
            alert(payload.message);
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

    let amIHost = false;
    // Heuristic to find self (Server broadcast doesn't strictly ID me, but we know our name)
    // IMPORTANT: In a real app we'd use a session token. Here we match names or just trust flow.
    // For "isHost" check:
    // If I just joined and I am the host, the personalized message said "is_host": true.
    // The payload passed to updateLobby might be the personalized one OR the broadcast one.
    // Let's use the 'is_host' flag if present in the top level of payload (from personal msg).

    if (payload.is_host !== undefined) {
        isHost = payload.is_host;
    }

    // Also if I am in the player list and marked as host
    const me = players.find(p => p.name === myName);
    if (me && me.is_host) isHost = true;

    players.forEach(p => {
        const span = document.createElement('span');
        span.className = 'player-tag';
        span.innerText = p.name;
        if (p.name === myName) {
            span.classList.add('is-me');
            // Store my ID if possible? Models send ID.
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

function startRound(roundData) {
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

    if (timerInterval) clearInterval(timerInterval);
    timerInterval = setInterval(() => {
        timeLeft--;
        timerEl.innerText = timeLeft;
        if (timeLeft <= 10) timerEl.classList.add('warning');
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

    // Show loading or waiting state overlay? For now just disable button
    document.getElementById('btn-submit').innerText = "Submitted! Waiting...";
    document.getElementById('btn-submit').disabled = true;
}

function handleOpponentSubmitted() {
    // Turn visible timer to red and set to 5 seconds if > 5
    if (timeLeft > 5) {
        timeLeft = 5;
        document.getElementById('timer').innerText = timeLeft;
        // The interval is still running, it will naturally tick down from 5
        alert("Opponent submitted! 5 seconds left!");
    }
}

function setupScoring(payload) {
    showScreen('scoring');
    const round = payload.round;
    const players = payload.players; // dict {id: Player}

    // Identify opponent ID
    const pIds = Object.keys(players);
    const opponentId = pIds.find(id => id !== myPlayerId);

    const container = document.getElementById('scoring-container');
    container.innerHTML = '';

    round.categories.forEach((cat, idx) => {
        const row = document.createElement('div');
        row.className = 'scoring-row';

        const myAnswer = round.answers[myPlayerId] ? round.answers[myPlayerId][cat] : "";
        const oppAnswer = round.answers[opponentId] ? round.answers[opponentId][cat] : "";

        // We need to score BOTH answers.
        // Actually typically you primarily score the opponent, but self-scoring works too.
        // Spec says: "Each player scores both themselves and the other player"

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

    // Setup click handlers for score buttons
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
    const scores = {}; // category -> {targetId -> score}

    // Gather logic
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
    const roundScores = payload.round_scores; // playerId -> {cat: score}
    const totalScores = payload.cumulative_scores;

    // Reset buttons
    document.getElementById('btn-submit').disabled = false;
    document.getElementById('btn-submit').innerText = "Submit Answers";
    document.getElementById('btn-submit-scores').disabled = false;
    document.getElementById('btn-submit-scores').innerText = "Submit Scores";

    let html = `<table class="result-table"><thead><tr><th>Player</th><th>Round Score</th><th>Total</th></tr></thead><tbody>`;

    Object.keys(totalScores).forEach(pid => {
        // Calculate round total
        let rTotal = 0;
        if (roundScores[pid]) {
            rTotal = Object.values(roundScores[pid]).reduce((a, b) => a + b, 0);
        }
        // Name lookup - simplified (we need access to players list again? strictly we didn't save it)
        // Ideally we persist players globally.
        // Hack: Use the DOM or saved state. 
        // Better: I will save players map in updateLobby.
        const name = (window.playersMap && window.playersMap[pid]) ? window.playersMap[pid].name : (pid === myPlayerId ? "You" : "Opponent");

        html += `<tr><td>${name}</td><td>+${rTotal}</td><td>${totalScores[pid]}</td></tr>`;
    });
    html += `</tbody></table>`;

    document.getElementById('round-summary').innerHTML = html;

    // Controls
    if (isHost) {
        document.getElementById('next-round-controls').classList.remove('hidden');
        document.getElementById('waiting-next-round').classList.add('hidden');

        if (payload.is_final_round) { // >= 3 rounds
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

    // Event Listeners
    document.getElementById('btn-host').addEventListener('click', () => {
        myName = document.getElementById('host-name').value;
        if (!myName) return alert("Enter name");
        send("JOIN_GAME", { player_name: myName }); // No code = host
    });

    document.getElementById('btn-join').addEventListener('click', () => {
        myName = document.getElementById('join-name').value;
        const code = document.getElementById('join-code').value;
        if (!myName || !code) return alert("Enter name and code");
        send("JOIN_GAME", { player_name: myName, room_code: code });
    });

    document.getElementById('btn-start-game').addEventListener('click', () => {
        send("START_GAME", {});
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
        // Simple reload for now, or implement deeper reset
        window.location.reload();
    });
});

// Helper for player map
// Patch updateLobby to save map
const originalUpdateLobby = updateLobby;
window.playersMap = {};
updateLobby = function (payload) {
    payload.players.forEach(p => {
        window.playersMap[p.id] = p;
    });
    originalUpdateLobby(payload);
}
