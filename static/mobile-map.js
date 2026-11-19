(() => {
  const compare = document.querySelector('.panel, .bar');
  const pins = document.getElementById('pin-tools');
  if (!compare || !pins) return;
  if (location.pathname === '/google') document.body.classList.add('google-view');
  const svg = paths => `<svg viewBox="0 0 24 24" width="22" height="22" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const icons = {
    search: svg('<circle cx="10.8" cy="10.8" r="6.8"/><path d="m16 16 4.5 4.5"/>'),
    layers: svg('<path d="m12 2 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/>'),
    draw: svg('<path d="m4 20 4.5-1 11-11-3.5-3.5-11 11L4 20Z"/><path d="m14.5 6.5 3 3"/>'),
    pin: svg('<path d="M19 10c0 5-7 11-7 11S5 15 5 10a7 7 0 1 1 14 0Z"/><circle cx="12" cy="10" r="2.3"/>'),
    locate: svg('<circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3m0 14v3M2 12h3m14 0h3"/>'),
    back: svg('<path d="m15 18-6-6 6-6"/>'),
    more: svg('<path d="M4 7h16M7 12h10M10 17h4"/>'),
    close: svg('<path d="m6 9 6 6 6-6"/>'),
    open: svg('<path d="m6 15 6-6 6 6"/>')
  };

  const search = document.createElement('div');
  search.id = 'mobile-search';
  search.innerHTML = `${icons.search}<input id="mobile-pin-search" type="search" placeholder="保存ピンを検索" aria-label="保存ピンを検索"><button id="mobile-search-clear" type="button" aria-label="検索を消す">×</button>`;
  document.body.append(search);
  const accountButton = document.createElement('button');
  accountButton.id = 'mobile-account';
  accountButton.type = 'button';
  accountButton.setAttribute('aria-label', 'アカウント設定を開く');
  accountButton.innerHTML = '<span>?</span>';
  document.body.append(accountButton);

  const rail = document.createElement('nav');
  rail.id = 'mobile-rail';
  rail.setAttribute('aria-label', '地図の操作');
  rail.innerHTML = `<button id="rail-layers" type="button" aria-label="地図の種類" title="地図の種類">${icons.layers}<span>地図</span></button><button id="rail-draw" type="button" aria-label="比較する範囲を囲む" title="範囲を囲む">${icons.draw}<span>範囲</span></button><button id="rail-pins" type="button" aria-label="保存ピンを見る" title="保存ピン">${icons.pin}<span>ピン</span></button><button id="rail-locate" type="button" aria-label="現在地を表示" title="現在地">${icons.locate}<span>現在地</span></button><div id="mobile-layer-menu" hidden></div>`;
  document.body.append(rail);

  const sheet = document.createElement('section');
  sheet.id = 'mobile-sheet';
  sheet.dataset.view = 'compare';
  sheet.setAttribute('aria-label', '地図の情報と操作');
  sheet.innerHTML = `<button id="sheet-handle" type="button" aria-label="パネルをたたむ"><span></span></button><div class="sheet-heading"><button id="sheet-back" type="button" aria-label="比較画面に戻る">${icons.back}</button><div><strong id="sheet-title">航空写真の比較</strong><small id="sheet-subtitle">地図上で範囲を選びます</small></div><button id="sheet-more" type="button" aria-label="詳細設定を開く">${icons.more}</button><button id="sheet-close" type="button" aria-label="パネルを収納する">${icons.close}</button></div><div class="sheet-content"></div>`;
  document.body.append(sheet);
  const reopen = document.createElement('button');
  reopen.id = 'mobile-sheet-reopen';
  reopen.type = 'button';
  reopen.innerHTML = `${icons.open}<span>比較パネルを開く</span>`;
  reopen.setAttribute('aria-label', '操作パネルを開く');
  document.body.append(reopen);
  sheet.querySelector('.sheet-content').append(compare, pins);
  const syncHeight = () => document.documentElement.style.setProperty('--mobile-sheet-height', `${sheet.classList.contains('is-hidden') ? 0 : sheet.getBoundingClientRect().height}px`);
  new ResizeObserver(syncHeight).observe(sheet);

  const pinInput = search.querySelector('input');
  const layerMenu = rail.querySelector('#mobile-layer-menu');
  const pinToggle = pins.querySelector('#pin-toggle');
  const title = sheet.querySelector('#sheet-title');
  const subtitle = sheet.querySelector('#sheet-subtitle');
  function hideSheet() { sheet.classList.add('is-hidden'); reopen.classList.add('is-visible'); syncHeight(); }
  function showSheet() { sheet.classList.remove('is-hidden'); reopen.classList.remove('is-visible'); syncHeight(); }
  function choose(mode) {
    showSheet();
    sheet.dataset.view = mode;
    sheet.classList.remove('is-expanded');
    title.textContent = mode === 'pins' ? '保存ピン' : '航空写真の比較';
    subtitle.textContent = mode === 'pins' ? '自分のピンと共有ピン' : '時期を選んで変化を調べる';
    if (mode === 'pins' && pins.classList.contains('pin-collapsed')) pinToggle.click();
  }
  sheet.querySelector('#sheet-back').onclick = () => choose('compare');
  sheet.querySelector('#sheet-more').onclick = () => sheet.classList.toggle('is-expanded');
  sheet.querySelector('#sheet-close').onclick = hideSheet;
  reopen.onclick = showSheet;
  sheet.querySelector('#sheet-handle').onclick = hideSheet;
  rail.querySelector('#rail-pins').onclick = () => choose('pins');
  rail.querySelector('#rail-draw').onclick = () => {
    choose('compare');
    document.getElementById('draw').click();
    hideSheet();
  };
  rail.querySelector('#rail-locate').onclick = () => {
    const existing = document.getElementById('locate');
    if (existing) { existing.click(); return; }
    if (!navigator.geolocation) return;
    navigator.geolocation.getCurrentPosition(position => {
      if (typeof map !== 'undefined' && map) map.setView([position.coords.latitude, position.coords.longitude], Math.max(map.getZoom(), 17));
    }, () => { subtitle.textContent = '現在地を取得できませんでした'; }, {enableHighAccuracy:true, timeout:10000});
  };

  const imagery = document.getElementById('imagery');
  for (const option of imagery.options) {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = option.textContent;
    button.onclick = () => {
      imagery.value = option.value;
      imagery.dispatchEvent(new Event('change', {bubbles:true}));
      layerMenu.hidden = true;
    };
    layerMenu.append(button);
  }
  rail.querySelector('#rail-layers').onclick = () => { layerMenu.hidden = !layerMenu.hidden; };
  document.addEventListener('pointerdown', event => {
    if (!rail.contains(event.target)) layerMenu.hidden = true;
  });

  pinInput.addEventListener('input', () => {
    choose('pins');
    pins.querySelector('#pin-filter').value = 'all';
    window.dispatchEvent(new Event('mobile-pin-search'));
  });
  search.querySelector('#mobile-search-clear').onclick = () => {
    pinInput.value = '';
    window.dispatchEvent(new Event('mobile-pin-search'));
    pinInput.focus();
  };
  pins.querySelector('#pin-add').addEventListener('click', () => {
    if (matchMedia('(max-width:700px)').matches) hideSheet();
  });
})();
