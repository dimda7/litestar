const ALL_ROWS = pageData('all-rows');

function showActionModal() {
    document.getElementById('action-modal').style.display = 'flex';
}

function closeActionModal() {
    document.getElementById('action-modal').style.display = 'none';
}

document.getElementById('action-modal').addEventListener('click', function(e) {
    if (e.target === this) closeActionModal();
});

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

function generateSQL() {
    closeActionModal();
    const formData = buildFormData();
    if (!formData) return;

    document.getElementById('error-box').style.display = 'none';
    showProgress();

    postForm('/train-parser/generate-sql/start', formData)
        .then(({ task_id }) => pollProgress('/train-parser/progress/' + task_id, (data) => {
            hideProgress();
            if (data.status === 'error' || !data.sql) {
                handleErrors(data);
            } else {
                downloadFile([data.sql], 'text/sql', sqlFileName());
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
        .then(({ task_id }) => pollProgress('/train-parser/progress/' + task_id, (data) => {
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
