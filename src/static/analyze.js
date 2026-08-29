const $ = (id) => document.getElementById(id);

const docInput = $("docInput");
const uploadDrop = $("uploadDrop");
const uploadFileRow = $("uploadFileRow");
const uploadFileName = $("uploadFileName");
const uploadRemove = $("uploadRemove");
const analyzeBtn = $("analyzeBtn");
const analyzeStatus = $("analyzeStatus");
const analyzeResults = $("analyzeResults");
const analyzeDocInfo = $("analyzeDocInfo");
const analyzeSummary = $("analyzeSummary");
const templateCard = $("templateCard");
const templateSubtitle = $("templateSubtitle");
const templatePreview = $("templatePreview");
const templateDownloadBtn = $("templateDownloadBtn");
const structureReport = $("structureReport");
const structureGrid = $("structureGrid");
const analyzeToolbar = $("analyzeToolbar");
const analyzeIssues = $("analyzeIssues");
const exportBtn = $("exportBtn");

let currentFile = null;
let lastAnalysisData = null;
let currentFilter = "all";
let currentTemplateExample = "";
let currentTemplateTitle = "";

const ALLOWED_EXTENSIONS = [
  ".pdf", ".docx", ".txt",
  ".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tiff", ".tif",
];

function updateFileState() {
  if (currentFile) {
    uploadFileName.textContent = currentFile.name;
    uploadFileRow.hidden = false;
    analyzeBtn.disabled = false;
    uploadDrop.classList.add("has-file");
  } else {
    uploadFileName.textContent = "";
    uploadFileRow.hidden = true;
    analyzeBtn.disabled = true;
    uploadDrop.classList.remove("has-file");
  }
}

