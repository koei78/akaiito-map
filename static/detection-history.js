(() => {
  const toolbar = document.querySelector('.actions, .bar');
  if (!toolbar) return;

  const trigger = document.createElement('button');
  trigger.id = 'detection-history-trigger';
  trigger.type = 'button';
  trigger.textContent = '検出履歴';
  toolbar.append(trigger);

  const dialog = document.createElement('dialog');
  dialog.id = 'detection-history-dialog';
  dialog.innerHTML = '<div class="detection-history-head"><button id="detection-history-back" type="button" hidden aria-label="履歴一覧に戻る">‹</button><h2 id="detection-history-title">新築検出の履歴</h2><button id="detection-history-close" type="button" aria-label="閉じる">×</button></div><div id="detection-history-content"></div>';
  document.body.append(dialog);
  const content = dialog.querySelector('#detection-history-content');
  const back = dialog.querySelector('#detection-history-back');
  const title = dialog.querySelector('#detection-history-title');
  const active = document.createElement('div');
  active.id = 'detection-history-active';
  active.hidden = true;
  active.innerHTML = '<span></span><button type="button" aria-label="履歴の地図表示を終了">×</button>';
  document.body.append(active);
  const isGoogle = location.pathname === '/google';
  let layers = [];

  function clearOverlay() {
    for (const layer of layers) {
      if (isGoogle) layer.setMap(null);
      else map.removeLayer(layer);
    }
    layers = [];
    active.hidden = true;
  }
  active.querySelector('button').onclick = clearOverlay;
  document.getElementById('start')?.addEventListener('click', clearOverlay);

  function timestamp(value) {
    if (!value) return '';
    const date = new Date(value.replace(' ', 'T') + 'Z');
    return Number.isNaN(date.getTime()) ? value : date.toLocaleString('ja-JP', {dateStyle:'medium', timeStyle:'short'});
  }
  function addText(parent, tag, value) {
    const node = document.createElement(tag);
    node.textContent = value;
    parent.append(node);
    return node;
  }
  async function openList() {
    back.hidden = true;
    title.textContent = '新築検出の履歴';
    content.textContent = '読み込み中…';
    if (!dialog.open) dialog.showModal();
    try {
      const response = await fetch('/api/detections/history');
      if (!response.ok) throw new Error();
      const rows = await response.json();
      content.replaceChildren();
      if (!rows.length) {
        addText(content, 'p', 'まだ検出履歴がありません。比較を実行すると、結果がここに保存されます。').className = 'detection-empty';
        return;
      }
      for (const row of rows) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'detection-run';
        addText(button, 'strong', `${timestamp(row.completed_at || row.created_at)} · 新築候補 ${row.building_count}件`);
        addText(button, 'small', `${row.years}年前と比較${row.status === 'partial' ? ' · 一部の範囲は取得不可' : ''}`);
        button.onclick = () => openRun(row.id);
        content.append(button);
      }
    } catch (_) { content.textContent = '検出履歴を読み込めませんでした。'; }
  }
  async function openRun(id) {
    content.textContent = '読み込み中…';
    try {
      const response = await fetch(`/api/detections/history/${id}`);
      if (!response.ok) throw new Error();
      const run = await response.json();
      back.hidden = false;
      title.textContent = '検出結果';
      content.replaceChildren();
      const summary = document.createElement('div');
      summary.className = 'detection-summary';
      addText(summary, 'strong', `新築候補 ${run.building_count}件`);
      addText(summary, 'small', `${timestamp(run.completed_at)} · ${run.years}年前と比較`);
      content.append(summary);
      const showAll = addText(content, 'button', '地図に表示する');
      showAll.type = 'button';
      showAll.className = 'detection-show-all';
      showAll.onclick = () => showOnMap(run);
      if (!run.points.length) addText(content, 'p', 'この比較では新築候補が検出されませんでした。').className = 'detection-empty';
      for (const point of run.points) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'detection-building';
        addText(button, 'strong', `新築候補 ${point.number || ''}`);
        addText(button, 'small', `${point.old_date || '過去'} → ${point.new_date || '現在'}`);
        if (point.reason) addText(button, 'span', point.reason);
        button.onclick = () => showOnMap(run, point);
        content.append(button);
      }
    } catch (_) { content.textContent = 'この検出結果を読み込めませんでした。'; }
  }
  function showOnMap(run, focusPoint) {
    if (typeof map === 'undefined' || !map) {
      content.textContent = '地図の読み込みが完了していません。';
      return;
    }
    clearOverlay();
    const coords = [];
    for (const point of run.points) {
      const outline = point.outline;
      if (!Array.isArray(outline) || outline.length < 3) continue;
      const valid = outline.every(pair => Array.isArray(pair) && pair.length >= 2 && Number.isFinite(pair[0]) && Number.isFinite(pair[1]));
      if (!valid) continue;
      coords.push(...outline);
      const info = document.createElement('div');
      addText(info, 'strong', `新築候補 ${point.number || ''}`);
      addText(info, 'div', `撮影日: ${point.old_date || '不明'} → ${point.new_date || '不明'}`);
      if (point.reason) addText(info, 'div', point.reason);
      if (isGoogle) {
        const layer = new google.maps.Polygon({map, paths:outline.map(pair => ({lat:pair[0], lng:pair[1]})), strokeColor:'#d93025', strokeWeight:3, fillColor:'#d93025', fillOpacity:.16});
        const popup = new google.maps.InfoWindow({content:info});
        layer.addListener('click', event => {popup.setPosition(event.latLng);popup.open(map);});
        layers.push(layer);
      } else {
        const layer = L.polygon(outline, {color:'#d93025',weight:3,fillOpacity:.16}).addTo(map);
        layer.bindPopup(info);
        layers.push(layer);
      }
    }
    const target = focusPoint?.outline?.length >= 3 ? focusPoint.outline : coords.length ? coords : run.polygon;
    if (target?.length >= 3) {
      if (isGoogle) {
        const bounds = new google.maps.LatLngBounds();
        target.forEach(pair => bounds.extend({lat:pair[0],lng:pair[1]}));
        map.fitBounds(bounds);
      } else map.fitBounds(L.latLngBounds(target), {padding:[30,30],maxZoom:19});
    }
    active.querySelector('span').textContent = `検出履歴を表示中 · ${run.building_count}件`;
    active.hidden = false;
    dialog.close();
  }
  trigger.onclick = openList;
  window.addEventListener('open-detection-history', openList);
  back.onclick = openList;
  dialog.querySelector('#detection-history-close').onclick = () => dialog.close();
})();
