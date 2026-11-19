(() => {
  const mobile = document.getElementById('rail-locate');
  const desktop = document.getElementById('locate');
  if (!mobile && !desktop) return;
  let watch = null;
  let heading = null;
  let lastPosition = null;
  let marker = null;
  let accuracy = null;
  let orientationStarted = false;
  const googleMap = location.pathname === '/google';
  const status = document.getElementById('status');
  const setStatus = message => { if (status) status.textContent = message; };

  function iconHtml() {
    const arrow = heading === null ? '' : `<span class="location-heading" style="transform:translate(-50%,-50%) rotate(${heading}deg)"></span>`;
    return `<span class="location-symbol">${arrow}<span class="location-dot"></span></span>`;
  }
  function googleIcon() {
    const arrow = heading === null ? '' : `<path d="M40 5 L29 31 L51 31 Z" fill="#1a73e8" stroke="white" stroke-width="3"/>`;
    return {url: `data:image/svg+xml;charset=UTF-8,${encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80" viewBox="0 0 80 80"><g transform="rotate(${heading || 0} 40 40)">${arrow}</g><circle cx="40" cy="40" r="13" fill="white"/><circle cx="40" cy="40" r="9" fill="#1a73e8"/></svg>`)}`, scaledSize: new google.maps.Size(64, 64), anchor: new google.maps.Point(32, 32)};
  }
  function updateMarker(position, center = false) {
    lastPosition = position;
    const lat = position.coords.latitude, lng = position.coords.longitude;
    const bearing = position.coords.heading;
    if (heading === null && Number.isFinite(bearing) && bearing >= 0) heading = bearing;
    if (googleMap) {
      if (!window.google?.maps || typeof map === 'undefined' || !map) return;
      const point = {lat, lng};
      if (!marker) marker = new google.maps.Marker({map, position:point, title:'現在地', clickable:false, zIndex:1000, icon:googleIcon()});
      else { marker.setPosition(point); marker.setIcon(googleIcon()); }
      if (!accuracy) accuracy = new google.maps.Circle({map, center:point, radius:position.coords.accuracy, strokeColor:'#1a73e8', strokeOpacity:.5, strokeWeight:1, fillColor:'#1a73e8', fillOpacity:.12, clickable:false});
      else { accuracy.setCenter(point); accuracy.setRadius(position.coords.accuracy); }
      if (center) map.panTo(point);
    } else {
      if (typeof L === 'undefined' || typeof map === 'undefined') return;
      const point = [lat, lng];
      const icon = L.divIcon({className:'location-marker', html:iconHtml(), iconSize:[64,64], iconAnchor:[32,32]});
      if (!marker) marker = L.marker(point, {icon, interactive:false, zIndexOffset:1000}).addTo(map);
      else { marker.setLatLng(point); marker.setIcon(icon); }
      if (!accuracy) accuracy = L.circle(point, {radius:position.coords.accuracy, color:'#1a73e8', weight:1, opacity:.5, fillColor:'#1a73e8', fillOpacity:.12, interactive:false}).addTo(map);
      else { accuracy.setLatLng(point); accuracy.setRadius(position.coords.accuracy); }
      if (center) map.setView(point, Math.max(map.getZoom(), 17));
    }
  }
  async function startOrientation() {
    if (orientationStarted || typeof DeviceOrientationEvent === 'undefined') return;
    try {
      if (typeof DeviceOrientationEvent.requestPermission === 'function') {
        if (await DeviceOrientationEvent.requestPermission() !== 'granted') return;
      }
      orientationStarted = true;
      window.addEventListener('deviceorientationabsolute', onOrientation, true);
      window.addEventListener('deviceorientation', onOrientation, true);
    } catch (_) { /* Heading is optional; GPS remains available. */ }
  }
  function onOrientation(event) {
    let direction = Number.isFinite(event.webkitCompassHeading) ? event.webkitCompassHeading : null;
    if (direction === null && event.absolute && Number.isFinite(event.alpha)) {
      direction = (360 - event.alpha + (screen.orientation?.angle || 0)) % 360;
    }
    if (direction === null) return;
    heading = direction;
    if (lastPosition) updateMarker(lastPosition);
  }
  function stop() {
    if (watch !== null) navigator.geolocation.clearWatch(watch);
    watch = null;
    if (marker) googleMap ? marker.setMap(null) : map.removeLayer(marker);
    if (accuracy) googleMap ? accuracy.setMap(null) : map.removeLayer(accuracy);
    marker = accuracy = lastPosition = null;
    if (desktop) desktop.textContent = '現在地を表示';
    mobile?.classList.remove('is-active');
    setStatus('現在地の表示を停止しました。');
  }
  async function toggle() {
    if (watch !== null) { stop(); return; }
    if (!navigator.geolocation) { setStatus('この端末では位置情報を取得できません。'); return; }
    await startOrientation();
    setStatus('現在地を取得しています…');
    let first = true;
    watch = navigator.geolocation.watchPosition(position => {
      updateMarker(position, first);
      first = false;
      if (desktop) desktop.textContent = '現在地を停止';
      mobile?.classList.add('is-active');
      setStatus(heading === null ? '現在地を表示中です。端末の向きは取得できない場合があります。' : '現在地と端末の向きを表示中です。');
    }, error => {
      setStatus(error.code === 1 ? '位置情報の許可が必要です。' : '現在地を取得できませんでした。');
      if (error.code === 1) {
        stop();
        setStatus('位置情報の許可が必要です。');
      }
    }, {enableHighAccuracy:true, timeout:15000, maximumAge:5000});
  }
  if (desktop) desktop.onclick = toggle;
  if (mobile) mobile.onclick = toggle;
  window.addEventListener('pagehide', () => { if (watch !== null) navigator.geolocation.clearWatch(watch); });
})();
