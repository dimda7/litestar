const ALL_ROWS = pageData('all-rows');

function changePerPage(val) {
    const url = new URL(window.location);
    url.searchParams.set('per_page', val);
    url.searchParams.set('page', '1');
    window.location = url;
}

function showActionModal() {
    document.getElementById('action-modal').style.display = 'flex';
}

function closeActionModal() {
    document.getElementById('action-modal').style.display = 'none';
}

document.getElementById('action-modal').addEventListener('click', function(e) {
    if (e.target === this) closeActionModal();
});

function handleErrors(data) {
    const box = document.getElementById('error-box');
    const list = document.getElementById('error-list');
    list.innerHTML = '';
    const errors = data.errors || [];
    errors.forEach(e => {
        const li = document.createElement('li');
        if (typeof e === 'string') {
            li.textContent = e;
        } else {
            li.textContent = 'Строка ' + e.row + ': ' + e.message;
        }
        list.appendChild(li);
    });
    box.style.display = 'block';
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function buildFormData() {
    const trainName = document.getElementById('train-name').value.trim();
    const trainTypeName = document.getElementById('train-type-name').value;
    if (!trainName) {
        alert('Введите название поезда');
        return null;
    }
    const formData = new FormData();
    formData.append('headers', JSON.stringify([]));
    formData.append('rows', JSON.stringify(ALL_ROWS));
    formData.append('train_name', trainName);
    formData.append('train_type_name', trainTypeName);
    return appendCsrfToken(formData);
}

function sqlFileName() {
    const trainName = document.getElementById('train-name').value.trim();
    const safeName = trainName.replace(/[^\p{L}\p{N}_-]+/gu, '_');
    return 'train_insert_' + safeName + '.sql';
}

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

function pollProgress(taskId, onFinished) {
    progressPollTimer = setInterval(() => {
        getJson('/train-parser/progress/' + taskId)
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
                alert('Ошибка получения прогресса: ' + err);
            });
    }, 400);
}

function generateSQL() {
    closeActionModal();
    const formData = buildFormData();
    if (!formData) return;

    document.getElementById('error-box').style.display = 'none';
    showProgress();

    postForm('/train-parser/generate-sql/start', formData)
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = sqlFileName();
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err); });
}

function executeInsert() {
    closeActionModal();
    const formData = buildFormData();
    if (!formData) return;

    if (!confirm('Вставить все данные поезда в базу данных?')) return;

    document.getElementById('error-box').style.display = 'none';
    showProgress();

    postForm('/train-parser/execute/start', formData)
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error' || data.status === 'partial') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err); });
}
