(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const originalFetch = window.fetch.bind(window);
  window.fetch = (input, options = {}) => {
    const url = typeof input === 'string' ? input : input.url;
    const method = (options.method || (typeof input !== 'string' && input.method) || 'GET').toUpperCase();
    if (!/^(GET|HEAD|OPTIONS)$/.test(method) && new URL(url, location.href).origin === location.origin) {
      options = {...options, headers: {...options.headers, 'X-CSRF-Token': csrf}};
    }
    return originalFetch(input, options);
  };


  const tools = document.createElement('div');
  tools.id = 'pin-tools';
  tools.innerHTML = '<div class="pin-head"><div><h2>保存したピン</h2><div id="pin-user"></div></div><button id="pin-toggle" type="button" aria-expanded="false" aria-label="ピン一覧を開く">一覧を見る</button></div><div class="pin-body"><input id="pin-search-desktop" type="search" placeholder="ピンを検索" aria-label="ピンを検索"><button id="pin-add">＋ ピンを追加</button><div class="pin-controls"><select id="pin-filter" aria-label="ピンの表示"><option value="mine">自分のピン</option><option value="all">共有ピンも表示</option></select><button id="pin-reload" title="ピンを更新" aria-label="ピンを更新">↻</button></div><button id="pin-history-open" type="button">ピンの履歴を見る</button><div id="pin-count" aria-live="polite"></div><div id="pin-list"></div></div><form method="post" action="/logout"><button>ログアウト</button></form>';
  document.body.append(tools);
  const toggle=tools.querySelector('#pin-toggle');
  const mobile=window.matchMedia('(max-width:650px)');
  const syncMobile=()=>{if(mobile.matches){tools.classList.add('pin-collapsed');toggle.setAttribute('aria-expanded','false');toggle.textContent='一覧を見る';}else{tools.classList.remove('pin-collapsed');toggle.setAttribute('aria-expanded','true');}};
  syncMobile();mobile.addEventListener('change',syncMobile);
  toggle.onclick=()=>{const collapsed=tools.classList.toggle('pin-collapsed');toggle.setAttribute('aria-expanded',String(!collapsed));toggle.setAttribute('aria-label',collapsed?'ピン一覧を開く':'ピン一覧を閉じる');toggle.textContent=collapsed?'一覧を見る':'閉じる';};
  tools.querySelector('form').addEventListener('submit', e => {
    e.preventDefault(); fetch('/logout', {method:'POST'}).then(() => location.href='/login');
  });
  tools.querySelector('#pin-user').textContent = 'ログイン中';
  fetch('/api/me').then(r => r.json()).then(d => {window.pinUserId=d.id;tools.querySelector('#pin-user').textContent = 'ログイン中: ' + d.username;reload();});

  const editor = document.createElement('dialog'); editor.id='pin-editor';
  editor.innerHTML = '<h2 id="pin-heading">ピン</h2><p class="pin-subtitle">この場所の情報を記録します</p><form id="pin-form"><label>ピンの名前<input name="title" maxlength="100" required placeholder="例：確認したい建物"></label><label>メモ<textarea name="note" maxlength="2000" rows="4" placeholder="気づいたことを記録"></textarea></label><label class="share-label"><input type="checkbox" name="shared"> 他のログインユーザーに共有する</label><menu><button type="submit">保存する</button><button type="button" id="pin-history">変更履歴</button><button type="button" id="pin-delete">ピンを削除</button><button type="button" id="pin-cancel">閉じる</button></menu></form><div id="pin-events" hidden></div><p id="pin-error" role="alert"></p>';
  document.body.append(editor);
  const historyDialog = document.createElement('dialog');
  historyDialog.id = 'pin-history-dialog';
  historyDialog.innerHTML = '<div class="history-head"><h2>ピンの履歴</h2><button id="pin-history-close" type="button" aria-label="履歴を閉じる">×</button></div><p class="history-description">追加・変更・削除したピンを確認できます。</p><div id="pin-history-list" aria-live="polite"></div>';
  document.body.append(historyDialog);
  historyDialog.querySelector('#pin-history-close').onclick = () => historyDialog.close();
  tools.querySelector('#pin-history-open').onclick = async () => {
    const list = historyDialog.querySelector('#pin-history-list');
    list.textContent = '読み込み中…';
    historyDialog.showModal();
    try {
      const response = await fetch('/api/pins/history');
      if (!response.ok) throw new Error('history request failed');
      const rows = await response.json();
      list.replaceChildren();
      if (!rows.length) {list.innerHTML = '<p class="history-empty">まだピンの履歴がありません。</p>';return;}
      const labels = {created:'追加',updated:'更新',deleted:'削除'};
      for (const row of rows) {
        const item = document.createElement(row.deleted_at ? 'div' : 'button');
        item.className = 'history-item';
        if (!row.deleted_at) {
          item.type = 'button';
          item.onclick = () => {historyDialog.close();window.dispatchEvent(new CustomEvent('open-saved-pin',{detail:row.pin_id}));};
        }
        const heading = document.createElement('strong');
        heading.textContent = `${labels[row.action] || row.action} · ${row.title}`;
        const details = document.createElement('small');
        details.textContent = `${row.recorded_at} UTC${row.deleted_at ? ' · 削除済み' : ''}`;
        item.append(heading, details);
        list.append(item);
      }
    } catch (_) {list.textContent = '履歴を取得できませんでした。';}
  };
  let allPins=[], layers=[], activePin=null, position=null, adding=false;
  const error = editor.querySelector('#pin-error');
  const isGoogle = () => location.pathname === '/google';
  function removeLayers(){layers.forEach(layer => isGoogle() ? layer.setMap(null) : map.removeLayer(layer));layers=[];}
  function drawPins(){
    if (typeof map === 'undefined' || !map) return;
    removeLayers();
    const filter=tools.querySelector('#pin-filter').value;
    const query=((mobile.matches ? document.getElementById('mobile-pin-search')?.value : tools.querySelector('#pin-search-desktop').value) || '').trim().toLocaleLowerCase();
    const list=tools.querySelector('#pin-list');list.innerHTML='';
    const visiblePins=allPins.filter(p => (filter==='all' || p.owner_id===window.pinUserId) && (!query || (p.title+' '+p.note+' '+p.owner).toLocaleLowerCase().includes(query)));
    tools.querySelector('#pin-count').textContent=visiblePins.length+'件のピン';
    if(!visiblePins.length)list.innerHTML='<div class="pin-empty">まだピンがありません。<br>地図をクリックして追加しましょう。</div>';
    for(const pin of visiblePins){
      const own=pin.owner_id===window.pinUserId;
      const label=(own?'自分':'共有: '+pin.owner)+' / '+pin.title;
      const button=document.createElement('button');button.textContent=label;if(!own)button.className='shared-pin';button.onclick=()=>focusPin(pin);list.append(button);
      if(isGoogle()){
        const layer=new google.maps.Marker({map,position:{lat:pin.lat,lng:pin.lng},title:label,icon:own?undefined:{url:'https://maps.google.com/mapfiles/ms/icons/blue-dot.png'}});
        layer.addListener('click',()=>openPin(pin));layers.push(layer);
      } else {
        const layer=own?L.marker([pin.lat,pin.lng]).addTo(map):L.circleMarker([pin.lat,pin.lng],{radius:9,color:'#228be6',fillColor:'#228be6',fillOpacity:1}).addTo(map);
        layer.on('click',()=>openPin(pin));layers.push(layer);
      }
    }
  }
  function focusPin(pin){isGoogle()?map.panTo({lat:pin.lat,lng:pin.lng}):map.setView([pin.lat,pin.lng],Math.max(map.getZoom(),18));openPin(pin);}
  window.addEventListener('open-saved-pin',event=>{const pin=allPins.find(p=>p.id===event.detail);if(pin)focusPin(pin);});
  function openPin(pin){
    activePin=pin;position={lat:pin.lat,lng:pin.lng};
    const own=pin.owner_id===window.pinUserId;
    editor.querySelector('#pin-heading').textContent=own?'ピンを編集':pin.owner+' の共有ピン';
    editor.querySelector('[name=title]').value=pin.title;editor.querySelector('[name=note]').value=pin.note;
    editor.querySelector('[name=shared]').checked=Boolean(pin.shared);
    editor.querySelectorAll('input,textarea').forEach(el=>el.disabled=!own);
    editor.querySelector('[type=submit]').hidden=!own;editor.querySelector('#pin-delete').hidden=!own;
    editor.querySelector('#pin-history').hidden=false;editor.querySelector('#pin-events').hidden=true;error.textContent='';editor.showModal();
  }
  function newPin(lat,lng){
    adding=false;activePin=null;position={lat,lng};editor.querySelector('#pin-heading').textContent='新しいピン';
    editor.querySelector('#pin-form').reset();editor.querySelectorAll('input,textarea').forEach(el=>el.disabled=false);
    editor.querySelector('[type=submit]').hidden=false;editor.querySelector('#pin-delete').hidden=true;
    editor.querySelector('#pin-history').hidden=true;editor.querySelector('#pin-events').hidden=true;error.textContent='';editor.showModal();
  }
  async function reload(){const response=await fetch('/api/pins');if(!response.ok)return;allPins=await response.json();drawPins();}
  tools.querySelector('#pin-filter').onchange=drawPins;tools.querySelector('#pin-reload').onclick=reload;
  tools.querySelector('#pin-search-desktop').oninput=drawPins;
  window.addEventListener('mobile-pin-search', drawPins);
  tools.querySelector('#pin-add').onclick=()=>{adding=true;tools.querySelector('#pin-add').textContent='地図上の場所をクリック';};
  function mapClick(event){if(!adding)return;tools.querySelector('#pin-add').textContent='＋ 地図にピンを追加';newPin(event.latLng?event.latLng.lat():event.latlng.lat,event.latLng?event.latLng.lng():event.latlng.lng);}
  const waitMap=setInterval(()=>{if(typeof map==='undefined'||!map)return;clearInterval(waitMap);if(isGoogle())map.addListener('click',mapClick);else map.on('click',mapClick);reload();},500);
  editor.querySelector('#pin-cancel').onclick=()=>editor.close();
  editor.querySelector('#pin-form').onsubmit=async e=>{
    e.preventDefault();const form=e.currentTarget;
    const body={...position,title:form.elements.namedItem('title').value.trim(),note:form.elements.namedItem('note').value.trim(),shared:form.elements.namedItem('shared').checked};
    const response=await fetch(activePin?'/api/pins/'+activePin.id:'/api/pins',{method:activePin?'PUT':'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data=await response.json();if(!response.ok){error.textContent=data.error;return;}editor.close();reload();
  };
  editor.querySelector('#pin-delete').onclick=async()=>{if(!activePin||!confirm('このピンを削除しますか？'))return;const response=await fetch('/api/pins/'+activePin.id,{method:'DELETE'});if(response.ok){editor.close();reload();}};
  editor.querySelector('#pin-history').onclick=async()=>{
    const response=await fetch('/api/pins/'+activePin.id+'/history');const data=await response.json();
    const box=editor.querySelector('#pin-events');box.hidden=false;box.replaceChildren();
    if(!response.ok){box.textContent=data.error;return;}
    const labels={created:'追加',updated:'更新',deleted:'削除'};
    for(const item of data){
      const row=document.createElement('div');row.className='pin-event';
      const title=document.createElement('strong');title.textContent=(labels[item.action]||item.action)+' · '+item.title;
      const detail=document.createElement('small');detail.textContent=item.recorded_at+' UTC · '+item.actor+' · '+(item.shared?'共有':'自分のみ');
      row.append(title,detail);
      if(item.note){const note=document.createElement('p');note.textContent=item.note;row.append(note);}
      box.append(row);
    }
  };
})();
