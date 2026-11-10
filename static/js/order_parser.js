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

    postForm('/order-parser/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/order-parser/progress/' + task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                downloadFile([data.sql], 'text/sql', 'assign_parent_order.sql');
            }
        }))
        .catch(err => { hideProgress(); alert('Ошибка запроса: ' + err); });
}

function executeUpdate() {
    closeModal();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    if (!confirm('Выполнить обновление всех строк в базе данных?')) return;

    showProgress();

    postForm('/order-parser/execute-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/order-parser/progress/' + task_id, (data) => {
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