function isAllowedFile(file) {
  if (!file) return false;
  const lower = file.name.toLowerCase();
  return ALLOWED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

docInput.addEventListener("change", (e) => {
  const file = e.target.files?.[0];
  if (file && isAllowedFile(file)) {
    currentFile = file;
    updateFileState();
  } else if (file) {
    analyzeStatus.textContent = `Поддерживаются: ${ALLOWED_EXTENSIONS.join(", ")}.`;
    docInput.value = "";
  }
});

uploadDrop.addEventListener("dragover", (e) => {
  e.preventDefault();
  uploadDrop.classList.add("dragover");
});

uploadDrop.addEventListener("dragleave", () => {
  uploadDrop.classList.remove("dragover");
});

uploadDrop.addEventListener("drop", (e) => {
  e.preventDefault();
  uploadDrop.classList.remove("dragover");
  const file = e.dataTransfer.files?.[0];
  if (file && isAllowedFile(file)) {
    currentFile = file;
    docInput.files = e.dataTransfer.files;
    updateFileState();
  } else {
    analyzeStatus.textContent = `Поддерживаются: ${ALLOWED_EXTENSIONS.join(", ")}.`;
  }
});

uploadDrop.addEventListener("click", () => docInput.click());

uploadRemove.addEventListener("click", (e) => {
  e.stopPropagation();
  currentFile = null;
  docInput.value = "";
  updateFileState();
  analyzeResults.hidden = true;
  lastAnalysisData = null;
  currentTemplateExample = "";
  currentTemplateTitle = "";
});

function severityClass(severity) {
  const s = (severity || "").toLowerCase();
  if (s === "критично") return "severity-critical";
  if (s === "важно") return "severity-important";
  return "severity-recommendation";
}

function severityLabel(severity) {
  const s = (severity || "").toLowerCase();
  if (s === "критично") return "Критично";
  if (s === "важно") return "Важно";
  return "Рекомендация";
}

function escapeHtml(value) {
  return String(value || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

function renderDocInfo(data) {
  const result = data.result || {};
  const metadata = result.metadata || {};
  const parts = [];

  if (data.doc_type_label) {
    parts.push(`<p><strong>Тип документа:</strong> ${escapeHtml(data.doc_type_label)} ${
      data.doc_type_confidence ? `(уверенность ${Math.round(data.doc_type_confidence * 100)}%)` : ""
    }</p>`);
  }

  if (metadata.parties?.length) {
    parts.push(`<p><strong>Стороны:</strong> ${escapeHtml(metadata.parties.join("; "))}</p>`);
  }
  if (metadata.dates?.length) {
    parts.push(`<p><strong>Даты:</strong> ${escapeHtml(metadata.dates.join("; "))}</p>`);
  }
  if (metadata.amounts?.length) {
    parts.push(`<p><strong>Суммы:</strong> ${escapeHtml(metadata.amounts.join("; "))}</p>`);
  }
  if (metadata.char_count) {
    parts.push(`<p><strong>Символов:</strong> ${metadata.char_count}</p>`);
  }

  analyzeDocInfo.innerHTML = parts.length
    ? `<div class="doc-info-card">${parts.join("")}</div>`
    : "";
}

function normLinkHtml(norm) {
  const match = (norm || "").match(/([^,]+),\s*ст\.?\s*(\S+)/i);
  if (!match) return escapeHtml(norm);
  const code = match[1].trim();
  const number = match[2].trim();
  return `<a href="/source?code=${encodeURIComponent(code)}&number=${encodeURIComponent(number)}" target="_blank" rel="noopener">${escapeHtml(norm)}</a>`;
}

function renderIssueCard(it) {
  const sourceLabel = it.type === "missing_section"
    ? "Отсутствует раздел"
    : it.type === "content_issue"
    ? "Содержание раздела"
    : "Проверка";

  return `
    <article class="issue-card ${severityClass(it.severity)}" data-severity="${severityClass(it.severity).replace("severity-", "")}">
      <div class="issue-header">
        <span class="issue-badge">${severityLabel(it.severity)}</span>
        <span class="issue-check">${escapeHtml(it.section_name || sourceLabel)}</span>
        <span class="issue-norm">${normLinkHtml(it.norm)}</span>
      </div>
      ${it.quote ? `<blockquote class="issue-quote">${escapeHtml(it.quote)}</blockquote>` : ""}
      <p class="issue-text">${escapeHtml(it.issue)}</p>
      ${it.norm_quote ? `
      <div class="issue-norm-quote">
        <p class="issue-norm-quote-label">Норма закона</p>
        <p class="issue-norm-quote-text">${escapeHtml(it.norm_quote)}</p>
      </div>` : ""}
      <div class="issue-suggestion">
        <p class="issue-suggestion-label">Предложение по правке</p>
        <p class="issue-suggestion-text">${escapeHtml(it.suggestion)}</p>
      </div>
      ${it.validation_note ? `<p class="issue-validation">⚠ ${escapeHtml(it.validation_note)}</p>` : ""}
    </article>
  `;
}

function renderTemplateCard(template) {
  if (!template) {
    templateCard.hidden = true;
    return;
  }

  currentTemplateTitle = template.title || "";
  currentTemplateExample = template.example_text || "";
  templateSubtitle.textContent = template.title || "";

  // Показываем первые 20 строк образца.
  const previewLines = currentTemplateExample.split("\n").slice(0, 20).join("\n");
  templatePreview.innerHTML = `<pre>${escapeHtml(previewLines)}${currentTemplateExample.split("\n").length > 20 ? "\n..." : ""}</pre>`;
  templateCard.hidden = false;
}

function renderStructureReport(structure) {
  if (!structure) {
    structureReport.hidden = true;
    return;
  }

  const found = structure.found_sections || [];
  const missing = structure.missing_sections || [];

  const cells = [];

  found.forEach((sec) => {
    cells.push(`
      <div class="structure-cell structure-found">
        <span class="structure-icon">✓</span>
        <span class="structure-name">${escapeHtml(sec.name)}</span>
        <span class="structure-status">найден</span>
      </div>
    `);
  });

  missing.forEach((sec) => {
    cells.push(`
      <div class="structure-cell structure-missing ${sec.required ? "required" : "optional"}">
        <span class="structure-icon">${sec.required ? "✕" : "?"}</span>
        <span class="structure-name">${escapeHtml(sec.name)}</span>
        <span class="structure-status">${sec.required ? "обязательный — отсутствует" : "рекомендуемый — отсутствует"}</span>
      </div>
    `);
  });

  structureGrid.innerHTML = cells.join("");
  structureReport.hidden = false;
}

function applyFilter() {
  if (!lastAnalysisData) return;
  const issues = (lastAnalysisData.result || {}).issues || [];
  const filtered =
    currentFilter === "all"
      ? issues
      : issues.filter((it) => (it.severity || "").toLowerCase() === currentFilter);

  analyzeIssues.innerHTML = filtered.length
    ? filtered.map(renderIssueCard).join("")
    : `<div class="analyze-empty"><p>Нет замечаний выбранной категории.</p></div>`;
}

function renderResults(data) {
  lastAnalysisData = data;
  const result = data.result || {};
  const summary = result.summary || {};
  const issues = result.issues || [];
  const template = result.template || {};
  const structure = result.structure || {};

  renderDocInfo(data);
  renderTemplateCard(template);
  renderStructureReport(structure);

  analyzeSummary.innerHTML = `
    <div class="summary-card">
      <p class="summary-title">Результат анализа</p>
      <div class="summary-cells">
        <div class="summary-cell critical">
          <span class="summary-num">${summary.critical || 0}</span>
          <span class="summary-label">Критично</span>
        </div>
        <div class="summary-cell important">
          <span class="summary-num">${summary.important || 0}</span>
          <span class="summary-label">Важно</span>
        </div>
        <div class="summary-cell recommendation">
          <span class="summary-num">${summary.recommendation || 0}</span>
          <span class="summary-label">Рекомендации</span>
        </div>
      </div>
      <p class="summary-total">Всего замечаний: <strong>${summary.total || issues.length}</strong></p>
    </div>
  `;

  analyzeToolbar.hidden = false;
  applyFilter();
  analyzeResults.hidden = false;
  analyzeResults.scrollIntoView({ behavior: "smooth", block: "start" });
}

analyzeBtn.addEventListener("click", async () => {
  if (!currentFile) return;

  analyzeBtn.disabled = true;
  analyzeStatus.textContent = "Анализирую документ, это может занять 10–40 секунд...";
  analyzeResults.hidden = true;
  analyzeToolbar.hidden = true;

  const form = new FormData();
  form.append("document", currentFile, currentFile.name);

  try {
    const res = await fetch("/api/analyze-document", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
    analyzeStatus.textContent = `Анализ завершён: ${data.filename}`;
    renderResults(data);
  } catch (e) {
    analyzeStatus.textContent = `Ошибка анализа: ${e?.message || e}`;
  } finally {
    analyzeBtn.disabled = false;
  }
});

analyzeToolbar?.addEventListener("click", (e) => {
  const btn = e.target.closest(".filter-btn");
  if (!btn) return;
  analyzeToolbar.querySelectorAll(".filter-btn").forEach((b) => b.classList.remove("active"));
  btn.classList.add("active");
  currentFilter = btn.dataset.filter;
  applyFilter();
});

templateDownloadBtn?.addEventListener("click", () => {
  if (!currentTemplateExample) return;
  const blob = new Blob([currentTemplateExample], { type: "text/plain;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `obrazets-${currentTemplateTitle || "document"}.txt`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

function buildReportMarkdown(data) {
  const result = data.result || {};
  const summary = result.summary || {};
  const issues = result.issues || [];
  const template = result.template || {};
  const structure = result.structure || {};
  const lines = [
    `# Отчёт по проверке документа`,
    ``,
    `**Файл:** ${data.filename || "—"}`,
    `**Тип документа:** ${data.doc_type_label || "—"}`,
    `**Уверенность:** ${data.doc_type_confidence ? Math.round(data.doc_type_confidence * 100) + "%" : "—"}`,
    `**Шаблон:** ${template.title || "—"}`,
    ``,
    `## Сводка`,
    ``,
    `- Критично: ${summary.critical || 0}`,
    `- Важно: ${summary.important || 0}`,
    `- Рекомендации: ${summary.recommendation || 0}`,
    `- Всего: ${summary.total || issues.length}`,
    ``,
    `## Структура документа`,
    ``,
    `Найдено разделов: ${structure.found_count || 0} из ${structure.total_sections || 0}`,
    `Отсутствует обязательных: ${structure.required_missing || 0}`,
    ``,
  ];

  (structure.found_sections || []).forEach((sec) => {
    lines.push(`- ✓ ${sec.name} — найден`);
  });
  (structure.missing_sections || []).forEach((sec) => {
    lines.push(`- ✕ ${sec.name} — ${sec.required ? "обязательный" : "рекомендуемый"} раздел отсутствует`);
  });

  lines.push("", `## Замечания`, "");

  if (!issues.length) {
    lines.push("Замечаний не выявлено.");
  } else {
    issues.forEach((it, idx) => {
      lines.push(`### ${idx + 1}. ${severityLabel(it.severity)} — ${it.section_name || "—"}`);
      lines.push("");
      lines.push(`**Тип:** ${it.type === "missing_section" ? "отсутствует раздел" : "содержание раздела"}`);
      lines.push(`**Цитата из документа:** ${it.quote || "—"}`);
      lines.push(`**Проблема:** ${it.issue || "—"}`);
      lines.push(`**Норма:** ${it.norm_quote || it.norm || "—"}`);
      lines.push(`**Предложение:** ${it.suggestion || "—"}`);
      lines.push("");
    });
  }

  return lines.join("\n");
}

exportBtn?.addEventListener("click", () => {
  if (!lastAnalysisData) return;
  const md = buildReportMarkdown(lastAnalysisData);
  const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `report-${lastAnalysisData.filename || "document"}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

initSetupFlow?.();
