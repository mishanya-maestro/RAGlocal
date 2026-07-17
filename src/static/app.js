function $(id) {
  return document.getElementById(id);
}

const STORAGE_KEY = "rag.chat.v1";
let isLoading = false;

const ICONS = {
  copy: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <rect x="9" y="9" width="13" height="13" rx="2" ry="2"></rect>
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"></path>
  </svg>`,
  check: `<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="20 6 9 17 4 12"></polyline>
  </svg>`,
};

/* ---------- helpers ---------- */

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderMarkdown(text) {
  const raw = String(text || "");
  if (window.marked && window.DOMPurify) {
    const html = window.marked.parse(raw, { breaks: true, gfm: true });
    return window.DOMPurify.sanitize(html);
  }
  return escapeHtml(raw).replaceAll("\n", "<br>");
}

async function postJSON(url, payload) {
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error(data.error || `HTTP ${res.status}`);
  }
  return data;
}

/* ---------- chat history persistence ---------- */

function loadHistory() {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch (_e) {
    return [];
  }
}

function saveHistory(history) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(history));
  } catch (_e) {
    /* ignore quota errors */
  }
}

function pushHistory(entry) {
  const h = loadHistory();
  h.push(entry);
  saveHistory(h);
}

function clearHistory() {
  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (_e) {
    /* ignore */
  }
}

/* ---------- sources & rendering ---------- */

function sourceLink(src) {
  if (!src || !src.code || !src.number) {
    return `<span>${escapeHtml(src?.label || "Источник")}</span>`;
  }
  const href = `/source?code=${encodeURIComponent(src.code)}&number=${encodeURIComponent(src.number)}`;
  return `<a href="${href}">${escapeHtml(src.label || `${src.code}, ст. ${src.number}`)}</a>`;
}

function renderSourcesHtml(sources) {
  const items = (sources || [])
    .map((src) =>
      typeof src === "string"
        ? `<li>${escapeHtml(src)}</li>`
        : `<li>${sourceLink(src)}</li>`
    )
    .join("");
  if (!items) {
    return "";
  }
  return `<div class="sources-inline"><span>Источники</span><ul>${items}</ul></div>`;
}

function exitEmptyState() {
  const main = $("appMain");
  if (main) main.classList.remove("is-empty");
}

function enterEmptyState() {
  const main = $("appMain");
  if (main) main.classList.add("is-empty");
}

function appendMessageRow(role, html, { scroll = true } = {}) {
  exitEmptyState();
  const row = document.createElement("article");
  row.className = `message-row ${role}`;
  row.innerHTML = html;
  $("chatMessages").appendChild(row);
  if (scroll) {
    row.scrollIntoView({ behavior: "smooth", block: "end" });
  }
  return row;
}

function appendUserMessage(text, opts) {
  return appendMessageRow(
    "user",
    `<div class="user-box"><p>${escapeHtml(text)}</p></div>`,
    opts
  );
}

function loadingMarkup() {
  return `
    <div class="loader-inline" aria-label="Загрузка">
      <div class="loader">
        <div class="circle"></div>
        <div class="circle"></div>
        <div class="circle"></div>
        <div class="circle"></div>
        <div class="circle"></div>
      </div>
    </div>
  `;
}

function appendAssistantLoading() {
  return appendMessageRow("assistant", loadingMarkup());
}

function setLoading(nextState) {
  isLoading = nextState;
  $("sendBtn").disabled = nextState;
}

function copyButton(text) {
  const payload = encodeURIComponent(text || "");
  return `
    <button class="copy-btn" type="button" data-copy="${payload}" aria-label="Скопировать" title="Скопировать">
      ${ICONS.copy}
    </button>
  `;
}

function renderNormalResponse(data) {
  const answerText = data.answer || "";
  const answerHtml = renderMarkdown(answerText);
  return `
    <div class="assistant-answer">
      <div class="answer-markdown">${answerHtml}</div>
      ${renderSourcesHtml(data.source_meta || data.sources || [])}
      ${copyButton(answerText)}
    </div>
  `;
}

function collapsibleBlock(markdownHtml) {
  return `
    <div class="collapsible-wrap" data-collapsed="true">
      <div class="answer-markdown">${markdownHtml}</div>
      <button type="button" class="expand-btn" hidden>Развернуть</button>
    </div>
  `;
}

function renderCompareResponse(data) {
  const directText = data.direct_error
    ? `Ошибка LLM: ${data.direct_error}`
    : data.direct_answer || "";
  const ragText = data.rag_error
    ? `Ошибка RAG: ${data.rag_error}`
    : data.rag_answer || "";
  const sourcesHtml = renderSourcesHtml(data.source_meta || data.sources || []);

  return `
    <div class="assistant-answer">
      <div class="compare-grid">
        <section class="compare-card">
          <h4>Без RAG</h4>
          ${collapsibleBlock(renderMarkdown(directText))}
          ${copyButton(directText)}
        </section>
        <section class="compare-card">
          <h4>С RAG</h4>
          ${collapsibleBlock(renderMarkdown(ragText))}
          ${sourcesHtml}
          ${copyButton(ragText)}
        </section>
      </div>
    </div>
  `;
}

function initCollapsibles(scope) {
  const root = scope || document;
  const wraps = root.querySelectorAll(".collapsible-wrap:not([data-init])");
  wraps.forEach((wrap) => {
    wrap.dataset.init = "1";
    const content = wrap.querySelector(".answer-markdown");
    const btn = wrap.querySelector(".expand-btn");
    if (!content || !btn) return;

    requestAnimationFrame(() => {
      const collapsedLimit = 280;
      const fullHeight = content.scrollHeight;
      if (fullHeight <= collapsedLimit + 8) {
        wrap.dataset.collapsed = "false";
        btn.hidden = true;
      } else {
        wrap.dataset.collapsed = "true";
        btn.hidden = false;
        btn.textContent = "Развернуть";
      }
    });
  });
}

/* ---------- copy ---------- */

async function copyToClipboard(button) {
  const text = decodeURIComponent(button.dataset.copy || "");
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    const original = button.innerHTML;
    button.innerHTML = ICONS.check;
    button.classList.add("copied");
    button.title = "Скопировано";
    setTimeout(() => {
      button.classList.remove("copied");
      button.innerHTML = original;
      button.title = "Скопировать";
    }, 1300);
  } catch (_error) {
    $("chatStatus").textContent = "Не удалось скопировать.";
  }
}

/* ---------- custom dropdowns ---------- */

function closeAllDropdowns(except) {
  document.querySelectorAll(".mode-dd.open").forEach((dd) => {
    if (dd === except) return;
    dd.classList.remove("open");
    const t = dd.querySelector(".mode-dd-trigger");
    if (t) t.setAttribute("aria-expanded", "false");
  });
}

function initDropdown(rootEl) {
  const trigger = rootEl.querySelector(".mode-dd-trigger");
  const menu = rootEl.querySelector(".mode-dd-menu");
  const label = rootEl.querySelector(".mode-dd-label");
  if (!trigger || !menu || !label) return;

  menu.removeAttribute("hidden");

  trigger.addEventListener("click", (event) => {
    event.stopPropagation();
    const isOpen = rootEl.classList.contains("open");
    closeAllDropdowns(rootEl);
    if (isOpen) {
      rootEl.classList.remove("open");
      trigger.setAttribute("aria-expanded", "false");
    } else {
      rootEl.classList.add("open");
      trigger.setAttribute("aria-expanded", "true");
    }
  });

  menu.addEventListener("click", (event) => {
    event.stopPropagation();
  });

  menu.querySelectorAll(".mode-dd-option").forEach((opt) => {
    opt.addEventListener("click", () => {
      const value = opt.dataset.value;
      rootEl.dataset.value = value;
      label.textContent = opt.textContent.trim();
      menu.querySelectorAll(".mode-dd-option").forEach((o) => {
        o.setAttribute("aria-selected", o === opt ? "true" : "false");
      });
      rootEl.classList.remove("open");
      trigger.setAttribute("aria-expanded", "false");
      rootEl.dispatchEvent(
        new CustomEvent("mode-dd:change", {
          detail: { value },
          bubbles: true,
        })
      );
    });
  });
}

function syncChatTypeLayout() {
  const main = $("appMain");
  if (!main) return;
  main.dataset.chatType = getDDValue("chatType") || "normal";
}

function getDDValue(id) {
  const el = $(id);
  return el?.dataset.value || "";
}

/* ---------- main flow ---------- */

function autosizeInput() {
  const el = $("chatInput");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 180) + "px";
}

async function sendQuery() {
  if (isLoading) return;
  const q = $("chatInput").value.trim();
  const transportMode = (getDDValue("transportMode") || "").toLowerCase();
  const chatType = getDDValue("chatType") || "normal";
  $("chatStatus").textContent = "";

  if (!q) {
    $("chatStatus").textContent = "Введите запрос.";
    return;
  }

  appendUserMessage(q);
  $("chatInput").value = "";
  autosizeInput();
  const loaderMsg = appendAssistantLoading();
  setLoading(true);
  $("chatStatus").textContent =
    chatType === "compare"
      ? `Сравниваю ответы (${transportMode})...`
      : `Генерирую ответ (${transportMode})...`;

  try {
    const endpoint = chatType === "compare" ? "/api/compare" : "/api/ask";
    const data = await postJSON(endpoint, { query: q, mode: transportMode });
    const html =
      chatType === "compare"
        ? renderCompareResponse(data)
        : renderNormalResponse(data);
    loaderMsg.innerHTML = html;
    initCollapsibles(loaderMsg);
    pushHistory({ role: "user", text: q });
    pushHistory({
      role: "assistant",
      kind: chatType === "compare" ? "compare" : "normal",
      data,
    });
    $("chatStatus").textContent = `Готово (${data.mode || transportMode}).`;
  } catch (e) {
    const errMsg = e?.message || "Неизвестная ошибка";
    loaderMsg.innerHTML = `<p class="error-text">Ошибка: ${escapeHtml(errMsg)}</p>`;
    pushHistory({ role: "user", text: q });
    pushHistory({ role: "assistant", kind: "error", text: errMsg });
    $("chatStatus").textContent = "Ошибка запроса.";
  } finally {
    setLoading(false);
    $("chatInput").focus();
    loaderMsg.scrollIntoView({ behavior: "smooth", block: "end" });
  }
}

function restoreHistory() {
  const history = loadHistory();
  if (!history.length) return;
  for (const entry of history) {
    if (entry.role === "user") {
      appendUserMessage(entry.text, { scroll: false });
    } else if (entry.role === "assistant") {
      if (entry.kind === "normal") {
        appendMessageRow("assistant", renderNormalResponse(entry.data), { scroll: false });
      } else if (entry.kind === "compare") {
        appendMessageRow("assistant", renderCompareResponse(entry.data), { scroll: false });
      } else if (entry.kind === "error") {
        appendMessageRow(
          "assistant",
          `<p class="error-text">Ошибка: ${escapeHtml(entry.text)}</p>`,
          { scroll: false }
        );
      }
    }
  }
  initCollapsibles();
  requestAnimationFrame(() => {
    window.scrollTo({ top: document.body.scrollHeight, behavior: "auto" });
  });
}

function startNewChat() {
  clearHistory();
  $("chatMessages").innerHTML = "";
  enterEmptyState();
  $("chatStatus").textContent = "";
  $("chatInput").value = "";
  autosizeInput();
  $("chatInput").focus();
}

/* ---------- wiring ---------- */

$("sendBtn").addEventListener("click", sendQuery);

$("chatInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendQuery();
  }
});

$("chatInput").addEventListener("input", autosizeInput);

$("chatMessages").addEventListener("click", (event) => {
  const copyBtn = event.target.closest(".copy-btn");
  if (copyBtn) {
    copyToClipboard(copyBtn);
    return;
  }
  const expandBtn = event.target.closest(".expand-btn");
  if (expandBtn) {
    const wrap = expandBtn.closest(".collapsible-wrap");
    if (!wrap) return;
    const collapsed = wrap.dataset.collapsed !== "false";
    if (collapsed) {
      wrap.dataset.collapsed = "false";
      expandBtn.textContent = "Свернуть";
    } else {
      wrap.dataset.collapsed = "true";
      expandBtn.textContent = "Развернуть";
      wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
    }
  }
});

document.addEventListener("click", () => closeAllDropdowns());

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeAllDropdowns();
});

$("newChatBtn").addEventListener("click", () => {
  startNewChat();
});

document.querySelectorAll(".mode-dd").forEach(initDropdown);

$("chatType")?.addEventListener("mode-dd:change", syncChatTypeLayout);
syncChatTypeLayout();

restoreHistory();
autosizeInput();

/* ---------- Voice input (STT into composer) ---------- */

const mic = {
  recorder: null,
  stream: null,
  chunks: [],
  mime: "",
  state: "idle", // idle | recording | transcribing
};

function micEl() {
  return $("micBtn");
}

function setMicState(next) {
  mic.state = next;
  const btn = micEl();
  if (!btn) return;
  btn.dataset.state = next;
  btn.disabled = next === "transcribing";
  btn.setAttribute(
    "aria-label",
    next === "recording"
      ? "Остановить запись"
      : next === "transcribing"
      ? "Распознавание..."
      : "Голосовой ввод"
  );
  btn.title = btn.getAttribute("aria-label");
}

function pickRecorderMime() {
  const candidates = [
    "audio/webm;codecs=opus",
    "audio/webm",
    "audio/ogg;codecs=opus",
    "audio/mp4",
  ];
  if (typeof MediaRecorder === "undefined") return "";
  for (const m of candidates) {
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) {
      return m;
    }
  }
  return "";
}

function releaseMicStream() {
  if (mic.stream) {
    mic.stream.getTracks().forEach((t) => t.stop());
    mic.stream = null;
  }
}

function appendToChatInput(text) {
  const input = $("chatInput");
  if (!input || !text) return;
  const cur = input.value;
  const sep = cur && !/\s$/.test(cur) ? " " : "";
  input.value = `${cur}${sep}${text}`;
  autosizeInput();
  input.focus();
  try {
    input.setSelectionRange(input.value.length, input.value.length);
  } catch (_) {
    /* ignore */
  }
}

async function startMicRecording() {
  const btn = micEl();
  if (!btn) return;

  if (btn.dataset.sttReady === "false") {
    $("chatStatus").textContent =
      "Голосовой ввод недоступен: добавьте ASSEMBLYAI_API_KEY в .env и перезапустите сервер.";
    return;
  }

  if (mic.state === "recording") {
    stopMicRecording(false);
    return;
  }
  if (mic.state === "transcribing") return;

  if (!navigator.mediaDevices?.getUserMedia) {
    $("chatStatus").textContent = "Микрофон недоступен в этом браузере.";
    return;
  }

  try {
    mic.stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (_e) {
    $("chatStatus").textContent =
      "Нет доступа к микрофону. Разрешите доступ и повторите.";
    return;
  }

  mic.mime = pickRecorderMime();
  try {
    mic.recorder = mic.mime
      ? new MediaRecorder(mic.stream, { mimeType: mic.mime })
      : new MediaRecorder(mic.stream);
  } catch (_e) {
    $("chatStatus").textContent = "Запись не поддерживается этим браузером.";
    releaseMicStream();
    return;
  }

  mic.chunks = [];
  mic.recorder.addEventListener("dataavailable", (ev) => {
    if (ev.data && ev.data.size > 0) mic.chunks.push(ev.data);
  });
  mic.recorder.addEventListener("stop", async () => {
    const silent = !!mic.recorder._silent;
    const type = mic.mime || "audio/webm";
    const blob = new Blob(mic.chunks, { type });
    releaseMicStream();
    if (silent) {
      setMicState("idle");
      return;
    }
    if (!blob.size) {
      setMicState("idle");
      $("chatStatus").textContent = "Запись пуста.";
      return;
    }
    await transcribeBlob(blob);
  });

  mic.recorder.start();
  setMicState("recording");
  $("chatStatus").textContent = "Слушаю... Нажмите микрофон снова, чтобы остановить.";
}

function stopMicRecording(silent) {
  if (mic.recorder && mic.recorder.state !== "inactive") {
    mic.recorder._silent = !!silent;
    try {
      mic.recorder.stop();
    } catch (_) {
      /* ignore */
    }
  } else {
    releaseMicStream();
    setMicState("idle");
  }
}

async function transcribeBlob(blob) {
  setMicState("transcribing");
  $("chatStatus").textContent = "Распознаю речь...";
  try {
    const form = new FormData();
    const ext = (blob.type.split("/")[1] || "webm").split(";")[0];
    form.append("audio", blob, `voice.${ext}`);
    const res = await fetch("/api/transcribe", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);

    const transcript = (data.text || "").trim();
    if (!transcript) {
      $("chatStatus").textContent = "Не удалось распознать речь, попробуйте ещё раз.";
      return;
    }
    appendToChatInput(transcript);
    $("chatStatus").textContent = "Готово. Нажмите ↵ или кнопку отправки.";
  } catch (e) {
    $("chatStatus").textContent = `Ошибка распознавания: ${e?.message || e}`;
  } finally {
    setMicState("idle");
  }
}

micEl()?.addEventListener("click", startMicRecording);

/* ---------- Model setup (autoselect) ---------- */

const SETUP_SKIP_KEY = "rag.setup.skip.v1";
let setupPollTimer = null;
let setupSelection = { llm: "", asr: "", embedding: "", reranker: "" };
let setupLatest = null;

const DL_ICON = `<svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
  <path d="M12 3v12"></path>
  <path d="M7 10l5 5 5-5"></path>
  <path d="M5 21h14"></path>
</svg>`;

function setupSkipped() {
  try {
    return localStorage.getItem(SETUP_SKIP_KEY) === "1";
  } catch (_e) {
    return false;
  }
}

function setSetupSkipped(value) {
  try {
    if (value) localStorage.setItem(SETUP_SKIP_KEY, "1");
    else localStorage.removeItem(SETUP_SKIP_KEY);
  } catch (_e) {
    /* ignore */
  }
}

function hideSetupOverlay() {
  const overlay = $("setupOverlay");
  if (overlay) overlay.hidden = true;
  if (setupPollTimer) {
    clearInterval(setupPollTimer);
    setupPollTimer = null;
  }
}

function showSetupOverlay() {
  const overlay = $("setupOverlay");
  if (overlay) overlay.hidden = false;
}

function formatSizeGb(value) {
  const n = Number(value || 0);
  if (n >= 10) return `${n.toFixed(0)} GB`;
  return `${n.toFixed(1)} GB`;
}

function currentSelection() {
  return {
    llm: setupSelection.llm,
    asr: setupSelection.asr,
    embedding: setupSelection.embedding,
    reranker: setupSelection.reranker,
  };
}

function progressFor(modelId, install) {
  const map = install?.progress || {};
  return map[modelId] || null;
}

function renderSetupSlots(data) {
  const root = $("setupSlots");
  if (!root) return;
  const install = data.install || {};
  const slots = data.slots || [];

  root.innerHTML = slots
    .map((slot) => {
      const model = slot.model || {};
      const badge = model.badge || {};
      const prog = progressFor(model.id, install);
      const pct = prog?.pct ?? (model.installed ? 100 : 0);
      const statusText = prog?.status
        ? prog.status
        : model.installed
        ? "установлена"
        : "не установлена";
      const selectHtml = slot.selectable
        ? `<select class="setup-select" data-role="${escapeHtml(slot.role)}" aria-label="Выбор ${escapeHtml(slot.title)}">
            ${(slot.options || [])
              .map(
                (opt) =>
                  `<option value="${escapeHtml(opt.id)}" ${
                    opt.id === slot.selected_id ? "selected" : ""
                  }>${escapeHtml(opt.label)} · ${formatSizeGb(opt.size_gb)}</option>`
              )
              .join("")}
          </select>`
        : "";

      return `
        <article class="setup-slot" data-role="${escapeHtml(slot.role)}" data-model="${escapeHtml(model.id || "")}">
          <div class="setup-slot-top">
            <div class="setup-slot-meta">
              <p class="setup-slot-title">${escapeHtml(slot.title)}</p>
              <p class="setup-slot-name">${escapeHtml(model.label || model.id || "—")}</p>
              <div class="setup-slot-size">${formatSizeGb(model.size_gb)} · ${escapeHtml(model.id || "")}</div>
            </div>
            <span class="setup-badge ${escapeHtml(badge.tone || "neutral")}">${escapeHtml(badge.label || "")}</span>
            <div class="setup-slot-controls">
              ${selectHtml}
              <button
                class="setup-dl-btn ${model.installed ? "done" : ""}"
                type="button"
                data-download="${escapeHtml(model.id || "")}"
                title="${model.installed ? "Уже установлена" : "Скачать модель"}"
                aria-label="Скачать ${escapeHtml(model.id || "")}"
                ${!data.ollama_online || install.running ? "disabled" : ""}
              >${DL_ICON}</button>
            </div>
          </div>
          <div class="setup-progress" aria-hidden="true"><span style="width:${pct}%"></span></div>
          <div class="setup-progress-label">${escapeHtml(statusText)}${prog?.pct != null ? ` · ${prog.pct}%` : ""}</div>
        </article>
      `;
    })
    .join("");

  root.querySelectorAll(".setup-select").forEach((el) => {
    el.addEventListener("change", async (event) => {
      const role = event.target.dataset.role;
      const value = event.target.value;
      if (role === "llm") setupSelection.llm = value;
      if (role === "asr") setupSelection.asr = value;
      await refreshSetupStatus();
    });
  });

  root.querySelectorAll("[data-download]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const modelId = btn.getAttribute("data-download");
      if (!modelId) return;
      await downloadOneModel(modelId);
    });
  });
}

function renderSetupStatus(data) {
  setupLatest = data;
  const hw = data.hardware || {};
  const tierMeta = data.tier_meta || {};
  const models = data.models || {};
  const hwEl = $("setupHardware");
  const statusEl = $("setupStatus");
  const installBtn = $("setupInstallBtn");
  const hero = $("setupHero");
  const tierBadge = $("setupTierBadge");
  const tierSubtitle = $("setupTierSubtitle");

  if (!setupSelection.llm) setupSelection.llm = models.llm || "";
  if (!setupSelection.asr) setupSelection.asr = models.asr || "";
  if (!setupSelection.embedding) setupSelection.embedding = models.embedding || "";
  if (!setupSelection.reranker) setupSelection.reranker = models.reranker || "";

  // Синхронизация selection с сервером после смены опции
  setupSelection.llm = models.llm || setupSelection.llm;
  setupSelection.asr = models.asr || setupSelection.asr;
  setupSelection.embedding = models.embedding || setupSelection.embedding;
  setupSelection.reranker = models.reranker || setupSelection.reranker;

  if (hero) hero.dataset.tone = tierMeta.tone || data.tier || "medium";
  if (tierBadge) tierBadge.textContent = tierMeta.title || String(data.tier || "").toUpperCase();
  if (tierSubtitle) {
    tierSubtitle.textContent =
      tierMeta.subtitle ||
      "Рекомендуем модели под мощность вашего компьютера.";
  }

  if (hwEl) {
    hwEl.innerHTML = `
      <li><strong>CPU</strong>${escapeHtml(String(hw.cpu_cores || "?"))} ядер @ ${escapeHtml(String(Math.round(hw.cpu_freq_mhz || 0)))} MHz</li>
      <li><strong>RAM</strong>${escapeHtml(String(hw.ram_gb || "?"))} GB</li>
      <li><strong>GPU</strong>${escapeHtml(hw.gpu || "не обнаружен")}</li>
      <li><strong>VRAM</strong>${escapeHtml(String(hw.vram_gb || 0))} GB</li>
      <li><strong>Баллы</strong>${escapeHtml(String(hw.score || 0))}</li>
      <li><strong>RAM нужно</strong>${escapeHtml(String(data.ram_need_gb || 0))} GB</li>
    `;
  }

  renderSetupSlots(data);

  if (statusEl) {
    if (!data.ollama_online) {
      statusEl.textContent = "Ollama недоступна. Запустите Ollama и обновите страницу.";
    } else if (data.ready) {
      statusEl.textContent = "Все выбранные модели установлены.";
    } else {
      statusEl.textContent = `Нужно скачать: ${(data.missing || []).join(", ")}`;
    }
  }

  if (installBtn) {
    installBtn.disabled = !data.ollama_online || Boolean(data.install?.running);
    installBtn.textContent = data.ready
      ? "Применить выбранные модели"
      : "Установить выбранные";
  }
}

async function refreshSetupStatus() {
  const params = new URLSearchParams();
  // selection передаём через POST-like query is awkward; use install/apply body.
  // For status with selection, call a tiny workaround: send selection via headers? Better add query.
  // Instead re-fetch with fetch POST to a status endpoint isn't available.
  // We'll pass selection as query string.
  if (setupSelection.llm) params.set("llm", setupSelection.llm);
  if (setupSelection.asr) params.set("asr", setupSelection.asr);
  const url = `/api/setup/status?${params.toString()}`;
  const data = await fetch(url).then((r) => r.json());
  if (data.error) throw new Error(data.error);
  renderSetupStatus(data);
  return data;
}

async function pollInstallUntilDone() {
  const statusEl = $("setupStatus");
  const installBtn = $("setupInstallBtn");
  if (installBtn) installBtn.disabled = true;

  if (setupPollTimer) clearInterval(setupPollTimer);
  setupPollTimer = setInterval(async () => {
    try {
      const install = await fetch("/api/setup/install/status").then((r) => r.json());
      if (setupLatest) {
        setupLatest.install = install;
        renderSetupSlots(setupLatest);
      }
      if (statusEl) {
        statusEl.textContent = install.running
          ? `Установка: ${install.current || "..."}`
          : install.finished
          ? "Установка завершена"
          : statusEl.textContent;
      }
      if (!install.running && install.finished) {
        clearInterval(setupPollTimer);
        setupPollTimer = null;
        const data = await refreshSetupStatus();
        if (data.ready) {
          if (statusEl) statusEl.textContent = "Модели установлены и применены.";
        } else if (installBtn) {
          installBtn.disabled = false;
        }
      }
    } catch (e) {
      if (statusEl) statusEl.textContent = `Ошибка опроса: ${e?.message || e}`;
    }
  }, 900);
}

async function downloadOneModel(modelId) {
  const statusEl = $("setupStatus");
  try {
    if (statusEl) statusEl.textContent = `Скачивание ${modelId}...`;
    const result = await postJSON("/api/setup/install", {
      models: [modelId],
      selection: currentSelection(),
    });
    if (statusEl) statusEl.textContent = result.message || "Установка запущена";
    await pollInstallUntilDone();
  } catch (e) {
    if (statusEl) statusEl.textContent = `Ошибка: ${e?.message || e}`;
  }
}

async function onSetupInstallClick() {
  const statusEl = $("setupStatus");
  const installBtn = $("setupInstallBtn");
  try {
    if (installBtn) installBtn.disabled = true;
    const current = await refreshSetupStatus();
    if (current.ready && !(current.missing || []).length) {
      const applied = await postJSON("/api/setup/apply", {
        selection: currentSelection(),
      });
      if (statusEl) {
        statusEl.textContent = `Применено: ${applied.applied?.llm || ""}`;
      }
      setSetupSkipped(false);
      setTimeout(hideSetupOverlay, 700);
      return;
    }
    const result = await postJSON("/api/setup/install", {
      selection: currentSelection(),
    });
    if (statusEl) statusEl.textContent = result.message || "Установка запущена";
    await pollInstallUntilDone();
  } catch (e) {
    if (statusEl) statusEl.textContent = `Ошибка: ${e?.message || e}`;
    if (installBtn) installBtn.disabled = false;
  }
}

async function initSetupFlow() {
  const overlay = $("setupOverlay");
  if (!overlay) return;

  $("setupSkipBtn")?.addEventListener("click", () => {
    setSetupSkipped(true);
    hideSetupOverlay();
  });
  $("setupInstallBtn")?.addEventListener("click", onSetupInstallClick);

  try {
    const data = await refreshSetupStatus();
    // Показываем экран, если чего-то не хватает или пользователь ещё не пропускал.
    if (data.ready && setupSkipped()) {
      hideSetupOverlay();
      return;
    }
    if (data.ready) {
      // Даже если всё готово — кратко показать tier/модели один раз, если не skip.
      showSetupOverlay();
      return;
    }
    setSetupSkipped(false);
    showSetupOverlay();
  } catch (e) {
    showSetupOverlay();
    const statusEl = $("setupStatus");
    if (statusEl) statusEl.textContent = `Не удалось получить статус: ${e?.message || e}`;
  }
}

initSetupFlow();

