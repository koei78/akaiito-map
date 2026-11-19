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
  dialog.innerHTML = '<div class="account-head"><div class="account-avatar" id="account-avatar">?</div><div class="account-head-text"><h2 id="account-name">アカウント</h2><span class="account-subtitle">アカウント設定</span></div><button id="account-close" type="button" aria-label="閉じる">×</button></div><div class="account-page" id="account-settings"><h3>ピンの表示</h3><label class="account-share-setting"><input id="account-show-shared" type="checkbox"> 他のユーザーが共有したピンを表示する</label><p class="account-share-help">相手が共有したピンだけ表示します。自分の非公開ピンは共有されません。</p><div class="account-divider"></div><h3>ユーザー名を変更</h3><form id="username-form"><label>新しいユーザー名<input name="username" minlength="3" maxlength="40" required></label><label>現在のパスワード<input name="current_password" type="password" required autocomplete="current-password"></label><button class="account-submit">ユーザー名を保存</button></form><div class="account-divider"></div><h3>パスワードを変更</h3><form id="password-form"><label>現在のパスワード<input name="current_password" type="password" required autocomplete="current-password"></label><label>新しいパスワード<input name="new_password" type="password" minlength="8" required autocomplete="new-password"></label><button class="account-submit">パスワードを変更</button></form><div class="account-divider"></div><button class="account-logout" id="account-logout" type="button">ログアウト</button><p class="account-message" id="account-message" role="status"></p></div>';
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
    dialog.querySelector('#account-show-shared').checked = Boolean(account.show_shared_pins);
    dialog.querySelector('#username-form [name=username]').value = account.username;
  }
  const sharedSetting = dialog.querySelector('#account-show-shared');
  window.addEventListener('shared-pins-setting', event => { sharedSetting.checked = Boolean(event.detail); });
  sharedSetting.onchange = async () => {
    const desired = sharedSetting.checked;
    try {
      const response = await fetch('/api/account/settings', {method:'PATCH', headers:{'Content-Type':'application/json'}, body:JSON.stringify({show_shared_pins:desired})});
      if (!response.ok) throw Error('設定を保存できません。');
      window.dispatchEvent(new CustomEvent('shared-pins-setting', {detail:desired}));
      say('ピンの表示設定を保存しました。');
    } catch (error) {sharedSetting.checked = !desired;say(error.message,true);}
  };
  function open() {say('');loadAccount();dialog.showModal();}
  desktopButton.onclick = open;
  if (mobileButton) mobileButton.onclick = open;
  dialog.querySelector('#account-close').onclick = () => dialog.close();
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
