function loadSqlFile(input) {
    const file = input.files[0];
    if (!file) return;
    document.getElementById('sql-file-name').textContent = file.name;
    const reader = new FileReader();
    reader.onload = (e) => {
        document.getElementById('sql-textarea').value = e.target.result;
    };
    reader.readAsText(file, 'utf-8');
}

function renderResults(data) {
    const box = document.getElementById('sql-results');
    box.innerHTML = '';

    if (data.status === 'error') {
        const err = document.createElement('div');
        err.className = 'alert alert-error';
        err.style.marginBottom = '16px';
        const prefix = (typeof data.failed_index === 'number') ? ('Ошибка в запросе №' + (data.failed_index + 1) + ': ') : 'Ошибка: ';
        const strong = document.createElement('strong');
        strong.textContent = prefix;
        err.appendChild(strong);
        err.appendChild(document.createTextNode(data.message || ''));
        box.appendChild(err);
    }

    (data.results || []).forEach((r, i) => {
        const card = document.createElement('div');
        card.className = 'card';
        card.style.marginBottom = '16px';

        const title = document.createElement('div');
        title.style.cssText = 'font-family: monospace; font-size: 12px; color: var(--text-secondary); margin-bottom: 10px; white-space: pre-wrap; word-break: break-all;';
        title.textContent = '№' + (i + 1) + ': ' + r.statement;
        card.appendChild(title);

        if (r.columns) {
            if (r.rows.length === 0) {
                const empty = document.createElement('div');
                empty.style.cssText = 'font-size: 13px; color: var(--text-hint);';
                empty.textContent = 'Строк не найдено';
                card.appendChild(empty);
            } else {
                const wrap = document.createElement('div');
                wrap.className = 'table-wrapper';
                const table = document.createElement('table');

                const thead = document.createElement('thead');
                const htr = document.createElement('tr');
                r.columns.forEach(c => {
                    const th = document.createElement('th');
                    th.textContent = c;
                    htr.appendChild(th);
                });
                thead.appendChild(htr);
                table.appendChild(thead);

                const tbody = document.createElement('tbody');
                r.rows.forEach(row => {
                    const tr = document.createElement('tr');
                    row.forEach(v => {
                        const td = document.createElement('td');
                        td.textContent = (v === null) ? '—' : v;
                        tr.appendChild(td);
                    });
                    tbody.appendChild(tr);
                });
                table.appendChild(tbody);
                wrap.appendChild(table);
                card.appendChild(wrap);

                const count = document.createElement('div');
                count.style.cssText = 'font-size: 12px; color: var(--text-hint); margin-top: 8px;';
                count.textContent = 'Строк: ' + r.rows.length;
                card.appendChild(count);
            }
        } else {
            const msg = document.createElement('div');
            msg.style.cssText = 'font-size: 13px; color: #2e7d32;';
            msg.textContent = 'Выполнено. Затронуто строк: ' + r.rowcount;
            card.appendChild(msg);
        }

        box.appendChild(card);
    });
}

let currentExecutionId = null;
let timerStart = null;
let timerInterval = null;

function genExecutionId() {
    return 'q' + Date.now().toString(36) + Math.random().toString(36).slice(2);
}

function formatElapsed(ms) {
    return (ms / 1000).toFixed(2) + ' с';
}

function startTimer() {
    timerStart = performance.now();
    const timerEl = document.getElementById('sql-timer');
    timerEl.textContent = 'Выполняется: 0.00 с';
    timerInterval = setInterval(() => {
        timerEl.textContent = 'Выполняется: ' + formatElapsed(performance.now() - timerStart);
    }, 100);
}

function stopTimer(label) {
    clearInterval(timerInterval);
    const elapsed = performance.now() - timerStart;
    document.getElementById('sql-timer').textContent = label + ': ' + formatElapsed(elapsed);
}

function setRunningState(running) {
    const runBtn = document.getElementById('run-btn');
    const cancelBtn = document.getElementById('cancel-btn');
    runBtn.style.opacity = running ? '0.4' : '1';
    runBtn.style.pointerEvents = running ? 'none' : 'auto';
    cancelBtn.disabled = !running;
    cancelBtn.style.opacity = running ? '1' : '0.35';
    cancelBtn.style.cursor = running ? 'pointer' : 'not-allowed';
}

function executeSql() {
    const sql = document.getElementById('sql-textarea').value.trim();
    if (!sql) return;

    currentExecutionId = genExecutionId();
    document.getElementById('cancel-status').textContent = '';

    const formData = new FormData();
    formData.append('sql', sql);
    formData.append('execution_id', currentExecutionId);
    appendCsrfToken(formData);

    setRunningState(true);
    startTimer();

    postForm('/sql-console/execute', formData)
        .then(data => {
            let label = 'Выполнено за';
            if (data.status === 'error') {
                label = (data.message || '').indexOf('прерван') !== -1 ? 'Прервано через' : 'Завершено с ошибкой через';
            }
            stopTimer(label);
            renderResults(data);
        })
        .catch(err => {
            stopTimer('Прервано через');
            renderResults({ status: 'error', message: 'Ошибка запроса: ' + err });
        })
        .finally(() => {
            setRunningState(false);
            currentExecutionId = null;
        });
}

function cancelSql() {
    if (!currentExecutionId) return;

    const formData = new FormData();
    formData.append('execution_id', currentExecutionId);
    appendCsrfToken(formData);

    const cancelBtn = document.getElementById('cancel-btn');
    const statusEl = document.getElementById('cancel-status');
    cancelBtn.disabled = true;
    cancelBtn.style.opacity = '0.35';
    cancelBtn.style.cursor = 'not-allowed';
    statusEl.textContent = 'Отправка сигнала отмены...';

    // Cancelling is a short administrative operation (a pg_cancel_backend signal)
    // rather than the query itself, so it has its own timeout: if the server does
    // not answer within a reasonable time, the UI must not hang silently.
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 12000);

    fetch('/sql-console/cancel', { method: 'POST', body: formData, signal: controller.signal })
        .then(readJsonResponse)
        .then(data => {
            statusEl.textContent = data.message || (data.status === 'ok' ? 'Сигнал отмены отправлен' : 'Не удалось отменить запрос');
        })
        .catch(err => {
            const reason = (err && err.name === 'AbortError') ? 'таймаут запроса на отмену' : err;
            statusEl.textContent = 'Не удалось отправить сигнал отмены: ' + reason;
        })
        .finally(() => {
            clearTimeout(timeoutId);
            if (currentExecutionId) {
                cancelBtn.disabled = false;
                cancelBtn.style.opacity = '1';
                cancelBtn.style.cursor = 'pointer';
            }
        });
}

document.getElementById('sql-textarea').addEventListener('keydown', function(e) {
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
        executeSql();
    }
});
