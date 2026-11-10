function changePerPage(val) {
    const url = new URL(window.location);
    url.searchParams.set('per_page', val);
    url.searchParams.set('page', '1');
    window.location = url;
}

function showModal() {
    document.getElementById('action-modal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('action-modal').style.display = 'none';
}

document.getElementById('action-modal').addEventListener('click', function(e) {
    if (e.target === this) closeModal();
});

function showModalDesignNumber() {
    document.getElementById('action-modal-design-number').style.display = 'flex';
}

function closeModalDesignNumber() {
    document.getElementById('action-modal-design-number').style.display = 'none';
}

document.getElementById('action-modal-design-number').addEventListener('click', function(e) {
    if (e.target === this) closeModalDesignNumber();
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

function downloadSQL() {
    closeModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showCreateActivesProgress();

    postForm('/actives-parser/serial-number/generate-sql/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'actives_update_serial_number.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function executeUpdate() {
    closeModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить обновление всех строк в базе данных?')) return;

    showCreateActivesProgress();

    postForm('/actives-parser/serial-number/execute/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function downloadSQLDesignNumber() {
    closeModalDesignNumber();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showCreateActivesProgress();

    postForm('/actives-parser/design-number/generate-sql/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'actives_update_design_number.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function executeUpdateDesignNumber() {
    closeModalDesignNumber();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить обновление всех строк в базе данных?')) return;

    showCreateActivesProgress();

    postForm('/actives-parser/design-number/execute/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function showChangeOkzModal() {
    document.getElementById('change-okz-modal').style.display = 'flex';
}

function closeChangeOkzModal() {
    document.getElementById('change-okz-modal').style.display = 'none';
}

document.getElementById('change-okz-modal').addEventListener('click', function(e) {
    if (e.target === this) closeChangeOkzModal();
});

function downloadChangeOkzSQL() {
    closeChangeOkzModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showCreateActivesProgress();

    postForm('/actives-parser/change-okz/generate-sql/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'actives_change_okz.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeChangeOkz() {
    closeChangeOkzModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Изменить окз всех активов из файла в базе данных?')) return;

    showCreateActivesProgress();

    postForm('/actives-parser/change-okz/execute/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showCreateActivesModal() {
    document.getElementById('create-actives-modal').style.display = 'flex';
}

function closeCreateActivesModal() {
    document.getElementById('create-actives-modal').style.display = 'none';
}

document.getElementById('create-actives-modal').addEventListener('click', function(e) {
    if (e.target === this) closeCreateActivesModal();
});

let createActivesProgressPollTimer = null;

function showCreateActivesProgress() {
    document.getElementById('create-actives-progress-text').textContent = 'Проверка данных...';
    document.getElementById('create-actives-progress-bar-fill').style.width = '0%';
    document.getElementById('create-actives-progress-percent').textContent = '0%';
    document.getElementById('create-actives-progress-counts').textContent = '';
    document.getElementById('create-actives-progress-overlay').style.display = 'flex';
}

function updateCreateActivesProgress(state) {
    const total = state.total || 0;
    const processed = state.processed || 0;
    const pct = total > 0 ? Math.min(100, Math.round((processed / total) * 100)) : 0;
    document.getElementById('create-actives-progress-bar-fill').style.width = pct + '%';
    document.getElementById('create-actives-progress-percent').textContent = pct + '%';
    document.getElementById('create-actives-progress-counts').textContent = processed + ' из ' + total;
    document.getElementById('create-actives-progress-text').textContent =
        state.phase === 'executing' ? 'Запись в базу данных...' : 'Проверка данных...';
}

function hideCreateActivesProgress() {
    if (createActivesProgressPollTimer) {
        clearInterval(createActivesProgressPollTimer);
        createActivesProgressPollTimer = null;
    }
    document.getElementById('create-actives-progress-overlay').style.display = 'none';
}

function pollCreateActivesProgress(taskId, onFinished) {
    createActivesProgressPollTimer = setInterval(() => {
        getJson('/actives-parser/progress/' + taskId)
            .then(state => {
                if (state.status === 'running') {
                    updateCreateActivesProgress(state);
                    return;
                }
                clearInterval(createActivesProgressPollTimer);
                createActivesProgressPollTimer = null;
                onFinished(state);
            })
            .catch(err => {
                clearInterval(createActivesProgressPollTimer);
                createActivesProgressPollTimer = null;
                hideCreateActivesProgress();
                alert('Ошибка получения прогресса: ' + err);
            });
    }, 400);
}

function generateCreateActivesSQL() {
    closeCreateActivesModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showCreateActivesProgress();

    postForm('/actives-parser/create-actives/generate-sql/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'create_actives.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function showDeleteActivesModal() {
    document.getElementById('delete-actives-modal').style.display = 'flex';
}

function closeDeleteActivesModal() {
    document.getElementById('delete-actives-modal').style.display = 'none';
}

document.getElementById('delete-actives-modal').addEventListener('click', function(e) {
    if (e.target === this) closeDeleteActivesModal();
});

function generateDeleteActivesSQL() {
    closeDeleteActivesModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showCreateActivesProgress();

    postForm('/actives-parser/delete-actives/generate-sql/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'delete_actives.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function executeDeleteActives() {
    closeDeleteActivesModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('УДАЛИТЬ все активы из файла из базы данных? Действие необратимо.')) return;

    showCreateActivesProgress();

    postForm('/actives-parser/delete-actives/execute/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function showCreateNamedActivesModal() {
    document.getElementById('create-named-actives-modal').style.display = 'flex';
}

function closeCreateNamedActivesModal() {
    document.getElementById('create-named-actives-modal').style.display = 'none';
}

document.getElementById('create-named-actives-modal').addEventListener('click', function(e) {
    if (e.target === this) closeCreateNamedActivesModal();
});

function generateCreateNamedActivesSQL() {
    closeCreateNamedActivesModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showCreateActivesProgress();

    postForm('/actives-parser/create-named-actives/generate-sql/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'create_named_actives.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function executeCreateNamedActives() {
    closeCreateNamedActivesModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Создать именные активы в базе данных?')) return;

    showCreateActivesProgress();

    postForm('/actives-parser/create-named-actives/execute/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function showRecountMileageModal() {
    document.getElementById('recount-mileage-modal').style.display = 'flex';
}

function closeRecountMileageModal() {
    document.getElementById('recount-mileage-modal').style.display = 'none';
}

document.getElementById('recount-mileage-modal').addEventListener('click', function(e) {
    if (e.target === this) closeRecountMileageModal();
});

function downloadRecountMileageSQL() {
    closeRecountMileageModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showCreateActivesProgress();

    postForm('/actives-parser/recount-mileage/generate-sql/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'actives_recount_mileage.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function executeRecountMileage() {
    closeRecountMileageModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Пересчитать пробег для всех активов из файла в базе данных?')) return;

    showCreateActivesProgress();

    postForm('/actives-parser/recount-mileage/execute/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function showCreateActiveFromModelModal() {
    document.getElementById('create-active-from-model-modal').style.display = 'flex';
}

function closeCreateActiveFromModelModal() {
    document.getElementById('create-active-from-model-modal').style.display = 'none';
}

document.getElementById('create-active-from-model-modal').addEventListener('click', function(e) {
    if (e.target === this) closeCreateActiveFromModelModal();
});

function generateCreateActiveFromModelSQL() {
    closeCreateActiveFromModelModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showCreateActivesProgress();

    postForm('/actives-parser/create-active-from-model/generate-sql/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'create_active_from_model.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function executeCreateActiveFromModel() {
    closeCreateActiveFromModelModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Создать активы из модели lcn в базе данных?')) return;

    showCreateActivesProgress();

    postForm('/actives-parser/create-active-from-model/execute/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}

function executeCreateActives() {
    closeCreateActivesModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Создать активы из ТМЦ в базе данных?')) return;

    showCreateActivesProgress();

    postForm('/actives-parser/create-actives/execute/start', appendCsrfToken(new FormData()))
        .then(({ task_id }) => pollCreateActivesProgress(task_id, (data) => {
            hideCreateActivesProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                if (data.xlsx) {
                    const bytes = atob(data.xlsx);
                    const arr = new Uint8Array(bytes.length);
                    for (let i = 0; i < bytes.length; i++) arr[i] = bytes.charCodeAt(i);
                    const blob = new Blob([arr], { type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.href = url;
                    a.download = data.xlsx_filename || 'actives.xlsx';
                    a.click();
                    URL.revokeObjectURL(url);
                }
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideCreateActivesProgress(); alert('Ошибка запроса: ' + err); });
}
