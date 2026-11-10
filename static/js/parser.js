const HEADERS = pageData('headers');
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
    formData.append('headers', JSON.stringify(HEADERS));
    formData.append('rows', JSON.stringify(ALL_ROWS));
    return appendCsrfToken(formData);
}

function alertNewCarPlaces(data) {
    if (data.new_car_places && data.new_car_places.length > 0) {
        alert('Будут созданы car_place:\n' + data.new_car_places.join('\n'));
    }
}

function generateSQL() {
    closeActionModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'models_insert.sql';
                a.click();
                URL.revokeObjectURL(url);
                alertNewCarPlaces(data);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeSQL(skipErrors) {
    closeActionModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!skipErrors && !confirm('Выполнить вставку всех строк в базу данных?')) return;

    showProgress();

    const formData = buildFormData();
    if (skipErrors) formData.append('skip_errors', 'true');

    postForm('/parser/execute-sql/start', formData)
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'confirm_errors') {
                handleErrors(data);
                if (data.valid_count > 0
                    && confirm('Ошибок валидации: ' + data.errors.length + '. Строк без ошибок: ' + data.valid_count
                        + '.\n\nВставить строки без ошибок (ошибочные будут пропущены)?')) {
                    executeSQL(true);
                }
            } else if (data.status === 'error') {
                handleErrors(data);
            } else {
                if (data.errors && data.errors.length > 0) {
                    handleErrors(data);
                    alert(data.message);
                } else {
                    alert(data.message);
                    location.reload();
                }
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showDeleteModal() {
    document.getElementById('delete-modal').style.display = 'flex';
}

function closeDeleteModal() {
    document.getElementById('delete-modal').style.display = 'none';
}

document.getElementById('delete-modal').addEventListener('click', function(e) {
    if (e.target === this) closeDeleteModal();
});

function deleteAllRows() {
    closeDeleteModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/delete-rows/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'models_delete.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeDelete() {
    closeDeleteModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить удаление всех строк из базы данных?')) return;

    showProgress();

    postForm('/parser/execute-delete/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showSerialNoneModal() {
    document.getElementById('serial-none-modal').style.display = 'flex';
}

function closeSerialNoneModal() {
    document.getElementById('serial-none-modal').style.display = 'none';
}

document.getElementById('serial-none-modal').addEventListener('click', function(e) {
    if (e.target === this) closeSerialNoneModal();
});

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
        getJson('/parser/progress/' + taskId)
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

function downloadSerialNoneSQL() {
    closeSerialNoneModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/serial-none/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'serial_none.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeSerialNoneUpdate() {
    closeSerialNoneModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm("Выполнить обновление serial_number='none' для всех строк в базе данных?")) return;

    showProgress();

    postForm('/parser/serial-none/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showMoveActivesModal() {
    document.getElementById('move-actives-modal').style.display = 'flex';
}

function closeMoveActivesModal() {
    document.getElementById('move-actives-modal').style.display = 'none';
}

document.getElementById('move-actives-modal').addEventListener('click', function(e) {
    if (e.target === this) closeMoveActivesModal();
});

function buildMoveActivesFormData() {
    const formData = new FormData();
    formData.append('rows', JSON.stringify(ALL_ROWS));
    formData.append('reason', document.getElementById('move-reason').value);
    formData.append('user_fullname', document.getElementById('move-user').value);
    formData.append('storage_name', document.getElementById('move-storage').value);
    formData.append('consignment_name', document.getElementById('move-consignment').value);
    formData.append('set_nocm', document.getElementById('move-set-nocm').checked ? '1' : '0');
    return appendCsrfToken(formData);
}

function downloadMoveActivesSQL() {
    closeMoveActivesModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/move-actives/generate-sql/start', buildMoveActivesFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'move_actives.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeMoveActives() {
    closeMoveActivesModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить перемещение всех активов в базе данных?')) return;

    showProgress();

    postForm('/parser/move-actives/execute-sql/start', buildMoveActivesFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showChangeLcnModal() {
    document.getElementById('change-lcn-modal').style.display = 'flex';
}

function closeChangeLcnModal() {
    document.getElementById('change-lcn-modal').style.display = 'none';
}

document.getElementById('change-lcn-modal').addEventListener('click', function(e) {
    if (e.target === this) closeChangeLcnModal();
});

function downloadChangeLcnSQL() {
    closeChangeLcnModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/change-lcn/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'change_lcn.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeChangeLcn() {
    closeChangeLcnModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить изменение lcn у всех найденных моделей в базе данных?')) return;

    showProgress();

    postForm('/parser/change-lcn/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
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
    showProgress();

    postForm('/parser/change-okz/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'change_okz.sql';
                a.click();
                URL.revokeObjectURL(url);
                alertNewCarPlaces(data);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeChangeOkz() {
    closeChangeOkzModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить изменение okz у всех найденных моделей в базе данных?')) return;

    showProgress();

    postForm('/parser/change-okz/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showChangeDesignNumberModal() {
    document.getElementById('change-design-number-modal').style.display = 'flex';
}

function closeChangeDesignNumberModal() {
    document.getElementById('change-design-number-modal').style.display = 'none';
}

document.getElementById('change-design-number-modal').addEventListener('click', function(e) {
    if (e.target === this) closeChangeDesignNumberModal();
});

function downloadChangeDesignNumberSQL() {
    closeChangeDesignNumberModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/change-design-number/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'change_model_design_number.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeChangeDesignNumber() {
    closeChangeDesignNumberModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить изменение ТМЦ у всех найденных моделей в базе данных?')) return;

    showProgress();

    postForm('/parser/change-design-number/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showChangeOkzActiveModal() {
    document.getElementById('change-okz-active-modal').style.display = 'flex';
}

function closeChangeOkzActiveModal() {
    document.getElementById('change-okz-active-modal').style.display = 'none';
}

document.getElementById('change-okz-active-modal').addEventListener('click', function(e) {
    if (e.target === this) closeChangeOkzActiveModal();
});

function downloadChangeOkzActiveSQL() {
    closeChangeOkzActiveModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/change-okz-active/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'change_okz_active.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeChangeOkzActive() {
    closeChangeOkzActiveModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить изменение okz у всех найденных активов в базе данных?')) return;

    showProgress();

    postForm('/parser/change-okz-active/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showChangeDesignNumberActiveModal() {
    document.getElementById('change-design-number-active-modal').style.display = 'flex';
}

function closeChangeDesignNumberActiveModal() {
    document.getElementById('change-design-number-active-modal').style.display = 'none';
}

document.getElementById('change-design-number-active-modal').addEventListener('click', function(e) {
    if (e.target === this) closeChangeDesignNumberActiveModal();
});

function downloadChangeDesignNumberActiveSQL() {
    closeChangeDesignNumberActiveModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/change-design-number-active/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'change_design_number_active.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeChangeDesignNumberActive() {
    closeChangeDesignNumberActiveModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить изменение ТМЦ у всех найденных активов в базе данных?')) return;

    showProgress();

    postForm('/parser/change-design-number-active/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showIsDefaultModal() {
    document.getElementById('is-default-modal').style.display = 'flex';
}

function closeIsDefaultModal() {
    document.getElementById('is-default-modal').style.display = 'none';
}

document.getElementById('is-default-modal').addEventListener('click', function(e) {
    if (e.target === this) closeIsDefaultModal();
});

function downloadIsDefaultSQL() {
    closeIsDefaultModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/is-default/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'is_default.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeIsDefaultUpdate() {
    closeIsDefaultModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить изменение is_default у всех найденных моделей в базе данных?')) return;

    showProgress();

    postForm('/parser/is-default/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function showMoveNoRelocateModal() {
    document.getElementById('move-no-relocate-modal').style.display = 'flex';
}

function closeMoveNoRelocateModal() {
    document.getElementById('move-no-relocate-modal').style.display = 'none';
}

document.getElementById('move-no-relocate-modal').addEventListener('click', function(e) {
    if (e.target === this) closeMoveNoRelocateModal();
});

function downloadMoveNoRelocateSQL() {
    closeMoveNoRelocateModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/parser/move-no-relocate/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                const blob = new Blob([data.sql], { type: 'text/sql' });
                const url = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = 'move_no_relocate.sql';
                a.click();
                URL.revokeObjectURL(url);
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeMoveNoRelocate() {
    closeMoveNoRelocateModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить перемещение всех найденных активов в базе данных?')) return;

    showProgress();

    postForm('/parser/move-no-relocate/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress(task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}
