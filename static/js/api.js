function appendCsrfToken(formData) {
    formData.append('_csrf_token', document.querySelector('meta[name="csrf-token"]').content);
    return formData;
}

// AuthMiddleware answers any fetch with a 401, not only the ones expecting
// JSON: the attachment download returns a blob and cannot go through
// readJsonResponse, yet needs the same re-login.
async function throwIfUnauthorized(resp) {
    if (resp.status !== 401) return;

    let target = '/auth/login';
    try { target = (await resp.json()).location || target; } catch (e) {}
    window.location = target;
    throw new Error('Сессия завершена — выполняется переход на страницу входа');
}

// Parse a JSON response. A bare resp.json() died with "Unexpected token '<'"
// whenever the server returned HTML — the login page after the session expired,
// or the 500 page — so the user saw a parser error instead of the reason.
async function readJsonResponse(resp) {
    await throwIfUnauthorized(resp);

    if (!(resp.headers.get('content-type') || '').includes('application/json')) {
        throw new Error('сервер ответил ' + resp.status + ' без JSON, подробности в log/app.log');
    }

    const data = await resp.json();
    if (!resp.ok) throw new Error(data.detail || data.message || ('ошибка ' + resp.status));
    return data;
}

async function postForm(url, formData) {
    return readJsonResponse(await fetch(url, { method: 'POST', body: formData }));
}

async function getJson(url) {
    return readJsonResponse(await fetch(url));
}
