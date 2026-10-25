/* Keep testing pages in the same workspace navigation as chat and logs. */
(() => {
    'use strict';
    const list = document.getElementById('sessionListUI');
    const button = document.getElementById('newChatTopBtn');
    let username;
    async function api(url, options) {
        const response = await fetch(url, {cache: 'no-store', ...options});
        const data = await response.json();
        if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '请求失败，请重试');
        return data;
    }
    function empty(text) {
        const note = document.createElement('p');
        note.className = 'sidebar-empty'; note.textContent = text;
        list.replaceChildren(note);
    }
    button.disabled = true;
    button.onclick = async () => {
        if (!username || button.disabled) return;
        button.disabled = true;
        try {
            const data = await api('/sessions/' + encodeURIComponent(username) + '/new', {method: 'POST'});
            location.assign('/?session_id=' + encodeURIComponent(data.session_id));
        } catch (error) {
            empty('创建对话失败：' + error.message);
            button.disabled = false;
        }
    };
    (async () => {
        try {
            username = (await api('/config')).username;
            document.getElementById('usernameLabel').textContent = username;
            document.getElementById('userAvatar').textContent = username.slice(0, 1).toUpperCase();
            button.disabled = false;
            const data = await api('/sessions/' + encodeURIComponent(username) + '?mode=designer');
            const sessions = (data.sessions || []).slice(0, 30);
            document.getElementById('sessionCount').textContent = sessions.length;
            list.replaceChildren();
            if (!sessions.length) empty('还没有历史对话');
            for (const session of sessions) {
                const item = document.createElement('a'); item.className = 'session-item';
                item.href = '/?session_id=' + encodeURIComponent(session.id);
                item.textContent = session.title || '未命名设计'; item.title = item.textContent;
                list.appendChild(item);
            }
        } catch (error) { empty('记录加载失败，请刷新重试'); }
    })();
})();
