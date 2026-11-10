const ALL_ROWS = pageData('all-rows');

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

    postForm(currentOperation.url + '/generate-sql/start', buildFormData())
        .then(({ task_id }) => pollProgress('/techcard-parser/progress/' + task_id, (data) => {
            hideProgress();
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                downloadFile([data.sql], 'text/sql', currentOperation.filename);
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
        .then(({ task_id }) => pollProgress('/techcard-parser/progress/' + task_id, (data) => {
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
