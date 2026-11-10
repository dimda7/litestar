const ALL_ROWS = pageData('all-rows');

function showModal() {
    document.getElementById('action-modal').style.display = 'flex';
}

function closeModal() {
    document.getElementById('action-modal').style.display = 'none';
}

document.getElementById('action-modal').addEventListener('click', function(e) {
    if (e.target === this) closeModal();
});

function showHoursModal() {
    document.getElementById('hours-modal').style.display = 'flex';
}

function closeHoursModal() {
    document.getElementById('hours-modal').style.display = 'none';
}

document.getElementById('hours-modal').addEventListener('click', function(e) {
    if (e.target === this) closeHoursModal();
});

function showDeleteModal() {
    document.getElementById('delete-modal').style.display = 'flex';
}

function closeDeleteModal() {
    document.getElementById('delete-modal').style.display = 'none';
}

document.getElementById('delete-modal').addEventListener('click', function(e) {
    if (e.target === this) closeDeleteModal();
});

function showDeleteMainModal() {
    document.getElementById('delete-main-modal').style.display = 'flex';
}

function closeDeleteMainModal() {
    document.getElementById('delete-main-modal').style.display = 'none';
}

document.getElementById('delete-main-modal').addEventListener('click', function(e) {
    if (e.target === this) closeDeleteMainModal();
});

// Replaces the shared handleErrors from tasks.js: this page titles the box per
// operation and also lists plain-string errors.
function handleErrors(data, title = 'Ошибки валидации:') {
    document.getElementById('error-title').textContent = title;
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

function downloadSQL() {
    closeModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/ptoir-parser/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/ptoir-parser/progress/' + task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                downloadFile([data.sql], 'text/sql', 'update_ptoir.sql');
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeUpdate() {
    closeModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить обновление всех строк в базе данных?')) return;

    showProgress();

    postForm('/ptoir-parser/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/ptoir-parser/progress/' + task_id, (data) => {
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

function downloadHoursSQL() {
    closeHoursModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/ptoir-parser/change-hours/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/ptoir-parser/progress/' + task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                downloadFile([data.sql], 'text/sql', 'change_hours.sql');
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeHours() {
    closeHoursModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Изменить моточасы у всех строк в базе данных?')) return;

    showProgress();

    postForm('/ptoir-parser/change-hours/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/ptoir-parser/progress/' + task_id, (data) => {
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

function downloadDeleteSQL() {
    closeDeleteModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/ptoir-parser/delete/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/ptoir-parser/progress/' + task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
                return;
            }
            if (data.errors.length) handleErrors(data, 'Пропущенные строки:');
            downloadFile([data.sql], 'text/sql', 'delete_ptoir.sql');
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeDelete() {
    closeDeleteModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('УДАЛИТЬ все ПТОиР из файла из базы данных? Действие необратимо.')) return;

    showProgress();

    postForm('/ptoir-parser/delete/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/ptoir-parser/progress/' + task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
                return;
            }
            if (data.errors.length) {
                handleErrors(data, data.message + '. Пропущенные строки:');
                alert(data.message);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function downloadDeleteMainSQL() {
    closeDeleteMainModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));
    showProgress();

    postForm('/ptoir-parser/delete-main/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/ptoir-parser/progress/' + task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
                return;
            }
            if (data.errors.length) handleErrors(data, 'Пропущенные строки:');
            downloadFile([data.sql], 'text/sql', 'delete_main_ptoir.sql');
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}

function executeDeleteMain() {
    closeDeleteMainModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('УДАЛИТЬ все БПТОиР из файла вместе с их ПТОиР из базы данных? Действие необратимо.')) return;

    showProgress();

    postForm('/ptoir-parser/delete-main/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/ptoir-parser/progress/' + task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
                return;
            }
            if (data.errors.length) {
                handleErrors(data, data.message + '. Пропущенные строки:');
                alert(data.message);
            } else {
                alert(data.message);
                location.reload();
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err.message); });
}
