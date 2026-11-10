const TRAINS_BY_NAME = new Map(pageData('trains').map(t => [t.name, t.id]));

const EMPTY_MESSAGE = 'У поезда нет записей о пробеге с заполненным временем';

let selectedTrainId = null;
let selectedRowId = null;

function showError(message) {
    const box = document.getElementById('error-box');
    box.textContent = message;
    box.style.display = 'block';
    document.getElementById('success-box').style.display = 'none';
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function showSuccess(message) {
    const box = document.getElementById('success-box');
    box.textContent = message;
    box.style.display = 'block';
    document.getElementById('error-box').style.display = 'none';
    window.scrollTo({ top: 0, behavior: 'smooth' });
}

function clearMessages() {
    document.getElementById('error-box').style.display = 'none';
    document.getElementById('success-box').style.display = 'none';
}

function clearSelection() {
    selectedRowId = null;
    document.getElementById('delete-btn').disabled = true;
    document.querySelectorAll('#mileage-tbody tr').forEach(tr => tr.classList.remove('selected-row'));
}

function onTrainInput() {
    selectedTrainId = TRAINS_BY_NAME.get(document.getElementById('train-name').value.trim()) ?? null;
    document.getElementById('load-btn').disabled = selectedTrainId === null;
    document.getElementById('mileage-card').style.display = 'none';
    clearSelection();
}

function selectRow(tr, rowId) {
    document.querySelectorAll('#mileage-tbody tr').forEach(r => r.classList.remove('selected-row'));
    tr.classList.add('selected-row');
    tr.querySelector('input[type="radio"]').checked = true;
    selectedRowId = rowId;
    document.getElementById('delete-btn').disabled = false;
}

function renderRows(rows, announceEmpty) {
    const tbody = document.getElementById('mileage-tbody');
    tbody.innerHTML = '';
    document.getElementById('mileage-card').style.display = 'block';

    if (!rows.length) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 6;
        td.style.cssText = 'color: var(--text-hint); text-align: center; padding: 20px;';
        td.textContent = EMPTY_MESSAGE;
        tr.appendChild(td);
        tbody.appendChild(tr);
        if (announceEmpty) showError(EMPTY_MESSAGE);
        return;
    }

    rows.forEach(row => {
        const tr = document.createElement('tr');
        tr.style.cursor = 'pointer';
        tr.onclick = () => selectRow(tr, row.id);

        const pick = document.createElement('td');
        const radio = document.createElement('input');
        radio.type = 'radio';
        radio.name = 'mileage-row';
        radio.style.accentColor = 'var(--accent)';
        pick.appendChild(radio);
        tr.appendChild(pick);

        [row.id, row.date, row.date_average, row.milage, row.mileage_average].forEach(value => {
            const td = document.createElement('td');
            td.textContent = value === null ? '—' : value;
            tr.appendChild(td);
        });

        tbody.appendChild(tr);
    });
}

async function refreshMileage(keepMessage) {
    if (!keepMessage) clearMessages();
    clearSelection();
    try {
        const data = await getJson('/mileage-correction/mileage/' + selectedTrainId);
        renderRows(data.rows, !keepMessage);
    } catch (err) {
        showError('Ошибка загрузки: ' + err.message);
    }
}

function loadMileage() {
    return refreshMileage(false);
}

function buildFormData() {
    const formData = new FormData();
    formData.append('train_id', selectedTrainId);
    formData.append('mileage_row_id', selectedRowId);
    return appendCsrfToken(formData);
}

async function openModal() {
    clearMessages();
    try {
        const data = await postForm('/mileage-correction/preview', buildFormData());
        if (data.status === 'error') {
            showError(data.message);
            return;
        }
        document.getElementById('modal-summary').innerHTML =
            'Будет удалено записей mileage_train: <strong>' + data.delete_count + '</strong>' +
            ' (после ' + data.kept_date + ')<br>' +
            'Счётчик будет установлен: <strong>' + data.counter_value + '</strong> на ' + data.counter_date;
        document.getElementById('action-modal').style.display = 'flex';
    } catch (err) {
        showError('Ошибка запроса: ' + err.message);
    }
}

function closeModal() {
    document.getElementById('action-modal').style.display = 'none';
}

document.getElementById('action-modal').addEventListener('click', function (e) {
    if (e.target === this) closeModal();
});

async function downloadSQL() {
    closeModal();
    try {
        const data = await postForm('/mileage-correction/generate-sql', buildFormData());
        if (data.status === 'error') {
            showError(data.message);
            return;
        }
        downloadFile([data.sql], 'text/sql', 'mileage_correction.sql');
    } catch (err) {
        showError('Ошибка запроса: ' + err.message);
    }
}

async function executeCorrection() {
    closeModal();
    if (!confirm('Удалить записи о пробеге и откатить счётчик? Действие необратимо.')) return;

    try {
        const data = await postForm('/mileage-correction/execute', buildFormData());
        if (data.status === 'error') {
            showError(data.message);
            return;
        }
        showSuccess(data.message);
        await refreshMileage(true);
    } catch (err) {
        showError('Ошибка запроса: ' + err.message);
    }
}
