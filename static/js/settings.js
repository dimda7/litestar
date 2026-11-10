const DB_PROFILES = pageData('db-profiles');

function setTheme(theme) {
    document.documentElement.setAttribute('data-theme', theme);
    localStorage.setItem('theme', theme);
    updateThemeButtons(theme);
}

function updateThemeButtons(theme) {
    document.getElementById('btn-theme-light').classList.toggle('active', theme === 'light');
    document.getElementById('btn-theme-dark').classList.toggle('active', theme === 'dark');
}

(function() {
    const saved = localStorage.getItem('theme') || 'light';
    document.documentElement.setAttribute('data-theme', saved);
    updateThemeButtons(saved);
})();

function getSelectedProfile() {
    const checked = document.querySelector('input[name="db_profile"]:checked');
    return checked ? checked.value : null;
}

function showResult(boxId, ok, message) {
    const box = document.getElementById(boxId);
    box.style.display = 'block';
    box.style.background = ok ? '#e8f5e9' : '#ffebee';
    box.style.color = ok ? '#2e7d32' : '#c62828';
    box.textContent = message;
}

function showDbResult(ok, message) {
    showResult('db-test-result', ok, message);
}

function buildDbFormData(profile) {
    const formData = new FormData();
    formData.append('profile', profile);
    appendCsrfToken(formData);
    return formData;
}

function testDbConnection() {
    const profile = getSelectedProfile();
    if (!profile) return;

    postForm('/settings/db/test-connection', buildDbFormData(profile))
        .then(data => showDbResult(data.status === 'ok', data.message))
        .catch(err => showDbResult(false, 'Ошибка запроса: ' + err.message));
}

function connectDb() {
    const profile = getSelectedProfile();
    if (!profile) return;

    postForm('/settings/db/connect', buildDbFormData(profile))
        .then(data => {
            showDbResult(data.status === 'ok', data.message);
            if (data.status === 'ok') {
                if (data.relogin) {
                    setTimeout(() => { window.location = '/auth/login'; }, 600);
                } else {
                    setTimeout(() => location.reload(), 600);
                }
            }
        })
        .catch(err => showDbResult(false, 'Ошибка запроса: ' + err.message));
}

function fillModal(profile) {
    document.getElementById('db-field-id').value = profile ? profile.id : '';
    document.getElementById('db-field-name').value = profile ? profile.name : '';
    document.getElementById('db-field-host').value = profile ? profile.host : '';
    document.getElementById('db-field-port').value = profile ? profile.port : 5432;
    document.getElementById('db-field-dbname').value = profile ? profile.dbname : '';
    document.getElementById('db-field-user').value = profile ? profile.user : '';
    document.getElementById('db-field-password').value = '';
    document.getElementById('db-modal-result').style.display = 'none';
}

function openDbModal(title, profile) {
    document.getElementById('db-modal-title').textContent = title;
    fillModal(profile);
    document.getElementById('db-modal').classList.add('open');
    document.getElementById('db-field-name').focus();
}

function openAddModal() {
    openDbModal('Новое подключение', null);
}

function openEditModal(profileId) {
    const profile = DB_PROFILES.find(p => p.id === profileId);
    if (!profile) return;

    openDbModal('Изменение подключения', profile);
    // DB_PROFILES holds no password — it is never rendered into the page; it is
    // fetched separately, and only for the connection being opened.
    postForm('/settings/db/password', buildDbFormData(profileId))
        .then(data => {
            if (data.status === 'ok') document.getElementById('db-field-password').value = data.password;
        })
        .catch(err => showResult('db-modal-result', false, 'Ошибка запроса: ' + err.message));
}

function closeDbModal() {
    document.getElementById('db-modal').classList.remove('open');
}

// A backdrop click does not close the modal: missing the window wiped the
// filled-in connection form. Closing is explicit only — the "×" button,
// "Отмена", or Escape.
document.addEventListener('keydown', function(event) {
    if (event.key === 'Escape') closeDbModal();
});

function buildModalFormData() {
    const formData = new FormData();
    formData.append('name', document.getElementById('db-field-name').value);
    formData.append('host', document.getElementById('db-field-host').value);
    formData.append('port', document.getElementById('db-field-port').value);
    formData.append('dbname', document.getElementById('db-field-dbname').value);
    formData.append('user', document.getElementById('db-field-user').value);
    formData.append('password', document.getElementById('db-field-password').value);
    appendCsrfToken(formData);
    return formData;
}

function testModalConnection() {
    postForm('/settings/db/test-params', buildModalFormData())
        .then(data => showResult('db-modal-result', data.status === 'ok', data.message))
        .catch(err => showResult('db-modal-result', false, 'Ошибка запроса: ' + err.message));
}

function saveConnection() {
    const profileId = document.getElementById('db-field-id').value;
    const formData = buildModalFormData();
    if (profileId) formData.append('profile', profileId);

    postForm(profileId ? '/settings/db/update' : '/settings/db/add', formData)
        .then(data => {
            if (data.status !== 'ok') {
                showResult('db-modal-result', false, data.message);
                return;
            }
            closeDbModal();
            showDbResult(true, data.message);
            if (data.relogin) {
                setTimeout(() => { window.location = '/auth/login'; }, 600);
            } else {
                setTimeout(() => location.reload(), 600);
            }
        })
        .catch(err => showResult('db-modal-result', false, 'Ошибка запроса: ' + err.message));
}

function deleteConnection(profileId) {
    const profile = DB_PROFILES.find(p => p.id === profileId);
    if (!profile) return;
    if (!confirm('Удалить подключение «' + profile.name + '»?')) return;

    postForm('/settings/db/delete', buildDbFormData(profileId))
        .then(data => {
            showDbResult(data.status === 'ok', data.message);
            if (data.status === 'ok') setTimeout(() => location.reload(), 600);
        })
        .catch(err => showDbResult(false, 'Ошибка запроса: ' + err.message));
}
