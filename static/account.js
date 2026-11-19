(() => {
  const head = document.querySelector('#pin-tools .pin-head');
  if (!head) return;
  const desktopButton = document.createElement('button');
  desktopButton.id = 'desktop-account';
  desktopButton.type = 'button';
  desktopButton.setAttribute('aria-label', 'アカウント設定を開く');
  desktopButton.textContent = '?';
  head.append(desktopButton);
  const mobileButton = document.getElementById('mobile-account');

  const dialog = document.createElement('dialog');
  dialog.id = 'account-dialog';
  dialog.innerHTML = '<div class="account-head"><div class="account-avatar" id="account-avatar">?</div><div class="account-head-text"><h2 id="account-name">アカウント</h2><span class="account-subtitle">アカウント設定とピン履歴</span></div><button id="account-close" type="button" aria-label="閉じる">×</button></div><div class="account-tabs" role="tablist"><button id="account-tab-settings" role="tab" aria-selected="true" type="button">アカウント設定</button><button id="account-tab-history" role="tab" aria-selected="false" type="button">ピン履歴</button></div><div class="account-page" id="account-settings"><h3>ユーザー名を変更</h3><form id="username-form"><label>新しいユーザー名<input name="username" minlength="3" maxlength="40" required></label><label>現在のパスワード<input name="current_password" type="password" required autocomplete="current-password"></label><button class="account-submit">ユーザー名を保存</button></form><div class="account-divider"></div><h3>パスワードを変更</h3><form id="password-form"><label>現在のパスワード<input name="current_password" type="password" required autocomplete="current-password"></label><label>新しいパスワード<input name="new_password" type="password" minlength="8" required autocomplete="new-password"></label><button class="account-submit">パスワードを変更</button></form><div class="account-divider"></div><button class="account-logout" id="account-logout" type="button">ログアウト</button><p class="account-message" id="account-message" role="status"></p></div><div class="account-page" id="account-history-page" hidden><div id="account-history"></div></div>';
  document.body.append(dialog);
  const message = dialog.querySelector('#account-message');
  function say(value, bad=false) {message.textContent=value;message.classList.toggle('error',bad);}
  function setName(username) {
    const initial = Array.from(username || '?')[0].toLocaleUpperCase();
    dialog.querySelector('#account-name').textContent = username;
    dialog.querySelector('#account-avatar').textContent = initial;
    desktopButton.textContent = initial;
    if (mobileButton) mobileButton.querySelector('span').textContent = initial;
  }
  async function loadAccount() {
    const response = await fetch('/api/account');
    if (!response.ok) return;
    const account = await response.json();
    setName(account.username);
    dialog.querySelector('#username-form [name=username]').value = account.username;
  }
  function selectTab(name) {
    const history = name === 'history';
    dialog.querySelector('#account-tab-settings').setAttribute('aria-selected', String(!history));
    dialog.querySelector('#account-tab-history').setAttribute('aria-selected', String(history));
    dialog.querySelector('#account-settings').hidden = history;
    dialog.querySelector('#account-history-page').hidden = !history;
    if (history) loadHistory();
  }
  async function loadHistory() {
    const response = await fetch('/api/pins/history');
    const box = dialog.querySelector('#account-history');
    box.replaceChildren();
    if (!response.ok) {box.textContent='履歴を取得できませんでした。';return;}
    const rows = await response.json();
    if (!rows.length) {box.innerHTML='<div class="history-empty">まだピンの履歴がありません。</div>';return;}
    const labels = {created:'追加',updated:'更新',deleted:'削除'};
    for (const row of rows) {
      const item = document.createElement(row.deleted_at ? 'div' : 'button');
      item.className = 'history-item';
      if (!row.deleted_at) {
        item.type = 'button';
        item.onclick = () => {dialog.close();window.dispatchEvent(new CustomEvent('open-saved-pin',{detail:row.pin_id}));};
      }
      const heading = document.createElement('strong');
      heading.textContent = `${labels[row.action] || row.action} · ${row.title}`;
      const details = document.createElement('small');
      details.textContent = `${row.recorded_at} UTC${row.deleted_at ? ' · 削除済み' : ''}`;
      item.append(heading, details);
      box.append(item);
    }
  }
  function open() {selectTab('settings');say('');loadAccount();dialog.showModal();}
  desktopButton.onclick = open;
  if (mobileButton) mobileButton.onclick = open;
  dialog.querySelector('#account-close').onclick = () => dialog.close();
  dialog.querySelector('#account-tab-settings').onclick = () => selectTab('settings');
  dialog.querySelector('#account-tab-history').onclick = () => selectTab('history');
  dialog.querySelector('#username-form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const body = {username:form.elements.namedItem('username').value.trim(),current_password:form.elements.namedItem('current_password').value};
    const response = await fetch('/api/account/username',{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) {say(result.error,true);return;}
    setName(result.username);form.elements.namedItem('current_password').value='';say('ユーザー名を変更しました。');
    const pinUser = document.getElementById('pin-user');if(pinUser)pinUser.textContent='ログイン中: '+result.username;
  };
  dialog.querySelector('#password-form').onsubmit = async event => {
    event.preventDefault();
    const form = event.currentTarget;
    const body = {current_password:form.elements.namedItem('current_password').value,new_password:form.elements.namedItem('new_password').value};
    const response = await fetch('/api/account/password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result = await response.json();
    if (!response.ok) {say(result.error,true);return;}
    form.reset();say('パスワードを変更しました。');
  };
  dialog.querySelector('#account-logout').onclick = async () => {await fetch('/logout',{method:'POST'});location.href='/login';};
  loadAccount();
})();
