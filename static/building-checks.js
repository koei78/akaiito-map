(() => {
  const checked = new Set(), listeners = new Map(), pending = new Set();
  const keyOf = p => `${Number(p.lat).toFixed(7)},${Number(p.lon).toFixed(7)}`;
  const notify = key => { for (const fn of listeners.get(key) || []) fn(checked.has(key)); };
  const ready = fetch('/api/checked-buildings').then(response => {
    if (!response.ok) throw Error('確認済み情報を読み込めません');
    return response.json();
  }).then(keys => {keys.forEach(key => checked.add(key)); for (const key of listeners.keys()) notify(key);})
    .catch(() => {window.dispatchEvent(new CustomEvent('building-check-error', {detail:'確認済み情報を読み込めません。再読み込みしてください。'})); throw Error('読み込み失敗');});
  ready.catch(() => {});
  window.buildingChecks = {
    register(point, callback) {
      const key = keyOf(point);
      if (!listeners.has(key)) listeners.set(key, new Set());
      listeners.get(key).add(callback); callback(checked.has(key));
      return () => {const group=listeners.get(key);if(group){group.delete(callback);if(!group.size)listeners.delete(key);}};
    },
    async toggle(point) {
      const key = keyOf(point);
      if (pending.has(key)) return;
      pending.add(key);
      try {
        await ready;
        const next = !checked.has(key);
        const csrf = document.querySelector('meta[name="csrf-token"]').content;
        const response = await fetch('/api/checked-buildings', {method:'PUT', headers:{'Content-Type':'application/json','X-CSRF-Token':csrf}, body:JSON.stringify({key,checked:next})});
        if (!response.ok) throw Error('保存失敗');
        if (next) checked.add(key); else checked.delete(key);
        notify(key);
      } catch (_) {window.dispatchEvent(new CustomEvent('building-check-error', {detail:'確認状態を保存できませんでした。'}));}
      finally {pending.delete(key);}
    }
  };
})();
