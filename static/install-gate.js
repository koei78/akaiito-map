(() => {
  const isPhone = /Android|iPhone|iPod/i.test(navigator.userAgent) && matchMedia('(pointer:coarse)').matches;
  const standalone = () => matchMedia('(display-mode: standalone)').matches || matchMedia('(display-mode: fullscreen)').matches || navigator.standalone === true;
  let installEvent = null;

  if ('serviceWorker' in navigator && (location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1')) {
    window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(() => {}));
  }
  if (!isPhone || standalone()) return;
  document.documentElement.classList.add('requires-install');

  window.addEventListener('beforeinstallprompt', event => {
    event.preventDefault();
    installEvent = event;
    const button = document.getElementById('install-now');
    if (button) button.hidden = false;
  });
  window.addEventListener('appinstalled', () => {
    installEvent = null;
    const note = document.getElementById('install-status');
    if (note) note.textContent = '追加できました。ホーム画面のアイコンから起動してください。';
    const button = document.getElementById('install-now');
    if (button) button.hidden = true;
  });

  function showGate() {
    if (standalone()) { document.documentElement.classList.remove('requires-install'); return; }
    const ios = /iPhone|iPod/i.test(navigator.userAgent);
    const secure = location.protocol === 'https:' || location.hostname === 'localhost' || location.hostname === '127.0.0.1';
    const gate = document.createElement('div');
    gate.id = 'install-gate';
    gate.innerHTML = '<div class="gate-card"><img class="gate-icon" src="/static/app-icon-192.png?v=3" alt="新築マップ"><h1>ホーム画面から開いてください</h1><p>新築マップはホーム画面に追加すると利用できます。</p><div class="gate-steps" id="install-steps"></div><button id="install-now" type="button" hidden>ホーム画面に追加する</button><small class="gate-note" id="install-status">追加後はホーム画面の「新築マップ」から起動してください。</small></div>';
    document.body.append(gate);
    const steps = gate.querySelector('#install-steps');
    steps.innerHTML = !secure
      ? '<strong>HTTPS で開いてください</strong>ホーム画面への追加には HTTPS のアドレスが必要です。'
      : ios
        ? '<strong>iPhone で追加する方法</strong>ブラウザの共有ボタンを押し、「ホーム画面に追加」を選んでください。'
        : '<strong>Android で追加する方法</strong>下のボタンが表示されたら押してください。表示されない場合はブラウザのメニューから「ホーム画面に追加」を選んでください。';
    const button = gate.querySelector('#install-now');
    button.hidden = !installEvent;
    button.onclick = async () => {
      if (!installEvent) return;
      const event = installEvent;
      installEvent = null;
      button.hidden = true;
      await event.prompt();
      const choice = await event.userChoice;
      gate.querySelector('#install-status').textContent = choice.outcome === 'accepted'
        ? '追加後はホーム画面のアイコンから起動してください。'
        : 'ブラウザのメニューから、いつでも追加できます。';
    };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', showGate, {once:true});
  else showGate();
})();
