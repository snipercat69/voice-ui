  (() => {
  const holdBtn = document.getElementById('holdBtn');
  const recordBtn = document.getElementById('recordBtn');
  const stopBtn = document.getElementById('stopBtn');
  const autoplayEl = document.getElementById('autoplay');
  const wakeEnableEl = document.getElementById('wakeEnable');
  const showTalkDetailsEl = document.getElementById('showTalkDetails');
  const talkDetailsEl = document.getElementById('talkDetails');
  const showVoiceDetailsEl = document.getElementById('showVoiceDetails');
  const voiceDetailsEl = document.getElementById('voiceDetails');
  const showCatalogDetailsEl = document.getElementById('showCatalogDetails');
  const catalogDetailsEl = document.getElementById('catalogDetails');
  const wakeStatusEl = document.getElementById('wakeStatus');
  const voiceProviderEl = document.getElementById('voiceProvider');
  const voiceModelEl = document.getElementById('voiceModel');
  const fastAgentEl = document.getElementById('fastAgent');
  const fastModelEl = document.getElementById('fastModel');
  const voiceRefEl = document.getElementById('voiceRef');
  const minimaxVoiceWrapEl = document.getElementById('minimaxVoiceWrap');
  const minimaxVoiceSelectEl = document.getElementById('minimaxVoiceSelect');
  const replyStyleEl = document.getElementById('replyStyle');
  const fastModeEl = document.getElementById('fastMode');
  const applyVoiceBtn = document.getElementById('applyVoiceBtn');
  const previewVoiceBtn = document.getElementById('previewVoiceBtn');
  const statusEl = document.getElementById('status');
  const processingSpinnerEl = document.getElementById('processingSpinner');
  const autoplayWarningEl = document.getElementById('autoplayWarning');
  const latencyEl = document.getElementById('latency');
  const transcriptEl = document.getElementById('transcript');
  const replyEl = document.getElementById('reply');
  const player = document.getElementById('player');
  const waveProgressEl = document.getElementById('waveProgress');
  const historyEl = document.getElementById('history');
  const catalogEl = document.getElementById('catalog');
  const catalogModeEl = document.getElementById('catalogMode');
  const catalogFiltersEl = document.getElementById('catalogFilters');
  const logEl = document.getElementById('log');
  const toastEl = document.getElementById('toast');
  const reminderListEl = document.getElementById('reminderList');
  const reminderHistoryEl = document.getElementById('reminderHistory');
  const toggleHistoryBtn = document.getElementById('toggleHistoryBtn');
  const toggleLogBtn = document.getElementById('toggleLogBtn');
  const toggleReminderHistoryBtn = document.getElementById('toggleReminderHistoryBtn');
  const dismissReminderHistoryBtn = document.getElementById('dismissReminderHistoryBtn');
  const SpeechRecognitionCtor = window.SpeechRecognition || window.webkitSpeechRecognition || null;

  let mediaRecorder = null;
  let chunks = [];
  let activeStream = null;
  let turnStartedAt = null;
  let holdMode = false;
  let supportedModels = {
    fish: ['s1', 's2-pro'],
    elevenlabs: ['eleven_multilingual_v2'],
    'local-piper': ['en_US-lessac-medium', 'en_US-ryan-medium'],
    'local-kokoro': ['af_bella', 'am_adam'],
    minimax: ['speech-2.8-turbo', 'speech-2.8-hd', 'speech-2.6-turbo', 'speech-2.6-hd'],
  };
  let supportedReplyStyles = ['short', 'normal', 'deep'];
  let minimaxEnglishVoices = [];
  let catalogCache = [];
  window._catalogCache = catalogCache; // debug
  let selectedCatalogId = null;
  let activeTagFilters = new Set();
  let wakeStream = null;
  let wakeChunkRecorder = null;
  let wakeSpeech = null;
  let wakeSpeechRunning = false;
  let interruptSpeech = null;
  let interruptSpeechRunning = false;
  let wakeDetectionMode = 'none'; // speech | chunk | none
  let wakeActive = false;
  let wakeLoopAbort = false;
  let wakeDetectBusy = false;
  let wakeCommandRunning = false;
  let wakePrimedUntilMs = 0;
  let reminderPollTimer = null;
  let reminderAutoplayBusy = false;
  let stopInFlight = false;
  const logEntries = ['UI loaded.'];

  const FILTER_TAGS = ['male', 'female', 'comedy', 'narration', 'free-local', 'free-tier-api'];

  function ensureCatalogStyles() {
    if (document.getElementById('catalog-inline-styles')) return;
    const style = document.createElement('style');
    style.id = 'catalog-inline-styles';
    style.textContent = `
      .catalog-chip {
        border: 1px solid #bbb;
        background: #fff;
        border-radius: 999px;
        padding: 4px 10px;
        margin: 0 6px 6px 0;
        cursor: pointer;
        font-size: 12px;
      }
      .catalog-chip.active {
        background: #111;
        color: #fff;
        border-color: #111;
      }
      .turn.catalog-item { cursor: pointer; }
      .turn.catalog-item.selected {
        border-color: #4b7cff;
        box-shadow: 0 0 0 2px rgba(75,124,255,.2);
        background: #f6f9ff;
      }
    `;
    document.head.appendChild(style);
  }

  function renderFilterChips() {
    if (!catalogFiltersEl) return;
    catalogFiltersEl.innerHTML = '';
    for (const tag of FILTER_TAGS) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = `catalog-chip ${activeTagFilters.has(tag) ? 'active' : ''}`;
      b.textContent = tag;
      b.addEventListener('click', () => {
        if (activeTagFilters.has(tag)) activeTagFilters.delete(tag);
        else activeTagFilters.add(tag);
        renderFilterChips();
        renderCatalog(catalogCache);
      });
      catalogFiltersEl.appendChild(b);
    }
    const clear = document.createElement('button');
    clear.type = 'button';
    clear.className = 'catalog-chip';
    clear.textContent = 'clear';
    clear.addEventListener('click', () => {
      activeTagFilters.clear();
      renderFilterChips();
      renderCatalog(catalogCache);
    });
    catalogFiltersEl.appendChild(clear);
  }

  function renderLog() {
    if (!logEl) return;
    logEl.textContent = logEntries.slice(-10).join('\n');
    logEl.scrollTop = logEl.scrollHeight;
  }

  function log(msg, kind = 'info') {
    const ts = new Date().toLocaleTimeString();
    const prefix = kind === 'error' ? '[ERR]' : kind === 'ok' ? '[OK]' : '[..]';
    logEntries.push(`${prefix} [${ts}] ${msg}`);
    renderLog();
  }

  function setStatus(text, kind = 'info') {
    statusEl.textContent = text;
    statusEl.className = 'status-chip';
    if (kind === 'error') statusEl.classList.add('status-error');
    else if (kind === 'ok') statusEl.classList.add('status-done');
    else if (kind === 'recording') statusEl.classList.add('status-recording');
    else if (kind === 'processing') statusEl.classList.add('status-processing');
  }

  function setProcessing(active) {
    if (!processingSpinnerEl) return;
    processingSpinnerEl.classList.toggle('show', !!active);
    if (stopBtn && !stopInFlight) {
      stopBtn.disabled = !active && (player.paused || !player.src);
    }
    if (active) startInterruptListener();
    else if (player.paused) stopInterruptListener();
    refreshStopButtonLabel();
  }

  function setAutoplayWarning(show) {
    if (!autoplayWarningEl) return;
    autoplayWarningEl.classList.toggle('show', !!show);
  }

  function setLatency(ms) {
    if (!ms || Number.isNaN(ms)) {
      latencyEl.textContent = '—';
      return;
    }
    latencyEl.textContent = `${Math.round(ms)} ms`;
  }

  function setWakeStatus(text) {
    if (!wakeStatusEl) return;
    wakeStatusEl.textContent = text;
  }

  function refreshStopButtonLabel() {
    if (!stopBtn) return;
    if (mediaRecorder && mediaRecorder.state === 'recording') {
      stopBtn.textContent = 'Stop Recording';
      return;
    }
    if (stopInFlight) {
      stopBtn.textContent = 'Stopping…';
      return;
    }
    if (processingSpinnerEl?.classList.contains('show') || !player.paused) {
      stopBtn.textContent = 'Stop Playback / Cancel Reply';
      return;
    }
    stopBtn.textContent = 'Stop';
  }

  function toast(msg, kind = 'info') {
    toastEl.textContent = msg;
    toastEl.style.background = kind === 'error' ? '#7a0012' : '#111';
    toastEl.classList.add('show');
    setTimeout(() => toastEl.classList.remove('show'), 2800);
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  function playReminderChime() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = 880;
      gain.gain.value = 0.0001;
      osc.connect(gain);
      gain.connect(ctx.destination);
      const now = ctx.currentTime;
      gain.gain.exponentialRampToValueAtTime(0.08, now + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + 0.22);
      osc.start(now);
      osc.stop(now + 0.24);
      setTimeout(() => ctx.close().catch(() => {}), 400);
    } catch (_) {
      // ignore browser audio init failures
    }
  }

  async function playReminder(reminder) {
    if (!reminder || !reminder.audioPath || reminderAutoplayBusy) return;
    reminderAutoplayBusy = true;
    try {
      const audioUrl = `/api/audio?path=${encodeURIComponent(reminder.audioPath)}`;
      transcriptEl.textContent = '(voice reminder)';
      replyEl.textContent = reminder.spokenText || `Reminder: ${reminder.text || 'something'}`;
      playReminderChime();
      await sleep(180);
      player.src = audioUrl;
      player.load();
      try {
        await player.play();
        setAutoplayWarning(false);
        toast(`Reminder: ${reminder.text || 'something'}`, 'ok');
        log(`Played reminder: ${reminder.text || 'something'}`, 'ok');
      } catch (e) {
        setAutoplayWarning(true);
        toast('Reminder is ready, but autoplay was blocked.', 'error');
        log(`Reminder autoplay blocked: ${e}`, 'error');
      }
    } finally {
      reminderAutoplayBusy = false;
    }
  }

  async function pollDueReminders() {
    try {
      const res = await fetch('/api/reminders/due');
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `reminder poll failed (${res.status})`);
      const reminders = Array.isArray(json.reminders) ? json.reminders : [];
      for (const reminder of reminders) {
        await playReminder(reminder);
      }
      await loadReminderLists();
    } catch (err) {
      log(`Reminder poll failed: ${err}`, 'error');
    }
  }

  function formatRemainingTime(dueAt) {
    if (!dueAt) return '';
    const ms = new Date(dueAt).getTime() - Date.now();
    if (Number.isNaN(ms)) return '';
    if (ms <= 0) return 'due now';
    const totalSec = Math.round(ms / 1000);
    const min = Math.floor(totalSec / 60);
    const sec = totalSec % 60;
    if (min <= 0) return `${sec}s remaining`;
    return `${min}m ${sec}s remaining`;
  }

  function formatReminderRow(reminder, kind = 'upcoming') {
    const wrap = document.createElement('div');
    const stateClass = kind === 'history'
      ? (reminder.dismissed ? 'reminder-dismissed' : 'reminder-spoken')
      : (reminder.isDue ? 'reminder-due' : 'reminder-upcoming');
    wrap.className = `turn ${stateClass}`;
    const due = reminder.dueAt || reminder.spokenAt || reminder.createdAt || '';
    const status = kind === 'history'
      ? (reminder.dismissed ? 'dismissed' : 'spoken')
      : (reminder.isDue ? 'due now' : 'scheduled');
    const countdown = kind === 'upcoming' ? formatRemainingTime(reminder.dueAt) : '';
    wrap.innerHTML = `
      <div class="meta"><strong>${escapeHtml(reminder.text || 'Reminder')}</strong> • ${escapeHtml(status)} • ${escapeHtml(due)}</div>
      <div class="bubble assistant">${escapeHtml(reminder.spokenText || reminder.text || '')}</div>
      ${countdown ? `<div class="reminder-countdown">${escapeHtml(countdown)}</div>` : ''}
      <div class="actions"></div>
    `;
    const actions = wrap.querySelector('.actions');

    if (reminder.audioPath) {
      const replayBtn = document.createElement('button');
      replayBtn.type = 'button';
      replayBtn.textContent = '▶ Replay';
      replayBtn.addEventListener('click', () => playReminder(reminder));
      actions.appendChild(replayBtn);
    }

    if (kind === 'upcoming') {
      const snoozeBtn = document.createElement('button');
      snoozeBtn.type = 'button';
      snoozeBtn.textContent = 'Snooze 5 min';
      snoozeBtn.addEventListener('click', async () => {
        try {
          const res = await fetch('/api/reminders/snooze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: reminder.id, minutes: 5 }),
          });
          const json = await res.json();
          if (!res.ok || !json.ok) throw new Error(json.error || `snooze failed (${res.status})`);
          toast('Reminder snoozed for 5 minutes', 'ok');
          await loadReminderLists();
        } catch (err) {
          toast('Could not snooze reminder', 'error');
          log(`Reminder snooze failed: ${err}`, 'error');
        }
      });
      actions.appendChild(snoozeBtn);

      const dismissBtn = document.createElement('button');
      dismissBtn.type = 'button';
      dismissBtn.textContent = 'Dismiss';
      dismissBtn.addEventListener('click', async () => {
        try {
          const res = await fetch('/api/reminders/dismiss', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: reminder.id }),
          });
          const json = await res.json();
          if (!res.ok || !json.ok) throw new Error(json.error || `dismiss failed (${res.status})`);
          toast('Reminder dismissed', 'ok');
          await loadReminderLists();
        } catch (err) {
          toast('Could not dismiss reminder', 'error');
          log(`Reminder dismiss failed: ${err}`, 'error');
        }
      });
      actions.appendChild(dismissBtn);
    }

    return wrap;
  }

  async function loadReminderLists() {
    if (!reminderListEl || !reminderHistoryEl) return;
    try {
      const res = await fetch('/api/reminders/list');
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `reminder list failed (${res.status})`);

      const upcoming = Array.isArray(json.upcoming) ? json.upcoming : [];
      const history = Array.isArray(json.history) ? json.history : [];

      if (reminderListEl) reminderListEl.innerHTML = '';
      if (reminderHistoryEl) reminderHistoryEl.innerHTML = '';

      if (!upcoming.length) {
        if (reminderListEl) reminderListEl.textContent = 'No upcoming reminders.';
      } else {
        upcoming.forEach((r) => { if (reminderListEl) reminderListEl.appendChild(formatReminderRow(r, 'upcoming')); });
      }

      if (!history.length) {
        if (reminderHistoryEl) reminderHistoryEl.textContent = 'No reminder history yet.';
      } else if (reminderHistoryEl) {
        history.forEach((r) => reminderHistoryEl.appendChild(formatReminderRow(r, 'history')));
      }
      if (reminderHistoryEl) reminderHistoryEl.style.display = 'none';
      if (toggleReminderHistoryBtn) toggleReminderHistoryBtn.textContent = '▲';
    } catch (err) {
      log(`Reminder list load failed: ${err}`, 'error');
    }
  }

  function renderModelOptions(provider, selected) {
    const list = supportedModels[provider] || [];
    voiceModelEl.innerHTML = '';
    list.forEach((m) => {
      const opt = document.createElement('option');
      opt.value = m;
      opt.textContent = m;
      if (m === selected) opt.selected = true;
      voiceModelEl.appendChild(opt);
    });
  }

  function renderReplyStyleOptions(styles, selected) {
    if (!replyStyleEl) return;
    const list = Array.isArray(styles) && styles.length ? styles : ['short', 'normal', 'deep'];
    replyStyleEl.innerHTML = '';
    list.forEach((s) => {
      const opt = document.createElement('option');
      opt.value = s;
      opt.textContent = s;
      if (s === selected) opt.selected = true;
      replyStyleEl.appendChild(opt);
    });
  }

  function renderMinimaxVoiceOptions(selected) {
    if (!minimaxVoiceSelectEl) return;
    minimaxVoiceSelectEl.innerHTML = '';
    const list = Array.isArray(minimaxEnglishVoices) ? minimaxEnglishVoices : [];
    list.forEach((voiceId) => {
      const opt = document.createElement('option');
      opt.value = voiceId;
      opt.textContent = voiceId;
      if (voiceId === selected) opt.selected = true;
      minimaxVoiceSelectEl.appendChild(opt);
    });
  }

  function syncVoiceRefUi() {
    const provider = voiceProviderEl?.value || 'fish';
    const isMinimax = provider === 'minimax';
    if (minimaxVoiceWrapEl) minimaxVoiceWrapEl.style.display = isMinimax ? '' : 'none';
    if (voiceRefEl) voiceRefEl.style.display = isMinimax ? 'none' : '';
    if (isMinimax && minimaxVoiceSelectEl) {
      voiceRefEl.value = minimaxVoiceSelectEl.value || 'English_Graceful_Lady';
    }
  }

  function containsWakeWord(text) {
    const lower = (text || '').toLowerCase();
    // Normalize punctuation and extra symbols.
    const norm = lower.replace(/[^a-z]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!norm) return false;

    // Exact match first.
    if (/\btrinity\b/.test(norm)) return true;

    // Allow a few common ASR slip-ups around the word "Trinity".
    const variants = ['trinty', 'trini', 'trinite', 'trinitys'];
    return variants.some((v) => norm.includes(v));
  }

  function extractWakeCommand(text) {
    const raw = (text || '').trim();
    if (!raw) return '';

    // Match "trinity" at beginning-ish and capture everything after it as command text.
    const m = raw.match(/(?:^|[\s,;:.!?-])trinity(?:[\s,;:.!?-]+)(.+)$/i);
    if (!m || !m[1]) return '';
    return m[1].trim();
  }

  function isWakeOnlyText(text) {
    const norm = (text || '').toLowerCase().replace(/[^a-z]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!norm) return false;
    return ['trinity', 'trinty', 'trini', 'trinite', 'trinitys'].includes(norm);
  }

  function isStopCommandText(text) {
    const norm = (text || '').toLowerCase().replace(/[^a-z]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!norm) return false;
    return [
      'stop',
      'stop trinity',
      'trinity stop',
      'cancel',
      'cancel that',
      'never mind',
      'nevermind',
      'shut up',
    ].includes(norm);
  }

  function canVoiceStopNow() {
    return !!(processingSpinnerEl?.classList.contains('show') || !player.paused);
  }

  function ensureInterruptSpeechRecognition() {
    if (!SpeechRecognitionCtor) return null;
    if (interruptSpeech) return interruptSpeech;

    const rec = new SpeechRecognitionCtor();
    rec.lang = 'en-US';
    rec.continuous = true;
    rec.interimResults = false;

    rec.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (!result?.isFinal) continue;
        const transcript = (result[0]?.transcript || '').trim();
        if (!transcript) continue;
        if (isStopCommandText(transcript) && canVoiceStopNow()) {
          log(`Voice stop detected: "${transcript}"`, 'ok');
          stopServerTurn().catch((err) => {
            log(`Interrupt stop failed: ${err}`, 'error');
          });
        }
      }
    };

    rec.onerror = (event) => {
      const err = event?.error || 'unknown';
      if (err === 'aborted') return;
      log(`Interrupt listener error: ${err}`, 'error');
    };

    rec.onend = () => {
      interruptSpeechRunning = false;
      if (canVoiceStopNow()) {
        setTimeout(() => startInterruptListener(), 150);
      }
    };

    interruptSpeech = rec;
    return rec;
  }

  function startInterruptListener() {
    if (!canVoiceStopNow()) return false;
    const rec = ensureInterruptSpeechRecognition();
    if (!rec) return false;
    try {
      rec.start();
      interruptSpeechRunning = true;
      return true;
    } catch (err) {
      const msg = String(err || 'unknown').toLowerCase();
      if (msg.includes('already started') || msg.includes('aborted')) {
        interruptSpeechRunning = true;
        return true;
      }
      log(`Interrupt listener start failed: ${err}`, 'error');
      return false;
    }
  }

  function stopInterruptListener() {
    if (!interruptSpeech) return;
    try {
      interruptSpeech.stop();
    } catch (_) {
      // ignore
    }
    interruptSpeechRunning = false;
  }

  async function processWakeTranscript(txt, source = 'wake') {
    const text = (txt || '').trim();
    if (!text) return;

    if (isStopCommandText(text) && canVoiceStopNow()) {
      log(`Voice stop detected (${source}): "${text}"`, 'ok');
      await stopServerTurn();
      return;
    }

    const now = Date.now();

    // If wake word was just heard in a previous chunk/result, treat the next spoken phrase as command.
    if (wakePrimedUntilMs > now) {
      const maybeCommand = extractWakeCommand(text) || text;
      if (maybeCommand && !isWakeOnlyText(maybeCommand)) {
        wakePrimedUntilMs = 0;
        log(`Wake follow-up captured (${source}): "${maybeCommand}"`, 'ok');
        await triggerWakeCommand(maybeCommand);
        return;
      }
    }

    if (containsWakeWord(text)) {
      const inlineCommand = extractWakeCommand(text);
      log(`Wake word detected (${source}): "${text}"`, 'ok');
      if (inlineCommand) {
        wakePrimedUntilMs = 0;
        await triggerWakeCommand(inlineCommand);
      } else {
        wakePrimedUntilMs = Date.now() + 5000;
        setWakeStatus('Wake heard — say command…');
        log('Wake primed; waiting for follow-up phrase', 'ok');
      }
    }
  }

  function renderCatalog(items) {
    catalogEl.innerHTML = '';

    if (!items || !items.length) {
      catalogEl.textContent = '(no catalog entries)';
      return;
    }
    const mode = (catalogModeEl?.value || 'basic');
    let filtered = mode === 'basic'
      ? items.filter((i) => i.tested || i.provider === 'fish' || i.provider === 'elevenlabs').slice(0, 10)
      : items;

    if (activeTagFilters.size) {
      filtered = filtered.filter((i) => {
        const tags = new Set(i.tags || []);
        for (const tag of activeTagFilters) {
          if (!tags.has(tag)) return false;
        }
        return true;
      });
    }

    for (const item of filtered) {
      const div = document.createElement('div');
      div.className = 'turn catalog-item';
      if (item.id && item.id === selectedCatalogId) div.classList.add('selected');
      div.style.marginBottom = '6px';
      const testedBadge = item.tested ? '✅ tested' : '🧪 untested';
      div.innerHTML = `
        <div class="meta"><strong>${escapeHtml(item.label)}</strong> • ${escapeHtml(item.provider)} • ${escapeHtml(item.model)} • ${escapeHtml(item.stability || '')} • ${testedBadge}</div>
        <div class="meta">tags: ${(item.tags || []).map(escapeHtml).join(', ')}</div>
        <div class="meta">${escapeHtml(item.notes || '')}</div>
      `;
      div.addEventListener('click', () => {
        selectedCatalogId = item.id || null;
        renderCatalog(catalogCache);
        if (item.provider === 'fish' || item.provider === 'elevenlabs' || item.provider === 'local-piper' || item.provider === 'local-kokoro') {
          voiceProviderEl.value = item.provider;
          const selected = item.provider === 'fish'
            ? (item.model || 's1')
            : item.provider === 'elevenlabs'
              ? (item.model || 'eleven_multilingual_v2')
              : item.provider === 'local-piper'
                ? (item.model || 'en_US-lessac-medium')
                : (item.model || 'af_bella');
          renderModelOptions(item.provider, selected);
          voiceRefEl.value = item.voiceRef || '';
          toast(`Loaded preset: ${item.label}`);
        } else {
          toast('This is a catalog suggestion only (not wired provider yet).', 'error');
        }
      });
      catalogEl.appendChild(div);
    }
  }

  function escapeHtml(str) {
    return (str || '')
      .replaceAll('&', '&amp;')
      .replaceAll('<', '&lt;')
      .replaceAll('>', '&gt;');
  }

  function prependHistory(entry) {
    const div = document.createElement('div');
    div.className = 'turn';
    const ts = entry.timestamp || new Date().toISOString();
    const provider = entry.voiceProvider || 'fish';
    const model = entry.voiceModel || '';
    const audioPath = entry.audioPath || '';

    const audioUrl = audioPath ? `/api/audio?path=${encodeURIComponent(audioPath)}` : '';
    const downloadUrl = audioUrl ? `${audioUrl}&download=1` : '';

    div.innerHTML = `
      <div class="meta">${escapeHtml(ts)} • ${escapeHtml(provider)} • ${escapeHtml(model)}</div>
      <div class="bubble user"><strong>You:</strong> ${escapeHtml(entry.transcript || '')}</div>
      <div class="bubble assistant"><strong>Trinity:</strong> ${escapeHtml(entry.reply || '')}</div>
      <div class="meta"><code>${escapeHtml(audioPath)}</code></div>
      <div class="actions">
        <button type="button" class="replay-btn" ${audioUrl ? '' : 'disabled'}>▶ Replay</button>
        <a href="${downloadUrl}" ${downloadUrl ? 'download' : ''}>⬇ Download</a>
      </div>
    `;

    const replayBtn = div.querySelector('.replay-btn');
    replayBtn?.addEventListener('click', async () => {
      if (!audioUrl) return;
      player.src = audioUrl;
      player.load();
      try {
        await player.play();
      } catch (_) {
        setAutoplayWarning(true);
      }
    });

    if (!historyEl) return;
    historyEl.prepend(div);
    while (historyEl.children.length > 80) historyEl.removeChild(historyEl.lastChild);
  }

  async function loadHistory() {
    try {
      const res = await fetch('/api/history');
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `history failed (${res.status})`);
      if (historyEl) historyEl.innerHTML = '';
      const arr = (json.history || []).slice().reverse();
      arr.forEach(prependHistory);
      // start collapsed — hide entire history container
      if (historyEl) historyEl.style.display = 'none';
      if (toggleHistoryBtn) toggleHistoryBtn.textContent = '▲';
      log(`Loaded ${arr.length} history entries`, 'ok');
    } catch (err) {
      log(`History load failed: ${err}`, 'error');
    }
  }

  async function loadCatalog() {
    try {
      const res = await fetch('/api/free-voice-catalog');
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `catalog failed (${res.status})`);
      catalogCache = json.catalog || [];
      window._catalogCache = catalogCache;
      renderCatalog(catalogCache);
      log(`Loaded ${catalogCache.length} catalog entries`, 'ok');
    } catch (err) {
      console.error('loadCatalog error:', err);
      log(`Catalog load failed: ${err}`, 'error');
    }
  }
  window._loadCatalog = loadCatalog;
  window._renderCatalog = renderCatalog;

  async function checkHealth() {
    try {
      const res = await fetch('/health');
      const json = await res.json();
      if (json.ok) {
        setStatus('Backend reachable', 'ok');
        log('Health check OK', 'ok');
      } else {
        setStatus('Backend not ready', 'error');
        log('Health returned not-ok', 'error');
      }
    } catch (err) {
      setStatus('Backend unreachable', 'error');
      log(`Health check failed: ${err}`, 'error');
      toast('Backend unreachable', 'error');
    }
  }

  async function loadVoiceConfig() {
    try {
      const res = await fetch('/api/voice-config');
      const json = await res.json();
      if (res.ok && json.ok) {
        supportedModels = json.supportedModels || supportedModels;
        supportedReplyStyles = json.supportedReplyStyles || supportedReplyStyles;
        minimaxEnglishVoices = json.minimaxEnglishVoices || minimaxEnglishVoices;
        voiceProviderEl.value = json.provider || 'fish';
        const selectedModel = json.provider === 'elevenlabs'
          ? (json.elevenModelId || 'eleven_multilingual_v2')
          : json.provider === 'local-piper'
            ? (json.piperVoiceId || 'en_US-lessac-medium')
            : json.provider === 'local-kokoro'
              ? (json.kokoroVoiceId || 'af_bella')
            : json.provider === 'minimax'
              ? (json.minimaxSpeechModel || 'speech-2.8-turbo')
            : (json.fishModel || 's1');
        renderModelOptions(voiceProviderEl.value, selectedModel);
        voiceRefEl.value = json.provider === 'elevenlabs'
          ? (json.elevenVoiceId || '')
          : json.provider === 'local-piper'
            ? (json.piperVoiceId || '')
            : json.provider === 'local-kokoro'
              ? (json.kokoroVoiceId || '')
            : json.provider === 'minimax'
              ? (json.minimaxVoiceId || '')
            : (json.fishReferenceId || '');
        renderReplyStyleOptions(supportedReplyStyles, json.replyStyle || 'normal');
        renderMinimaxVoiceOptions(json.minimaxVoiceId || 'English_Graceful_Lady');
        syncVoiceRefUi();
        if (fastModeEl) fastModeEl.checked = !!json.fastMode;
        if (fastAgentEl) fastAgentEl.value = json.fastAgentId || 'voice-fast';
        if (fastModelEl) fastModelEl.value = json.fastAgentModel || '';
        log(`Voice config loaded (provider=${voiceProviderEl.value}, fastAgent=${json.fastAgentId || 'voice-fast'}, fastModel=${json.fastAgentModel || 'default'})`, 'ok');
      }
    } catch (err) {
      log(`Voice config load failed: ${err}`, 'error');
    }
  }

  async function applyVoiceConfig() {
    const provider = voiceProviderEl.value;
    const payload = {
      provider,
      fishModel: provider === 'fish' ? voiceModelEl.value : 's1',
      fishReferenceId: provider === 'fish' ? voiceRefEl.value.trim() : '',
      elevenModelId: provider === 'elevenlabs' ? voiceModelEl.value : 'eleven_multilingual_v2',
      elevenVoiceId: provider === 'elevenlabs' ? voiceRefEl.value.trim() : 'JBFqnCBsd6RMkjVDRZzb',
      piperVoiceId: provider === 'local-piper' ? voiceModelEl.value : 'en_US-lessac-medium',
      kokoroVoiceId: provider === 'local-kokoro' ? voiceModelEl.value : 'af_bella',
      minimaxSpeechModel: provider === 'minimax' ? voiceModelEl.value : 'speech-2.8-turbo',
      minimaxVoiceId: provider === 'minimax' ? (minimaxVoiceSelectEl?.value || voiceRefEl.value.trim()) : 'English_Graceful_Lady',
      replyStyle: replyStyleEl?.value || 'normal',
      fastMode: !!fastModeEl?.checked,
      fastAgentId: fastAgentEl?.value || 'voice-fast',
      fastAgentModel: fastModelEl?.value || '',
    };
    applyVoiceBtn.disabled = true;
    try {
      const res = await fetch('/api/voice-config', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `voice-config failed (${res.status})`);
      log(`Voice config applied (provider=${json.provider}, fastAgent=${json.fastAgentId || 'voice-fast'}, fastModel=${json.fastAgentModel || 'default'})`, 'ok');
      toast('Voice config updated', 'ok');
    } catch (err) {
      log(`Voice config apply failed: ${err}`, 'error');
      toast('Voice config apply failed', 'error');
    } finally {
      applyVoiceBtn.disabled = false;
    }
  }

  async function previewVoice() {
    const provider = voiceProviderEl.value;
    const payload = {
      text: 'Hello Cypher, this is a voice preview.',
      provider,
      fishModel: provider === 'fish' ? voiceModelEl.value : undefined,
      fishReferenceId: provider === 'fish' ? voiceRefEl.value.trim() : undefined,
      elevenModelId: provider === 'elevenlabs' ? voiceModelEl.value : undefined,
      elevenVoiceId: provider === 'elevenlabs' ? voiceRefEl.value.trim() : undefined,
      piperVoiceId: provider === 'local-piper' ? voiceModelEl.value : undefined,
      kokoroVoiceId: provider === 'local-kokoro' ? voiceModelEl.value : undefined,
      minimaxSpeechModel: provider === 'minimax' ? voiceModelEl.value : undefined,
      minimaxVoiceId: provider === 'minimax' ? (minimaxVoiceSelectEl?.value || voiceRefEl.value.trim()) : undefined,
    };
    previewVoiceBtn.disabled = true;
    setStatus('Generating voice preview…');
    try {
      const res = await fetch('/api/voice-preview', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      const json = await res.json();
      if (!res.ok || !json.ok) throw new Error(json.error || `preview failed (${res.status})`);
      const src = `data:${json.mimeType || 'audio/mpeg'};base64,${json.audioBase64 || ''}`;
      player.src = src;
      player.load();
      try {
        await player.play();
      } catch (_) {
        // autoplay restrictions are fine here
      }
      setStatus('Preview ready', 'ok');
      toast('Voice preview generated', 'ok');
      log(`Voice preview ok (${provider})`, 'ok');
    } catch (err) {
      setStatus('Voice preview failed', 'error');
      log(`Voice preview failed: ${err}`, 'error');
      toast('Voice preview failed', 'error');
    } finally {
      previewVoiceBtn.disabled = false;
    }
  }

  async function doVoiceTurn(blob) {
    const form = new FormData();
    form.append('audio', blob, 'voice.webm');

    turnStartedAt = performance.now();
    setStatus('Processing voice turn…', 'processing');
    setProcessing(true);
    log('Uploading audio and running /api/voice-turn');

    try {
      const res = await fetch('/api/voice-turn', {
        method: 'POST',
        body: form,
      });

      const json = await res.json();
      if (!res.ok || !json.ok) {
        throw new Error(json.error || `voice-turn failed (${res.status})`);
      }

      transcriptEl.textContent = json.transcript || '(empty transcript)';
      replyEl.textContent = json.reply || '(empty reply)';

      const mime = json.mimeType || 'audio/mpeg';
      const b64 = json.audioBase64;
      if (b64) {
        const src = `data:${mime};base64,${b64}`;
        player.src = src;
        player.load();
        if (autoplayEl.checked) {
          try {
            await player.play();
            log('Reply audio autoplay started', 'ok');
            setAutoplayWarning(false);
          } catch (e) {
            log(`Autoplay blocked by browser: ${e}`, 'error');
            toast('Autoplay blocked by browser. Use play button.', 'error');
            setAutoplayWarning(true);
          }
        }
      } else {
        toast('No audio returned. Text fallback shown.', 'error');
      }

      if (json.entry) prependHistory(json.entry);

      const elapsed = performance.now() - turnStartedAt;
      setLatency(elapsed);
      setStatus('Voice turn complete', 'ok');
      log(`Voice turn complete (${Math.round(elapsed)} ms)`, 'ok');
    } finally {
      setProcessing(false);
    }
  }

  async function doTextTurn(text) {
    const commandText = (text || '').trim();
    if (!commandText) throw new Error('Empty wake command text');

    turnStartedAt = performance.now();
    setStatus('Processing wake command…', 'processing');
    setProcessing(true);
    log(`Running /api/text-turn with wake command: "${commandText}"`);

    try {
      const res = await fetch('/api/text-turn', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: commandText }),
      });

      const json = await res.json();
      if (!res.ok || !json.ok) {
        throw new Error(json.error || `text-turn failed (${res.status})`);
      }

      transcriptEl.textContent = json.transcript || '(empty transcript)';
      replyEl.textContent = json.reply || '(empty reply)';

      const mime = json.mimeType || 'audio/mpeg';
      const b64 = json.audioBase64;
      if (b64) {
        const src = `data:${mime};base64,${b64}`;
        player.src = src;
        player.load();
        if (autoplayEl.checked) {
          try {
            await player.play();
            log('Reply audio autoplay started', 'ok');
            setAutoplayWarning(false);
          } catch (e) {
            log(`Autoplay blocked by browser: ${e}`, 'error');
            toast('Autoplay blocked by browser. Use play button.', 'error');
            setAutoplayWarning(true);
          }
        }
      } else {
        toast('No audio returned. Text fallback shown.', 'error');
      }

      if (json.entry) prependHistory(json.entry);

      const elapsed = performance.now() - turnStartedAt;
      setLatency(elapsed);
      setStatus('Wake command complete', 'ok');
      log(`Wake text-turn complete (${Math.round(elapsed)} ms)`, 'ok');
    } finally {
      setProcessing(false);
    }
  }

  function sleepMs(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function stopWakeListening(reason = 'manual') {
    wakeLoopAbort = true;
    wakePrimedUntilMs = 0;

    if (wakeSpeech) {
      try {
        wakeSpeech.stop();
      } catch (_) {
        // ignore
      }
    }
    wakeSpeechRunning = false;

    try {
      if (wakeChunkRecorder && wakeChunkRecorder.state === 'recording') {
        wakeChunkRecorder.stop();
      }
    } catch (_) {
      // ignore
    }

    if (wakeStream) {
      try {
        wakeStream.getTracks().forEach((t) => t.stop());
      } catch (_) {
        // ignore
      }
    }

    wakeStream = null;
    wakeChunkRecorder = null;
    wakeActive = false;
    wakeDetectBusy = false;
    wakeDetectionMode = 'none';

    if (!wakeCommandRunning) {
      setWakeStatus(reason === 'toggle-off' ? 'Wake word disabled' : 'Wake word idle');
    }
    log(`Wake listener stopped (${reason})`);
  }

  async function captureWakeChunk(durationMs = 1800) {
    if (!wakeStream || !wakeActive || wakeLoopAbort) return null;

    return new Promise((resolve) => {
      const options = MediaRecorder.isTypeSupported('audio/webm')
        ? { mimeType: 'audio/webm' }
        : (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
          ? { mimeType: 'audio/webm;codecs=opus' }
          : undefined);

      const localChunks = [];
      let settled = false;

      let rec;
      try {
        rec = options ? new MediaRecorder(wakeStream, options) : new MediaRecorder(wakeStream);
      } catch (err) {
        log(`Wake chunk recorder init failed: ${err}`, 'error');
        resolve(null);
        return;
      }

      wakeChunkRecorder = rec;

      rec.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) localChunks.push(event.data);
      };

      rec.onstop = () => {
        if (settled) return;
        settled = true;
        if (wakeChunkRecorder === rec) wakeChunkRecorder = null;
        if (!localChunks.length) {
          resolve(null);
          return;
        }
        resolve(new Blob(localChunks, { type: rec.mimeType || 'audio/webm' }));
      };

      try {
        rec.start();
      } catch (err) {
        log(`Wake chunk recorder start failed: ${err}`, 'error');
        resolve(null);
        return;
      }

      setTimeout(() => {
        try {
          if (rec.state === 'recording') rec.stop();
        } catch (_) {
          // ignore
        }
      }, durationMs);
    });
  }

  async function handleWakeChunk(blob) {
    if (!wakeEnableEl?.checked || !wakeActive || wakeCommandRunning) return;
    if (wakeDetectBusy) return;
    if (!blob || !blob.size) return;

    wakeDetectBusy = true;
    try {
      // --- Primary: openWakeWord backend detection ---
      const form = new FormData();
      form.append('audio', blob, 'wake.webm');
      let owwDetected = false;
      try {
        const owwRes = await fetch('/api/wake-detect', { method: 'POST', body: form });
        if (owwRes.ok) {
          const owwJson = await owwRes.json();
          if (owwJson.ok) {
            log(`openWakeWord score: ${owwJson.score?.toFixed(3)} (threshold ${owwJson.threshold}) detected=${owwJson.detected}`);
            if (owwJson.detected) {
              owwDetected = true;
              // Fire a synthetic wake event — next speech chunk or primed window handles command
              await processWakeTranscript('trinity', 'oww');
              return;
            }
            // OWW ran fine but no detection — skip chunk fallback transcription
            return;
          }
        }
        // OWW endpoint failed — fall through to transcription fallback
        log('openWakeWord endpoint unavailable, falling back to transcription', 'warn');
      } catch (owwErr) {
        log(`openWakeWord error: ${owwErr} — falling back to transcription`, 'warn');
      }

      // --- Fallback: transcription-based detection ---
      const form2 = new FormData();
      form2.append('audio', blob, 'wake.webm');
      const res = await fetch('/api/transcribe', { method: 'POST', body: form2 });
      const json = await res.json();
      if (!res.ok || !json.ok) {
        const errMsg = json && json.error ? json.error : `wake transcribe failed (${res.status})`;
        log(`Wake transcribe soft-fail: ${errMsg}`, 'error');
        return;
      }
      const txt = json.transcript || '';
      if (!txt) return;
      await processWakeTranscript(txt, 'chunk-fallback');
    } catch (err) {
      log(`Wake detection error: ${err}`, 'error');
    } finally {
      wakeDetectBusy = false;
    }
  }

  function ensureWakeSpeechRecognition() {
    if (!SpeechRecognitionCtor) return null;
    if (wakeSpeech) return wakeSpeech;

    const rec = new SpeechRecognitionCtor();
    rec.lang = 'en-US';
    rec.continuous = true;
    rec.interimResults = false;

    rec.onresult = (event) => {
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (!result?.isFinal) continue;
        const transcript = (result[0]?.transcript || '').trim();
        if (!transcript) continue;
        processWakeTranscript(transcript, 'speech').catch((err) => {
          log(`Wake speech processing error: ${err}`, 'error');
        });
      }
    };

    rec.onerror = (event) => {
      const err = event?.error || 'unknown';
      if (err === 'aborted') {
        return;
      }
      log(`Wake speech error: ${err}`, 'error');
      if (['not-allowed', 'service-not-allowed', 'audio-capture'].includes(err)) {
        toast('Wake word speech API blocked. Check browser mic permissions.', 'error');
        if (wakeEnableEl) wakeEnableEl.checked = false;
        stopWakeListening('speech-permission-error');
      }
    };

    rec.onend = () => {
      wakeSpeechRunning = false;
      if (wakeEnableEl?.checked && wakeActive && !wakeLoopAbort && !wakeCommandRunning) {
        setTimeout(() => {
          if (wakeEnableEl?.checked && wakeActive && !wakeLoopAbort && !wakeCommandRunning) {
            startWakeSpeechRecognition();
          }
        }, 150);
      }
    };

    wakeSpeech = rec;
    return rec;
  }

  function startWakeSpeechRecognition() {
    const rec = ensureWakeSpeechRecognition();
    if (!rec) return false;

    try {
      rec.start();
      wakeSpeechRunning = true;
      wakeDetectionMode = 'speech';
      setWakeStatus('Listening for "Trinity"…');
      log('Wake listener started (browser speech API)', 'ok');
      return true;
    } catch (err) {
      // start() throws if already started or unavailable in current browser/session.
      const msg = String(err || 'unknown');
      if (msg.toLowerCase().includes('already started')) {
        wakeSpeechRunning = true;
        wakeDetectionMode = 'speech';
        return true;
      }
      log(`Wake speech start failed: ${msg}`, 'error');
      return false;
    }
  }

  async function runWakeLoop() {
    while (wakeActive && !wakeLoopAbort && wakeEnableEl?.checked && !wakeCommandRunning) {
      if (wakePrimedUntilMs && Date.now() > wakePrimedUntilMs) {
        wakePrimedUntilMs = 0;
        setWakeStatus('Listening for "Trinity"…');
      }

      const blob = await captureWakeChunk(2400);
      if (!wakeActive || wakeLoopAbort || !wakeEnableEl?.checked || wakeCommandRunning) break;
      if (blob && blob.size > 0) {
        await handleWakeChunk(blob);
      }
      await sleepMs(100);
    }
  }

  async function startWakeListening() {
    if (wakeActive || wakeCommandRunning) return;
    if (!wakeEnableEl?.checked) return;

    wakeActive = true;
    wakeLoopAbort = false;
    wakePrimedUntilMs = 0;

    // Primary path: browser speech recognition (fast, low-latency).
    if (startWakeSpeechRecognition()) {
      return;
    }

    // Fallback path: chunked audio → /api/wake-detect (openWakeWord) or transcription.
    try {
      wakeStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      wakeDetectionMode = 'chunk';
      setWakeStatus('Listening for "Trinity"…');
      log('Wake listener started (chunk fallback)');

      // fire-and-forget loop
      runWakeLoop().catch((err) => {
        log(`Wake loop crashed: ${err}`, 'error');
      });
    } catch (err) {
      log(`Wake listener failed: ${err}`, 'error');
      setWakeStatus('Wake word mic failed');
      toast('Wake word mic access failed', 'error');
      if (wakeEnableEl) wakeEnableEl.checked = false;
      wakeActive = false;
    }
  }

  async function triggerWakeCommand(inlineCommand = '') {
    if (wakeCommandRunning) return;

    const inline = (inlineCommand || '').trim();
    if (!inline || inline.length < 2 || isWakeOnlyText(inline)) return;

    wakeCommandRunning = true;
    wakePrimedUntilMs = 0;
    setWakeStatus('Wake word detected – running command…');
    log(`Wake command accepted: "${inline}"`, 'ok');

    await stopWakeListening('wake-command-exec');

    try {
      await doTextTurn(inline);
    } catch (err) {
      log(`Wake command failed: ${err}`, 'error');
      toast('Wake command failed. See log.', 'error');
    }

    wakeCommandRunning = false;
    if (wakeEnableEl?.checked) {
      setWakeStatus('Listening for "Trinity"…');
      startWakeListening();
    } else {
      setWakeStatus('Wake word disabled');
    }
  }

  async function startRecording(mode = 'normal') {
    if (mediaRecorder && mediaRecorder.state === 'recording') return;
    holdMode = mode === 'hold';

    try {
      if (wakeEnableEl?.checked) {
        await stopWakeListening('manual-record');
      }
      activeStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      chunks = [];

      const options = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
        ? { mimeType: 'audio/webm;codecs=opus' }
        : { mimeType: 'audio/webm' };

      mediaRecorder = new MediaRecorder(activeStream, options);

      mediaRecorder.ondataavailable = (event) => {
        if (event.data && event.data.size > 0) chunks.push(event.data);
      };

      mediaRecorder.onstop = async () => {
        const blob = new Blob(chunks, { type: mediaRecorder.mimeType || 'audio/webm' });
        if (activeStream) {
          activeStream.getTracks().forEach((t) => t.stop());
          activeStream = null;
        }

        recordBtn.disabled = false;
        stopBtn.disabled = false;
        holdBtn.classList.remove('recording');
        refreshStopButtonLabel();

        log(`Recorded ${Math.round(blob.size / 1024)} KB`);
        if (blob.size < 500) {
          setStatus('Recording too short', 'error');
          toast('Recording too short. Try again.', 'error');
          return;
        }

        try {
          if (wakeEnableEl?.checked) {
            startWakeListening();
          }
          await doVoiceTurn(blob);
        } catch (err) {
          setStatus('Voice turn failed', 'error');
          log(`${err}`, 'error');
          toast('Voice turn failed. See log.', 'error');
        }
      };

      mediaRecorder.start();
      recordBtn.disabled = true;
      stopBtn.disabled = false;
      holdBtn.classList.add('recording');
      refreshStopButtonLabel();
      setStatus(holdMode ? 'Recording (hold)…' : 'Recording…', 'recording');
      log('Recording started');
    } catch (err) {
      setStatus('Mic access failed', 'error');
      log(`Mic access failed: ${err}`, 'error');
      toast('Mic access failed', 'error');
    }
  }

  function stopRecording() {
    if (!mediaRecorder || mediaRecorder.state !== 'recording') return;
    mediaRecorder.stop();
    refreshStopButtonLabel();
    setStatus('Uploading…', 'processing');
    setProcessing(true);
    log('Recording stopped');
  }

  async function stopServerTurn() {
    if (stopInFlight) return;
    stopInFlight = true;
    stopBtn.disabled = true;
    refreshStopButtonLabel();

    try {
      try {
        const res = await fetch('/api/stop', { method: 'POST' });
        const json = await res.json();
        if (!res.ok || !json.ok) throw new Error(json.error || `stop failed (${res.status})`);
        log(`Stop endpoint: ${json.detail || (json.stopped ? 'stopped' : 'idle')}`, json.stopped ? 'ok' : 'info');
      } catch (err) {
        log(`Stop endpoint failed: ${err}`, 'error');
      }

      try {
        player.pause();
        player.removeAttribute('src');
        player.load();
      } catch (_) {
        // ignore local player reset issues
      }

      if (waveProgressEl) waveProgressEl.style.width = '0%';
      setProcessing(false);
      setStatus('Stopped', 'ok');
      toast('Stopped current voice playback/generation', 'ok');
    } finally {
      if (!mediaRecorder || mediaRecorder.state !== 'recording') {
        recordBtn.disabled = false;
        stopBtn.disabled = false;
        holdBtn.classList.remove('recording');
      }
      stopInFlight = false;
      refreshStopButtonLabel();
    }
  }

  // Classic click flow
  recordBtn.addEventListener('click', () => startRecording('normal'));
  stopBtn.addEventListener('click', async () => {
    if (mediaRecorder && mediaRecorder.state === 'recording') {
      stopRecording();
      return;
    }
    await stopServerTurn();
  });

  // Hold-to-talk flow
  holdBtn.addEventListener('pointerdown', async (e) => {
    e.preventDefault();
    await startRecording('hold');
  });
  holdBtn.addEventListener('pointerup', (e) => {
    e.preventDefault();
    if (holdMode) stopRecording();
  });
  holdBtn.addEventListener('pointercancel', () => {
    if (holdMode) stopRecording();
  });
  holdBtn.addEventListener('pointerleave', (e) => {
    if (holdMode && e.buttons === 0) stopRecording();
  });

  // Spacebar hold-to-talk
  let spaceHeld = false;
  window.addEventListener('keydown', async (e) => {
    if (e.code !== 'Space' || e.repeat) return;
    const tag = (document.activeElement?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'textarea' || document.activeElement?.isContentEditable) return;
    e.preventDefault();
    if (spaceHeld) return;
    spaceHeld = true;
    await startRecording('hold');
  });

  window.addEventListener('keyup', (e) => {
    if (e.code !== 'Space') return;
    if (!spaceHeld) return;
    e.preventDefault();
    spaceHeld = false;
    if (holdMode) stopRecording();
  });

  voiceProviderEl.addEventListener('change', () => {
    const provider = voiceProviderEl.value;
    const selected = provider === 'fish'
      ? 's1'
      : provider === 'elevenlabs'
        ? 'eleven_multilingual_v2'
        : provider === 'local-piper'
          ? 'en_US-lessac-medium'
          : provider === 'local-kokoro'
            ? 'af_bella'
            : 'speech-2.8-turbo';
    renderModelOptions(provider, selected);
    if (provider === 'fish') {
      voiceRefEl.placeholder = 'optional fish reference id';
      if (!voiceRefEl.value.trim()) voiceRefEl.value = '';
    } else if (provider === 'elevenlabs') {
      voiceRefEl.placeholder = 'elevenlabs voice id';
      if (!voiceRefEl.value.trim()) voiceRefEl.value = 'JBFqnCBsd6RMkjVDRZzb';
    } else if (provider === 'local-piper') {
      voiceRefEl.placeholder = 'piper voice id (auto from model)';
      voiceRefEl.value = voiceModelEl.value;
    } else if (provider === 'local-kokoro') {
      voiceRefEl.placeholder = 'kokoro voice id (auto from model)';
      voiceRefEl.value = voiceModelEl.value;
    } else {
      renderMinimaxVoiceOptions(minimaxVoiceSelectEl?.value || voiceRefEl.value || 'English_Graceful_Lady');
      if (minimaxVoiceSelectEl && !minimaxVoiceSelectEl.value && minimaxEnglishVoices.length) {
        minimaxVoiceSelectEl.value = minimaxEnglishVoices[0];
      }
      voiceRefEl.value = minimaxVoiceSelectEl?.value || 'English_Graceful_Lady';
    }
    syncVoiceRefUi();
  });

  player.addEventListener('timeupdate', () => {
    if (!waveProgressEl || !player.duration || Number.isNaN(player.duration)) return;
    const pct = Math.max(0, Math.min(100, (player.currentTime / player.duration) * 100));
    waveProgressEl.style.width = `${pct}%`;
    refreshStopButtonLabel();
  });
  player.addEventListener('play', () => {
    if (stopBtn && !stopInFlight) stopBtn.disabled = false;
    startInterruptListener();
    refreshStopButtonLabel();
  });
  player.addEventListener('pause', () => {
    if (stopBtn && !stopInFlight) stopBtn.disabled = processingSpinnerEl?.classList.contains('show') ? false : true;
    if (!processingSpinnerEl?.classList.contains('show')) stopInterruptListener();
    refreshStopButtonLabel();
  });
  player.addEventListener('ended', () => {
    if (waveProgressEl) waveProgressEl.style.width = '100%';
    if (stopBtn && !stopInFlight) stopBtn.disabled = true;
    stopInterruptListener();
    refreshStopButtonLabel();
  });
  player.addEventListener('loadedmetadata', () => {
    if (waveProgressEl) waveProgressEl.style.width = '0%';
    if (stopBtn && !stopInFlight) stopBtn.disabled = false;
    refreshStopButtonLabel();
  });

  minimaxVoiceSelectEl?.addEventListener('change', () => {
    if (voiceProviderEl?.value === 'minimax' && voiceRefEl) {
      voiceRefEl.value = minimaxVoiceSelectEl.value;
    }
  });

  applyVoiceBtn.addEventListener('click', applyVoiceConfig);
  previewVoiceBtn.addEventListener('click', previewVoice);
  catalogModeEl?.addEventListener('change', () => renderCatalog(catalogCache));
  toggleCatalogBtn?.addEventListener('click', () => {
    if (!catalogEl) return;
    const isHidden = catalogEl.style.display === 'none';
    catalogEl.style.display = isHidden ? '' : 'none';
    toggleCatalogBtn.textContent = isHidden ? '▼' : '▶';
  });

  // start catalog hidden
  if (catalogEl) { catalogEl.style.display = 'none'; toggleCatalogBtn.textContent = '▶'; }

  // start reminder history collapsed (not hidden — data already loaded into it)
  const initReminderHistoryCollapse = () => {
    if (reminderHistoryEl) reminderHistoryEl.style.display = 'none';
    if (toggleReminderHistoryBtn) toggleReminderHistoryBtn.textContent = '▲';
  };
  if (reminderHistoryEl) initReminderHistoryCollapse();

  document.getElementById('toggleHistoryBtn')?.addEventListener('click', () => {
    if (!historyEl) return;
    const collapsed = historyEl.style.display === 'none';
    historyEl.style.display = collapsed ? '' : 'none';
    toggleHistoryBtn.textContent = collapsed ? '▼' : '▲';
  });

  toggleLogBtn?.addEventListener('click', () => {
    if (!logEl) return;
    const collapsed = logEl.style.display === 'none';
    logEl.style.display = collapsed ? '' : 'none';
    toggleLogBtn.textContent = collapsed ? '▼' : '▲';
  });

  document.getElementById('dismissReminderHistoryBtn')?.addEventListener('click', () => {
    if (!reminderHistoryEl) return;
    reminderHistoryEl.innerHTML = '';
    if (reminderHistoryEl) reminderHistoryEl.style.display = '';
    if (dismissReminderHistoryBtn) dismissReminderHistoryBtn.style.display = 'none';
    if (toggleReminderHistoryBtn) toggleReminderHistoryBtn.style.display = '';
  });
  if (dismissReminderHistoryBtn && toggleReminderHistoryBtn) toggleReminderHistoryBtn.style.display = 'none';

  wakeEnableEl?.addEventListener('change', () => {
    if (wakeEnableEl.checked) {
      startWakeListening();
    } else {
      stopWakeListening('toggle-off');
    }
  });
  if (wakeEnableEl?.checked) startWakeListening();

  // talk/voice/catalog detail toggles handled by inline onclick in HTML

  renderLog();
  refreshStopButtonLabel();
  checkHealth();
  ensureCatalogStyles();
  renderFilterChips();
  loadVoiceConfig();
  loadHistory();
  loadCatalog();
  loadReminderLists();
  pollDueReminders();
  reminderPollTimer = setInterval(pollDueReminders, 5000);
})();
