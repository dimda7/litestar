const ALL_ROWS = pageData('all-rows');
let currentAction = '';

function showModal(action) {
    currentAction = action;
    const modal = document.getElementById('action-modal');
    const title = document.getElementById('modal-title');
    const desc = document.getElementById('modal-desc');
    const hint = document.getElementById('sql-filename-hint');
    const btnSql = document.getElementById('btn-download-sql');

    if (action === 'counter-group') {
        title.textContent = 'Обновление id_counter_group';
        desc.textContent = 'Обновить поле id_counter_group в design_number по значению counter_group из файла';
        hint.textContent = 'Файл design_number_update_counter_group.sql';
        btnSql.style.display = 'flex';
    } else if (action === 'unit-type') {
        title.textContent = 'Обновление id_unit_type';
        desc.textContent = 'Обновить поле id_unit_type в design_number по значению unit_type из файла';
        hint.textContent = 'Файл design_number_update_unit_type.sql';
        btnSql.style.display = 'flex';
    } else if (action === 'serial-1c') {
        title.textContent = 'Обновление is_serial_1c';
        desc.textContent = 'Обновить поле is_serial_1c в design_number по значению из файла';
        hint.textContent = 'Файл design_number_update_is_serial_1c.sql';
        btnSql.style.display = 'flex';
    } else if (action === 'sync-counter-active') {
        title.textContent = 'Синхронизация counter_active';
        desc.textContent = 'Добавить/удалить записи counter_active в зависимости от id_counter_group design_number';
        hint.textContent = 'Файл sync_counter_active.sql';
        btnSql.style.display = 'flex';
    } else if (action === 'rename') {
        title.textContent = 'Изменение ТМЦ номера';
        desc.textContent = 'Переименовать design_number.number из колонки «Старый ТМЦ» в «Новый ТМЦ»';
        hint.textContent = 'Файл design_number_rename.sql';
        btnSql.style.display = 'flex';
    } else if (action === 'update') {
        title.textContent = 'Обновление ТМЦ номера';
        desc.textContent = 'Перезаписать все поля design_number и связи maint_use по данным файла';
        hint.textContent = 'Файл design_number_update.sql';
        btnSql.style.display = 'flex';
    } else if (action === 'create') {
        title.textContent = 'Создание ТМЦ номера';
        desc.textContent = 'Создать новые design_number, а существующие перезаписать по данным файла';
        hint.textContent = 'Файл design_number_create.sql';
        btnSql.style.display = 'flex';
    }
    modal.style.display = 'flex';
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

function getEndpoints() {
    if (currentAction === 'counter-group') {
        return {
            generateSql: '/design-number-parser/generate-sql-counter-group',
            execute: '/design-number-parser/update-counter-group',
            sqlFile: 'design_number_update_counter_group.sql'
        };
    } else if (currentAction === 'unit-type') {
        return {
            generateSql: '/design-number-parser/generate-sql-unit-type',
            execute: '/design-number-parser/update-unit-type',
            sqlFile: 'design_number_update_unit_type.sql'
        };
    } else if (currentAction === 'sync-counter-active') {
        return {
            generateSql: '/design-number-parser/generate-sql-counter-active',
            execute: '/design-number-parser/sync-counter-active',
            sqlFile: 'sync_counter_active.sql'
        };
    } else if (currentAction === 'rename') {
        return {
            generateSql: '/design-number-parser/generate-sql-rename-design-number',
            execute: '/design-number-parser/rename-design-number',
            sqlFile: 'design_number_rename.sql'
        };
    } else if (currentAction === 'update') {
        return {
            generateSql: '/design-number-parser/generate-sql-update-design-number',
            execute: '/design-number-parser/update-design-number',
            sqlFile: 'design_number_update.sql'
        };
    } else if (currentAction === 'create') {
        return {
            generateSql: '/design-number-parser/generate-sql-create-design-number',
            execute: '/design-number-parser/create-design-number',
            sqlFile: 'design_number_create.sql'
        };
    }
    return {
        generateSql: '/design-number-parser/generate-sql-is-serial-1c',
        execute: '/design-number-parser/update-is-serial-1c',
        sqlFile: 'design_number_update_is_serial_1c.sql'
    };
}

function downloadSQL() {
    closeModal();
    const ep = getEndpoints();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    postForm(ep.generateSql, buildFormData())
        .then(data => {
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                downloadFile([data.sql], 'text/sql', ep.sqlFile);
            }
        })
        .catch(err => alert('Ошибка запроса: ' + err));
}

function executeUpdate() {
    closeModal();
    const ep = getEndpoints();
    document.getElementById('error-box').style.display = 'none';
    document.querySelectorAll('#data-tbody tr').forEach(r => r.classList.remove('error-row'));

    let confirmMsg = 'Выполнить обновление всех строк в базе данных?';
    if (currentAction === 'sync-counter-active') {
        confirmMsg = 'Синхронизировать counter_active для всех строк? Записи будут добавлены/удалены.';
    } else if (currentAction === 'rename') {
        confirmMsg = 'Переименовать ' + ALL_ROWS.length + ' ТМЦ в базе данных?';
    } else if (currentAction === 'update') {
        confirmMsg = 'Перезаписать все поля у ' + ALL_ROWS.length + ' ТМЦ в базе данных? Старые значения сохранятся в лог.';
    } else if (currentAction === 'create') {
        confirmMsg = 'Создать или обновить ' + ALL_ROWS.length + ' ТМЦ в базе данных? Старые значения сохранятся в лог.';
    }
    if (!confirm(confirmMsg)) return;

    postForm(ep.execute, buildFormData())
        .then(data => {
            if (data.status === 'error') {
                handleErrors(data);
            } else {
                alert(data.message);
                location.reload();
            }
        })
        .catch(err => alert('Ошибка запроса: ' + err));
}
