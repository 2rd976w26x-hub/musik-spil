let localTimer = null;

function updateTimerText(){
  const tt = el('timerText');
  if(!tt || !state) return;

  if(state.status !== 'round'){
    tt.innerText = '';
    return;
  }

  if(state.round_started_at){
    const left = Math.max(0, Math.ceil(
      (state.timer_seconds || 0) - (Date.now()/1000 - state.round_started_at)
    ));
    tt.innerText = 'Tid tilbage: ' + left + 's';
  } else {
    tt.innerText = 'Venter på DJ…';
  }
}

function startLocalTimer(){
  stopLocalTimer();
  updateTimerText();
  localTimer = setInterval(updateTimerText, 250);
}

function stopLocalTimer(){
  if(localTimer){
    clearInterval(localTimer);
    localTimer = null;
  }
}

let coverTimer = null;
const covers = [
  'covers/cover1.svg',
  'covers/cover2.svg',
  'covers/cover3.svg',
  'covers/cover4.svg',
  'covers/cover5.svg'
];

function startCoverRotation(){
  const img = document.getElementById('coverImg');
  if(!img) return;
  stopCoverRotation();
  let idx = Math.floor(Math.random()*covers.length);
  img.src = covers[idx];
  coverTimer = setInterval(()=>{
    idx = (idx + 1) % covers.length;
    img.src = covers[idx];
  }, 3000);
}

function stopCoverRotation(){
  if(coverTimer){
    clearInterval(coverTimer);
    coverTimer = null;
  }
}

let room=null, player=null, state=null;

// Stable per-device id (used to avoid joining the same room multiple times per device
// and for simple anonymous server stats). Stored in localStorage.
function getDeviceId(){
  const k='piratwhist_device_id';
  let id=localStorage.getItem(k);
  if(!id){
    // Prefer a UUID, but fall back to a simple random string.
    id=(crypto && crypto.randomUUID) ? crypto.randomUUID() : (Math.random().toString(16).slice(2)+Date.now().toString(16));
    localStorage.setItem(k,id);
  }
  return id;
}
let categories = [];

function el(id){ return document.getElementById(id); }

function setNet(ok){
  const ns = document.getElementById('netStatus');
  if(!ns) return;
  ns.innerText = ok ? 'Online' : 'Forbinder…';
  ns.className = ok ? 'pill pillNeutral' : 'pill';
}


async function loadCategories(){
  try{
    const r = await api({action:'categories'});
    categories = (r.categories || []);
  }catch(e){
    categories = [];
  }
}

function populateCategorySelect(selected){
  const sel = document.getElementById('categorySelect');
  if(!sel) return;
  sel.innerHTML = '';
  const list = (state && state.available_categories && state.available_categories.length)
    ? state.available_categories : categories;
  (list || ['Standard']).forEach(c=>{
    const opt = document.createElement('option');
    opt.value = c;
    opt.innerText = c;
    if(selected && c === selected) opt.selected = true;
    sel.appendChild(opt);
  });
}

async function loadVersion(){
  try{
    const r = await api({action:'version'});
    const v = (r && r.version) ? r.version : 'v1.4.39-github-ready';
    const vt = document.getElementById('versionText');
    if(vt) vt.innerText = v;
  }catch(e){
    const vt = document.getElementById('versionText');
    if(vt) vt.innerText = 'v1.4.39-github-ready';
  }
}

async function api(d){
  // Attach device id to all API calls.
  if(d && typeof d==='object' && !d.device_id){
    d.device_id = getDeviceId();
  }
  const r = await fetch('/api',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body:JSON.stringify(d)
  });
  const data = await r.json().catch(()=> ({}));
  if(!r.ok){
    const msg = data && data.error ? data.error : ('http_'+r.status);
    throw new Error(msg);
  }
  return data;
}

function show(id){
  ['view-lobby','view-round','view-result','view-end']
    .forEach(v=>el(v).classList.add('hidden'));
  el(id).classList.remove('hidden');
}

