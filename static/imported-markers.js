(() => {
  const panel = document.createElement('div');
  panel.id = 'imported-marker-tools';
  panel.innerHTML = '<button id="imported-marker-toggle" type="button" aria-pressed="false">既存マーカーを表示</button><span id="imported-marker-status" role="status"></span>';
  const pinBody = document.querySelector('#pin-tools .pin-body');
  if (pinBody) pinBody.prepend(panel);
  else document.body.append(panel);
  const button = panel.querySelector('button');
  const status = panel.querySelector('span');
  const rail = document.getElementById('mobile-rail');
  const mobileButton = document.createElement('button');
  mobileButton.id = 'rail-imported-markers';
  mobileButton.type = 'button';
  mobileButton.setAttribute('aria-label', '既存マーカーを表示');
  mobileButton.setAttribute('aria-pressed', 'false');
  mobileButton.innerHTML = '<span class="imported-rail-symbol" aria-hidden="true">●</span><span>既存</span>';
  rail?.querySelector('#rail-pins')?.after(mobileButton);

  const labels = {markers: 'マーカー', existing_customers: '既存顧客', parking_spots: '駐車場'};
  const types = {ng: 'NG', star: 'スター', heart: 'ハート', want_to_go: '行きたい', existing_customer: '既存顧客'};
  const colors = {ng: '#697883', star: '#e8a524', heart: '#e56685', want_to_go: '#25a68d', existing_customer: '#4d86df', parking_spots: '#815bc7'};
  const colorFor = item => colors[item.details?.marker_type] || colors[item.layer] || '#396b9d';
  const googleView = location.pathname === '/google';
  let enabled = false, layers = [], controller = null, timer = null, currentMap = null;

  function clearLayers() {
    for (const layer of layers) googleView ? layer.setMap(null) : currentMap.removeLayer(layer);
    layers = [];
  }
  function popup(item) {
    const detail = item.details || {};
    const body = document.createElement('div');
    body.className = 'imported-marker-popup';
    const heading = document.createElement('strong');
    heading.textContent = item.label;
    body.append(heading);
    const lines = [
      labels[item.layer],
      detail.marker_type ? `種類: ${types[detail.marker_type] || detail.marker_type}` : '',
      detail.prefecture || '', detail.display_address || '', detail.memo || '',
      detail.workers?.length ? `担当: ${detail.workers.map(worker => worker.display_name).filter(Boolean).join('、')}` : '',
    ].filter(Boolean);
    for (const line of lines) {
      const row = document.createElement('div');
      row.textContent = line;
      body.append(row);
    }
    return body;
  }
  function googleIcon(color, cluster) {
    return {path: google.maps.SymbolPath.CIRCLE, scale: cluster ? 20 : 9,
      fillColor: color, fillOpacity: 1, strokeColor: '#fff', strokeWeight: cluster ? 3 : 2};
  }
  function addPoint(item) {
    const color = colorFor(item);
    if (googleView) {
      const marker = new google.maps.Marker({map: currentMap, position: {lat: item.lat, lng: item.lng},
        title: item.label, icon: googleIcon(color, false)});
      marker.addListener('click', () => new google.maps.InfoWindow({content: popup(item)}).open(currentMap, marker));
      layers.push(marker);
    } else {
      const icon = L.divIcon({className: 'imported-marker-icon',
        html: `<span style="background:${color}"></span>`, iconSize: [22, 22], iconAnchor: [11, 11]});
      layers.push(L.marker([item.lat, item.lng], {icon, title: item.label}).addTo(currentMap).bindPopup(popup(item)));
    }
  }
  function addCluster(item) {
    const color = colors[item.layer] || '#396b9d';
    const zoomIn = () => googleView
      ? (currentMap.setCenter({lat: item.lat, lng: item.lng}), currentMap.setZoom(Math.min((currentMap.getZoom() || 10) + 2, 21)))
      : currentMap.setView([item.lat, item.lng], Math.min(currentMap.getZoom() + 2, 21));
    if (googleView) {
      const marker = new google.maps.Marker({map: currentMap, position: {lat: item.lat, lng: item.lng},
        title: `${labels[item.layer]} ${item.count}件。クリックで拡大`, icon: googleIcon(color, true),
        label: {text: String(item.count), color: '#fff', fontWeight: '700', fontSize: '12px'}});
      marker.addListener('click', zoomIn);
      layers.push(marker);
    } else {
      const icon = L.divIcon({className: 'imported-cluster-icon',
        html: `<span style="background:${color}">${item.count}</span>`, iconSize: [42, 42], iconAnchor: [21, 21]});
      layers.push(L.marker([item.lat, item.lng], {icon, title: `${labels[item.layer]} ${item.count}件。クリックで拡大`})
        .addTo(currentMap).on('click', zoomIn));
    }
  }
  function bounds() {
    const view = currentMap.getBounds();
    if (!view) return null;
    const southwest = view.getSouthWest(), northeast = view.getNorthEast();
    const coordinate = (point, axis) => typeof point[axis] === 'function' ? point[axis]() : point[axis];
    const south = Math.max(-90, coordinate(southwest, 'lat'));
    const north = Math.min(90, coordinate(northeast, 'lat'));
    const west = Math.max(-180, coordinate(southwest, 'lng'));
    const east = Math.min(180, coordinate(northeast, 'lng'));
    return south < north && west < east ? {south, west, north, east} : null;
  }
  async function refresh() {
    if (!enabled || !currentMap) return;
    const box = bounds();
    if (!box) return;
    controller?.abort();
    const active = controller = new AbortController();
    const query = new URLSearchParams(box);
    status.textContent = '表示範囲を読み込み中…';
    try {
      const response = await fetch(`/api/imported-markers?${query}`, {signal: active.signal});
      if (!response.ok) throw new Error('読み込みに失敗しました');
      const data = await response.json();
      if (!enabled || active !== controller) return;
      clearLayers();
      if (data.mode === 'missing') {
        status.textContent = '既存マーカーデータが未登録です';
        return;
      }
      for (const item of data.items) data.mode === 'points' ? addPoint(item) : addCluster(item);
      status.textContent = data.mode === 'clusters'
        ? `${data.count.toLocaleString()}件。拡大すると個別表示します`
        : `${data.count.toLocaleString()}件を表示`;
    } catch (error) {
      if (error.name !== 'AbortError') status.textContent = error.message;
    }
  }
  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(refresh, 180);
  }
  function toggle() {
    enabled = !enabled;
    button.setAttribute('aria-pressed', String(enabled));
    mobileButton.setAttribute('aria-pressed', String(enabled));
    button.textContent = enabled ? '既存マーカーを隠す' : '既存マーカーを表示';
    mobileButton.setAttribute('aria-label', button.textContent);
    if (enabled) schedule();
    else {controller?.abort(); clearTimeout(timer); clearLayers(); status.textContent = '';}
  }
  button.onclick = toggle;
  mobileButton.onclick = toggle;
  const waitMap = setInterval(() => {
    if (typeof map === 'undefined' || !map) return;
    clearInterval(waitMap);
    currentMap = map;
    if (googleView) map.addListener('idle', schedule);
    else map.on('moveend zoomend', schedule);
  }, 300);
})();
