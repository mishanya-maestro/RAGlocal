(function () {
  'use strict';

  const $ = (sel, el = document) => el.querySelector(sel);
  const $$ = (sel, el = document) => Array.from(el.querySelectorAll(sel));

  const corporaList = $('#corporaList');
  const corporaEmpty = $('#corporaEmpty');
  const corporaStatus = $('#corporaStatus');
  const createForm = $('#createCorpusForm');
  const nameInput = $('#corpusName');
  const descInput = $('#corpusDescription');

  let activeCorpusId = null;

  function setStatus(msg, type = 'info') {
    corporaStatus.textContent = msg;
    corporaStatus.className = 'status-line ' + type;
  }

  function clearStatus() {
    corporaStatus.textContent = '';
    corporaStatus.className = 'status-line';
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\"/g, '&quot;');
  }

  async function api(path, opts = {}) {
    const res = await fetch(path, opts);
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || `HTTP ${res.status}`);
    }
    return res.json();
  }

  async function loadCorpora() {
    try {
      const data = await api('/api/corpora');
      activeCorpusId = data.active_corpus_id;
      renderCorpora(data.corpora || [], data.active_corpus_id);
    } catch (e) {
      setStatus('Ошибка загрузки корпусов: ' + e.message, 'error');
    }
  }

  function renderCorpora(corpora, activeId) {
    corporaList.innerHTML = '';
    if (!corpora.length) {
      corporaList.appendChild(corporaEmpty);
      corporaEmpty.hidden = false;
      return;
    }
    corporaEmpty.hidden = true;

    for (const c of corpora) {
      const isActive = c.id === activeId;
      const card = document.createElement('div');
      card.className = 'corpus-card' + (isActive ? ' active' : '');
      card.dataset.id = c.id;

      const docs = c.documents || [];
      const indexed = c.indexed_count || 0;

      card.innerHTML = `
        <div class="corpus-header">
          <div>
            <div class="corpus-title">${escapeHtml(c.name)} ${isActive ? '<span class="corpus-active-badge">Активный</span>' : ''}</div>
            <div class="corpus-meta">${escapeHtml(c.description || '')}</div>
            <div class="corpus-meta">Документов: ${docs.length}, чанков: ${indexed}</div>
          </div>
          <div class="corpus-actions">
            <button type="button" class="corpus-btn small ${isActive ? 'disabled' : ''}" data-action="activate" ${isActive ? 'disabled' : ''}>${isActive ? 'Выбран' : 'Выбрать'}</button>
            <button type="button" class="corpus-btn small danger" data-action="delete">Удалить</button>
          </div>
        </div>
        <div class="corpus-body">
          <div class="corpus-upload">
            <input type="file" id="file-${c.id}" class="corpus-file-input" multiple
              accept=".pdf,.docx,.txt,.png,.jpg,.jpeg,.webp,.bmp,.gif,.tiff,.tif" hidden />
            <label for="file-${c.id}" class="corpus-drop">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="17 8 12 3 7 8"></polyline>
                <line x1="12" y1="3" x2="12" y2="15"></line>
              </svg>
              <span>Загрузить документы</span>
            </label>
          </div>
          <div class="corpus-docs">
            ${docs.length ? '' : '<div class="corpus-docs-empty">Документов нет</div>'}
            <ul class="corpus-docs-list">
              ${docs.map(d => `
                <li class="corpus-doc">
                  <span class="corpus-doc-name">${escapeHtml(d.filename)}</span>
                  <span class="corpus-doc-meta">${(d.char_count || 0).toLocaleString()} знаков</span>
                  <button type="button" class="corpus-doc-delete" data-doc-id="${escapeHtml(d.doc_id)}" title="Удалить">×</button>
                </li>
              `).join('')}
            </ul>
          </div>
        </div>
      `;

      corporaList.appendChild(card);
    }

    bindCorpusEvents();
  }

  function bindCorpusEvents() {
    $$('[data-action="activate"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const id = btn.closest('.corpus-card').dataset.id;
        try {
          setStatus('Активируем корпус...');
          await api(`/api/corpora/${id}/set-active`, { method: 'POST' });
          activeCorpusId = id;
          await loadCorpora();
          setStatus('Корпус активирован. Теперь вопросы будут искать по нему.', 'success');
        } catch (e) {
          setStatus('Ошибка активации: ' + e.message, 'error');
        }
      });
    });

    $$('[data-action="delete"]').forEach(btn => {
      btn.addEventListener('click', async () => {
        const card = btn.closest('.corpus-card');
        const id = card.dataset.id;
        const name = card.querySelector('.corpus-title').childNodes[0].textContent.trim();
        if (!confirm(`Удалить корпус «${name}» и все его документы?`)) return;
        try {
          setStatus('Удаляем корпус...');
          await api(`/api/corpora/${id}`, { method: 'DELETE' });
          await loadCorpora();
          setStatus('Корпус удалён.', 'success');
        } catch (e) {
          setStatus('Ошибка удаления: ' + e.message, 'error');
        }
      });
    });

    $$('.corpus-file-input').forEach(input => {
      input.addEventListener('change', async () => {
        const id = input.closest('.corpus-card').dataset.id;
        await uploadDocuments(id, input.files);
        input.value = '';
      });
    });

    $$('.corpus-doc-delete').forEach(btn => {
      btn.addEventListener('click', async () => {
        const card = btn.closest('.corpus-card');
        const corpusId = card.dataset.id;
        const docId = btn.dataset.docId;
        if (!confirm('Удалить документ из корпуса?')) return;
        try {
          setStatus('Удаляем документ...');
          await api(`/api/corpora/${corpusId}/documents/${docId}`, { method: 'DELETE' });
          await loadCorpora();
          setStatus('Документ удалён.', 'success');
        } catch (e) {
          setStatus('Ошибка удаления документа: ' + e.message, 'error');
        }
      });
    });

    $$('.corpus-drop').forEach(drop => {
      drop.addEventListener('dragover', e => {
        e.preventDefault();
        drop.classList.add('dragover');
      });
      drop.addEventListener('dragleave', () => drop.classList.remove('dragover'));
      drop.addEventListener('drop', async e => {
        e.preventDefault();
        drop.classList.remove('dragover');
        const id = drop.closest('.corpus-card').dataset.id;
        await uploadDocuments(id, e.dataTransfer.files);
      });
    });
  }

  async function uploadDocuments(corpusId, files) {
    if (!files || !files.length) return;
    const form = new FormData();
    let count = 0;
    for (const f of files) {
      form.append('documents', f);
      count++;
    }
    try {
      setStatus(`Загружаем ${count} документ(ов)...`);
      const data = await api(`/api/corpora/${corpusId}/documents`, { method: 'POST', body: form });
      await loadCorpora();
      const errText = data.errors && data.errors.length ? ` (ошибок: ${data.errors.length})` : '';
      setStatus(`Загружено ${data.added.length} документов${errText}. Индексация завершена.`, 'success');
      if (data.errors && data.errors.length) {
        console.warn('Upload errors:', data.errors);
      }
    } catch (e) {
      setStatus('Ошибка загрузки: ' + e.message, 'error');
    }
  }

  createForm.addEventListener('submit', async e => {
    e.preventDefault();
    const name = nameInput.value.trim();
    const description = descInput.value.trim();
    if (!name) return;
    try {
      setStatus('Создаём корпус...');
      await api('/api/corpora', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name, description }),
      });
      nameInput.value = '';
      descInput.value = '';
      await loadCorpora();
      setStatus('Корпус создан. Загрузите документы и выберите его активным.', 'success');
    } catch (e) {
      setStatus('Ошибка создания корпуса: ' + e.message, 'error');
    }
  });

  loadCorpora();
})();