function render(){
  if(!state){
    renderLiveScore(null);
    show('view-lobby');
    // Reset lobby UI when leaving a room
    renderLobby();
    const lb = document.getElementById('leaveRoomBtn');
    if(lb) lb.classList.add('hidden');
    return;
  }

  // Always keep the live stilling up to date
  renderLiveScore(state);

  if(!state.started){
    stopLocalTimer();
    show('view-lobby');
    const hp = el('historyPanel');
    if(hp) hp.classList.add('hidden');
    renderLobby();
    return;
  }
  const hp = el('historyPanel');
  if(hp) hp.classList.remove('hidden');

  if(state.status==='round'){
    show('view-round');
    renderRound();
  } else if(state.status==='round_result'){
    show('view-result');
    renderResult();
  } else if(state.status==='game_over'){
    show('view-end');
    renderEnd();
  }
}

function renderLiveScore(s){
  const box = el('liveScore');
  if(!box) return;

  if(!s || !s.players || s.players.length === 0){
    box.classList.add('hidden');
    box.innerHTML = '';
    return;
  }

  const scores = s.scores || {};
  box.innerHTML = (s.players||[]).map(p=>{
    const sc = (scores && scores[p.id] != null) ? scores[p.id] : 0;
    return `<span class="pill scorePill">${escapeHtml(p.name)}: <b>${sc}</b></span>`;
  }).join('');

  // Vis også hvem der er DJ lige nu (hvis spillet er startet)
  if(typeof s.dj_index === 'number' && s.players[s.dj_index]){
    const dj = s.players[s.dj_index];
    box.innerHTML = `<span class="pill pillNeutral">DJ: ${escapeHtml(dj.name)}</span>` + box.innerHTML;
  }

  box.classList.remove('hidden');
}

function escapeHtml(str){
  return String(str||'')
    .replaceAll('&','&amp;')
    .replaceAll('<','&lt;')
    .replaceAll('>','&gt;')
    .replaceAll('"','&quot;')
    .replaceAll("'",'&#39;');
}

function renderLobby(){
  // Room-only lobby section (should be hidden unless you're in a room)
  const roomOnly = document.getElementById('lobbyRoomOnly');
  const ul = el('lobbyPlayers');

  if(!state){
    // Not in a room: keep lobby clean
    if(roomOnly) roomOnly.classList.add('hidden');
    if(ul) ul.innerHTML = '';
    const hc = el('hostControls');
    if(hc) hc.classList.add('hidden');
    return;
  }

  // Hide room-only controls in lobby until a room exists
  if(roomOnly){ roomOnly.classList.toggle('hidden', !room); }
  ul.innerHTML = '';
  (state.players||[]).forEach(p=>{
    const li=document.createElement('li');
    li.innerText=p.name;
    ul.appendChild(li);
  });

  el('hostControls').classList.toggle('hidden', !player || player.id !== state.host_id);

  // Category select (host only)
  if(player && player.id === state.host_id){
    populateCategorySelect(state.category || 'Standard');
  }

  // Leave button
  const lb = document.getElementById('leaveRoomBtn');
  if(lb){ lb.classList.toggle('hidden', !room || !player); }
}

