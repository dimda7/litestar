const ALL_ROWS = pageData('all-rows');

function changePerPage(val) {
    const url = new URL(window.location);
    url.searchParams.set('per_page', val);
    url.searchParams.set('page', '1');
    window.location = url;
}

const OPERATIONS = {
    create: {
        title: 'Создать техкарты',
        description: 'Создать техкарты со статусом и классификаторами (techcard, techcard_status, techcard_to_specification)',
        url: '/techcard-parser',
        filename: 'create_techcards.sql',
        confirm: 'Создать техкарты в базе данных?',
    },
    operations: {
        title: 'Наполнить операциями',
        description: 'Добавить операции в существующие техкарты (techcard_work_operation)',
        url: '/techcard-parser/operations',
        filename: 'add_techcard_operations.sql',
        confirm: 'Добавить операции в техкарты в базе данных?',
    },
};
let currentOperation = OPERATIONS.create;

function showModal(name) {
    currentOperation = OPERATIONS[name];
    document.getElementById('modal-title').textContent = currentOperation.title;
    document.getElementById('modal-description').textContent = currentOperation.description;
    document.getElementById('modal-filename').textContent = currentOperation.filename;
    document.getElementById('action-modal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('action-modal').style.display = 'none';
}

document.getElementById('action-modal').addEventListener('click', function(e) {
    if (e.target === this) closeModal();
});

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

function buildFormData() {
    const formData = new FormData();
    formData.append('rows', JSON.stringify(ALL_ROWS));
    return appendCsrfToken(formData);
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
        getJson('/techcard-parser/progress/' + taskId)
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

function downloadSQL() {
    closeModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm(currentOperation.url + '/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = currentOperation.filename;
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err); });
}

function executeUpdate() {
    closeModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm(currentOperation.confirm)) return;

    showProgress();

    postForm(currentOperation.url + '/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err); });
}
