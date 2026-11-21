(() => {
  const googleView = location.pathname === '/google';
  let layers = [], subscriptions = [], timer = null, generation = 0, localPoints = [];
  window.sharedDetectionPoints = [];
  const clear = () => {
    subscriptions.forEach(fn => fn()); subscriptions = [];
    layers.forEach(layer => googleView ? layer.setMap(null) : map.removeLayer(layer)); layers = [];
  };
  const schedule = () => {clearTimeout(timer);timer=setTimeout(load,350);};
  async function load() {
    const bounds = map.getBounds(); if (!bounds) return;
    const south=googleView?bounds.getSouthWest().lat():bounds.getSouth(), west=googleView?bounds.getSouthWest().lng():bounds.getWest();
    const north=googleView?bounds.getNorthEast().lat():bounds.getNorth(), east=googleView?bounds.getNorthEast().lng():bounds.getEast();
    const requestId=++generation, params=new URLSearchParams({south,west,north,east});
    try {
      const response=await fetch('/api/shared-detections?'+params);if(!response.ok)throw Error();
      const data=await response.json();if(requestId!==generation)return;
      window.sharedDetectionPoints=data.points;
      clear();
      for(const point of data.points) {
        if(localPoints.some(p=>Math.abs(p.lat-point.lat)<.000005 && Math.abs(p.lon-point.lon)<.000005))continue;
        if(!Array.isArray(point.outline)||point.outline.length<3)continue;
        const info=document.createElement('div');info.textContent='共有された新築候補（タップで確認済み切替）';
        if(googleView){
          const layer=new google.maps.Polygon({map,paths:point.outline.map(([lat,lng])=>({lat,lng})),strokeColor:'#d93025',strokeWeight:3,fillColor:'#d93025',fillOpacity:.12});
          subscriptions.push(buildingChecks.register(point,checked=>layer.setOptions({strokeColor:checked?'#1a73e8':'#d93025',fillColor:checked?'#1a73e8':'#d93025'})));
          const popup=new google.maps.InfoWindow({content:info});layer.addListener('click',event=>{buildingChecks.toggle(point);popup.setPosition(event.latLng);popup.open(map);});layers.push(layer);
        }else{
          const layer=L.polygon(point.outline,{color:'#d93025',weight:3,fillOpacity:.12}).addTo(map);layer.bindPopup(info);
          subscriptions.push(buildingChecks.register(point,checked=>layer.setStyle({color:checked?'#1a73e8':'#d93025'})));layer.on('click',()=>buildingChecks.toggle(point));layers.push(layer);
        }
      }
    } catch (_) { /* Keep existing outlines when the connection is temporarily unavailable. */ }
  }
  window.addEventListener('current-compare-updated',event=>{localPoints=event.detail||[];schedule();});
  let retries = 0;
  const initialize=()=>{
    if(typeof map==='undefined'||!map){if(retries++<120)setTimeout(initialize,250);return;}
    if(googleView)map.addListener('idle',schedule);else map.on('moveend',schedule);
    setInterval(()=>{if(!document.hidden)schedule();},30000);
    schedule();
  };
  initialize();
})();