function renderRound(){
  startCoverRotation();
  startLocalTimer();

  const dj = state.players[state.dj_index];
  const isDJ = player && player.id === dj.id;

  const totalRounds = state.rounds_total || 0;
  const remainingRounds = Math.max(0, totalRounds - (state.round_index || 0));
  el('roundText').innerText = 'Runde ' + (state.round_index+1) + ' af ' + totalRounds + ' (' + remainingRounds + ' tilbage)';
  el('roleText').innerText = isDJ ? 'Du er DJ' : 'Gæt årstal';

  el('djPanel').classList.toggle('hidden', !isDJ);
  el('guessPanel').classList.toggle('hidden', isDJ);

  if(isDJ && state.current_song){
    el('djSongTitle').innerText = state.current_song.title;
    el('djSongMeta').innerText = state.current_song.artist + ' ('+state.current_song.year+')';
    el('playLink').href = state.current_song.spotifyUrl;
    // DJ can skip a bad song
    el('skipSongBtn').disabled = false;
    el('skipSongBtn').classList.remove('hidden');
  } else {
    // Hide/disable skip button when not DJ or no song yet
    el('skipSongBtn').disabled = true;
    el('skipSongBtn').classList.add('hidden');
  }

  // Guess list: show who has guessed (do not reveal year in-round)
  const gl = el('guessList');
  gl.innerHTML = '';
  (state.players||[]).forEach(p=>{
    if(p.id === dj.id) return; // DJ doesn't guess
    const has = state.guesses && (state.guesses[p.id] !== undefined);
    const li = document.createElement('li');
    li.innerText = has ? ('✅ ' + p.name) : ('⏳ ' + p.name);
    gl.appendChild(li);
  });

  // Scoreboard: total points
  const sb = el('scoreboard');
  sb.innerHTML='';
  (state.players||[]).forEach(p=>{
    const li=document.createElement('li');
    const sc = (state.scores && state.scores[p.id] !== undefined) ? state.scores[p.id] : 0;
    li.innerText = p.name + ': ' + sc + ' point';
    sb.appendChild(li);
  });
  updateTimerText();

  const gs = el('guessStatus');
  if(gs) gs.innerText = '';

  renderHistory();
}

function renderResult(){
  stopCoverRotation();
  stopLocalTimer();

  el('resultCorrectYear').innerText = 'Korrekt år: ' + state.current_song.year;

  const dj = state.players[state.dj_index];

  const ul = el('resultTable');
  ul.innerHTML='';

  (state.players||[]).forEach(p=>{
    if(p.id === dj.id) return; // DJ doesn't guess
    const g = state.guesses ? state.guesses[p.id] : undefined;
    const lp = state.last_round_points ? (state.last_round_points[p.id] ?? 0) : 0;
    const total = state.scores ? (state.scores[p.id] ?? 0) : 0;

    const li=document.createElement('li');
    li.innerText = `${p.name}: ${g ?? '-'}  (+${lp})  total: ${total}`;
    ul.appendChild(li);
  });

  renderHistory();
}

function renderEnd(){
  stopCoverRotation();
  stopLocalTimer();

  const scores = (state.players||[])
    .map(p=>({name:p.name,score:(state.scores?.[p.id] ?? 0)}))
    .sort((a,b)=>b.score-a.score);

  el('winnerText').innerText = 'Vinder: ' + (scores[0]?.name ?? '-');

  const ul = el('finalScoreboard');
  ul.innerHTML='';
  scores.forEach(s=>{
    const li=document.createElement('li');
    li.innerText=s.name+': '+s.score;
    ul.appendChild(li);
  });

  renderHistory();
}

