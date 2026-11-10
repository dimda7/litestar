// Shared by the parser pages: the progress overlay of a background task, the
// validation error box, file downloads and the rows-per-page switch.

let progressPollTimer = null;

function showProgress() {
    document.getElementById('progress-text').textContent = 'Проверка данных...';
    document.getElementById('progress-bar-fill').style.width = '0%';
    document.getElementById('progress-percent').textContent = '0%';
    document.getElementById('progress-counts').textContent = '';
    document.getElementById('progress-overlay').style.display = 'flex';
}

function updateProgress(state) {
    const total = state.total || 0;
    const processed = state.processed || 0;
    const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
    document.getElementById('progress-bar-fill').style.width = pct + '%';
    document.getElementById('progress-percent').textContent = pct + '%';
    document.getElementById('progress-counts').textContent = processed + ' из ' + total;
    document.getElementById('progress-text').textContent =
        state.phase === 'executing' ? 'Запись в базу данных...' : 'Проверка данных...';
}

function hideProgress() {
    if (progressPollTimer) {
        clearInterval(progressPollTimer);
        progressPollTimer = null;
    }
    document.getElementById('progress-overlay').style.display = 'none';
}

// Poll a task's progress URL (e.g. '/parser/progress/' + task_id) until it settles.
function pollProgress(url, onFinished) {
    progressPollTimer = setInterval(() => {
        getJson(url)
            .then(state => {
                if (state.status === 'running') {
                    updateProgress(state);
                    return;
                }
                clearInterval(progressPollTimer);
                progressPollTimer = null;
                onFinished(state);
            })
            .catch(err => {
                clearInterval(progressPollTimer);
                progressPollTimer = null;
                hideProgress();
                alert('Ошибка получения прогресса: ' + err.message);
            });
    }, 400);
}

function handleErrors(data) {
    const box = document.getElementById('error-box');
    const list = document.getElementById('error-list');
    list.innerHTML = '';
    data.errors.forEach(e => {
        const li = document.createElement('li');
        li.textContent = 'Строка ' + e.row + ': ' + e.message;
        list.appendChild(li);
        const tr = document.querySelector('[data-row-num="' + e.row + '"]');
        if (tr) tr.classList.add('error-row');
    });
    box.style.display = 'block';
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function downloadFile(parts, type, filename) {
    const url = URL.createObjectURL(new Blob(parts, { type: type }));
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
}

function changePerPage(val) {
    const url = new URL(window.location);
    url.searchParams.set('per_page', val);
    url.searchParams.set('page', '1');
    window.location = url;
}
