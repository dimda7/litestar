function jiraFindAttachments(issuePrefixId, issueNumId, fileInputId, fileNameId, listContainerId, descriptionId) {
    const prefix = document.getElementById(issuePrefixId).value.trim();
    const num = document.getElementById(issueNumId).value.trim();
    const issue = prefix + num;
    const container = document.getElementById(listContainerId);
    const descBox = document.getElementById(descriptionId);
    if (!num) {
        alert('Введите номер задачи Jira');
        return;
    }
    container.style.display = 'block';
    container.textContent = 'Загрузка...';
    descBox.style.display = 'none';

    getJson('/jira/attachments?issue=' + encodeURIComponent(issue))
        .then(data => {
            if (data.status === 'error') {
                container.textContent = data.message;
                container.style.color = '#c62828';
                return;
            }

            descBox.textContent = data.description || 'Описание отсутствует';
            descBox.style.display = 'block';

            if (!data.attachments.length) {
                container.textContent = 'В задаче нет вложений';
                container.style.color = 'var(--text-hint)';
                return;
            }
            container.style.color = '';
            container.innerHTML = '';
            data.attachments.forEach(att => {
                const btn = document.createElement('button');
                btn.type = 'button';
                btn.textContent = att.filename + ' (' + Math.round(att.size / 1024) + ' КБ)';
                btn.style.cssText = 'display: block; width: 100%; text-align: left; padding: 8px 12px; margin-bottom: 6px; border: 1px solid var(--border); border-radius: 8px; background: var(--bg-card); color: var(--text-primary); cursor: pointer;';
                btn.onclick = () => jiraUseAttachment(att.content_url, att.filename, fileInputId, fileNameId, listContainerId);
                container.appendChild(btn);
            });
        })
        .catch(err => {
            container.textContent = 'Ошибка запроса: ' + err.message;
            container.style.color = '#c62828';
        });
}

function jiraUseAttachment(contentUrl, filename, fileInputId, fileNameId, listContainerId) {
    const container = document.getElementById(listContainerId);
    container.style.color = '';
    container.textContent = 'Скачивание «' + filename + '»...';

    fetch('/jira/attachments/download?content_url=' + encodeURIComponent(contentUrl))
        .then(async resp => {
            await throwIfUnauthorized(resp);
            if (!resp.ok) throw new Error('HTTP ' + resp.status);
            return resp.blob();
        })
        .then(blob => {
            const file = new File([blob], filename);
            const dt = new DataTransfer();
            dt.items.add(file);
            document.getElementById(fileInputId).files = dt.files;
            document.getElementById(fileNameId).textContent = filename;
            container.style.display = 'none';
        })
        .catch(err => {
            container.textContent = 'Ошибка загрузки файла: ' + err.message;
            container.style.color = '#c62828';
        });
}