function renderHistory(){
  const c = el('historyContainer');
  if(!c) return;
  const hist = state.history || [];
  if(hist.length === 0){
    c.innerText = 'Ingen runder endnu.';
    return;
  }
  c.innerHTML = '';
  hist.forEach(h=>{
    const card = document.createElement('div');
    card.className = 'historyCard';

    const song = h.song || {};
    const title = document.createElement('div');
    title.className = 'historyTitle';
    title.innerText = `Runde ${h.round_number}: ${song.title || '-'} — ${song.artist || '-'} (${song.year || '-'})`;

    const meta = document.createElement('div');
    meta.className = 'historyMeta';
    meta.innerText = `DJ: ${h.dj_name || '-'}  •  Spotify: ${song.spotifyUrl ? 'link' : '—'}`;

    const link = document.createElement('a');
    if(song.spotifyUrl){
      link.href = song.spotifyUrl;
      link.target = '_blank';
      link.rel = 'noopener';
      link.innerText = 'Åbn i Spotify';
    } else {
      link.innerText = '';
    }

    const table = document.createElement('table');
    table.className = 'historyTable';
    const thead = document.createElement('thead');
    thead.innerHTML = '<tr><th>Spiller</th><th>Gæt</th><th>Point</th></tr>';
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    (h.guesses || []).forEach(g=>{
      const tr = document.createElement('tr');
      tr.innerHTML = `<td>${g.player_name}</td><td>${g.guess_year}</td><td>${g.points}</td>`;
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);

    card.appendChild(title);
    card.appendChild(meta);
    if(song.spotifyUrl) card.appendChild(link);
    card.appendChild(table);
    c.appendChild(card);
  });
}

async function refreshState(){
  if(!room) return;
  try{
    state = await api({action:'state', room});
    setNet(true);
    render();
  }catch(e){
    setNet(false);
    console.warn(e);
  }
}

loadVersion();
loadCategories();
setInterval(refreshState, 1000);

// EVENTS
el('createBtn').onclick = async () => {
  try{
    const r = await api({
      action:'create_room',
      name: el('nameInput').value,
      timer:+el('timerSelect').value,
      rounds:+el('roundsSelect').value
    });
    room = r.room;
    player = r.player;
    el('roomCodeDisplay').innerText = 'Rumkode: ' + room;
    el('roomCodeDisplay').classList.remove('hidden');
    await refreshState();
  }catch(e){
    alert('Kunne ikke oprette rum: ' + e.message);
  }
};

el('joinBtn').onclick = async () => {
  try{
    room = el('roomInput').value.toUpperCase().trim();
    const r = await api({action:'join', room, name: el('nameInput').value});
    player = r.player;
    el('roomCodeDisplay').innerText = 'Rumkode: ' + room;
    el('roomCodeDisplay').classList.remove('hidden');
    await refreshState();
  }catch(e){
    alert('Kunne ikke joine: ' + e.message);
  }
};

const categorySelect = document.getElementById('categorySelect');
if(categorySelect){
  categorySelect.onchange = async () => {
    if(!room || !player) return;
    try{
      await api({action:'set_category', room, player: player.id, category: categorySelect.value});
      await refreshState();
    }catch(e){
      alert('Kunne ikke skifte kategori: ' + e.message);
    }
  };
}

el('startGameBtn').onclick = async () => {
  try{
    // Send current room settings so the server uses what the host selected
    const timer = el('timerSelect') ? el('timerSelect').value : null;
    const rounds = el('roundsSelect') ? el('roundsSelect').value : null;
    const category = el('categorySelect') ? el('categorySelect').value : null;
    await api({action:'start_game', room, timer, rounds, category});
    await refreshState();
  }catch(e){
    alert('Kunne ikke starte spil: ' + e.message);
  }
};

el('startTimerBtn').onclick = async () => {
  try{
    const r = await api({action:'start_timer', room, player: player ? player.id : null});
    if(r && r.round_started_at){ state.round_started_at = r.round_started_at; }
    await refreshState();
}catch(e){
    alert('Kunne ikke starte timer: ' + e.message);
  }
};

el('skipSongBtn').onclick = async () => {
  try{
    // DJ: skip current song (no points) and draw a new random song in the same category
    await api({action:'skip_song', room, player: player.id});
    await refreshState();
  }catch(e){
    alert('Kunne ikke springe sang over: ' + e.message);
  }
};

el('submitGuessBtn').onclick = async () => {
  try{
    const yearRaw = el('guessYearInput').value.trim();
    if(!yearRaw) { el('guessStatus').innerText='Skriv et årstal'; return; }
    const year = parseInt(yearRaw, 10);
    if(Number.isNaN(year)) { el('guessStatus').innerText='Ugyldigt årstal'; return; }

    await api({action:'submit_guess', room, player: player.id, year});
    el('guessStatus').innerText = 'Gæt sendt ✅';
    await refreshState();
  }catch(e){
    el('guessStatus').innerText = 'Fejl: ' + e.message;
  }
};

// --- Year input UX helpers (numeric keyboard on iPhone + quick adjust buttons) ---
(() => {
  const yearInput = el('guessYearInput');
  const submitBtn = el('submitGuessBtn');
  const statusEl = el('guessStatus');
  if(!yearInput || !submitBtn) return;

  const clampYear = (y) => {
    const now = new Date().getFullYear();
    const min = 1800;
    const max = now;
    if(Number.isNaN(y)) return min;
    return Math.max(min, Math.min(max, y));
  };

  const sanitize = () => {
    const cleaned = (yearInput.value || '').replace(/\D/g, '').slice(0, 4);
    if(cleaned !== yearInput.value) yearInput.value = cleaned;
  };

  yearInput.addEventListener('input', () => {
    sanitize();
    if(statusEl) statusEl.innerText = '';
  });

  yearInput.addEventListener('keydown', (e) => {
    if(e.key === 'Enter'){
      e.preventDefault();
      submitBtn.click();
    }
  });

  const step = (delta) => {
    sanitize();
    const now = new Date().getFullYear();
    const base = yearInput.value ? parseInt(yearInput.value, 10) : now;
    const next = clampYear(base + delta);
    yearInput.value = String(next);
    yearInput.focus();
    if(statusEl) statusEl.innerText = '';
  };

  const bindStep = (id, delta) => {
    const btn = document.getElementById(id);
    if(!btn) return;
    btn.addEventListener('click', () => step(delta));
  };

  bindStep('yearMinus10', -10);
  bindStep('yearMinus1', -1);
  bindStep('yearPlus1', 1);
  bindStep('yearPlus10', 10);

  // Quick decade buttons (HTML uses .yearQuick; older builds used .yearChip)
  document.querySelectorAll('.yearChip, .yearQuick').forEach((btn) => {
    btn.addEventListener('click', () => {
      const y = (btn.getAttribute('data-year') || '').trim();
      yearInput.value = y;
      sanitize();
      yearInput.focus();
      if(statusEl) statusEl.innerText = '';
    });
  });
})();

el('nextRoundBtn').onclick = async () => {
  try{
    await api({action:'next_round', room});
    await refreshState();
  }catch(e){
    alert('Kunne ikke næste runde: ' + e.message);
  }
};

el('newGameBtn').onclick = async () => {
  try{
    await api({action:'reset_game', room});
    await refreshState();
  }catch(e){
    alert('Kunne ikke nulstille: ' + e.message);
  }
};


function initHistoryToggle(){
  const hp = el('historyPanel');
  const btn = el('historyToggleBtn');
  if(!hp || !btn) return;

  const key = 'musikspil_history_collapsed';
  const saved = localStorage.getItem(key);
  if(saved === '1'){
    hp.classList.add('collapsed');
    btn.innerText = 'Vis';
  } else {
    btn.innerText = 'Skjul';
  }

  btn.onclick = () => {
    const collapsed = hp.classList.toggle('collapsed');
    localStorage.setItem(key, collapsed ? '1' : '0');
    btn.innerText = collapsed ? 'Vis' : 'Skjul';
  };
}

initHistoryToggle();


// Leave room
const leaveBtn = document.getElementById('leaveRoomBtn');
if(leaveBtn){
  leaveBtn.onclick = async () => {
    try{
      if(room && player){
        await api({action:'leave_room', room, player: player.id});
      }
    }catch(e){
      // ignore
    }
    room = null;
    player = null;
    state = null;
    const rc = document.getElementById('roomCodeDisplay');
    if(rc){ rc.innerText=''; rc.classList.add('hidden'); }
    leaveBtn.classList.add('hidden');
    show('view-lobby');
    renderLobby();
  };
}
