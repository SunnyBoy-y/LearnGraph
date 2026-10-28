/* Scoped public Web store relay. Native host authenticates each event and releases this owner on destroy. */
(function (request) {
  if (!request || window.top !== window || !request.owner) return;
  try { if (location.origin !== new URL(request.origin).origin) return; } catch { return; }
  if (window.__lgSidebarOwner === request.owner) { window.__lgSidebarSeed?.(request.seed); return; }
  window.__lgSidebarDispose?.();
  window.__lgSidebarOwner = request.owner;
  const scope = { token: request.token, workspaceId: request.workspaceId };
  let latestSeed = request.seed, appliedRevision = -1, sequence = 0, unsubscribe, timer, disposed = false;
  const pending = new Map();
  const started = Date.now();
  function send(event) {
    if (disposed) return;
    const number = ++sequence;
    if (event.sessionId) pending.set(event.sessionId, number);
    // A disconnected Android bridge must not break the original Web send/voice path.
    try {
      window.LearnGraphNative?.sidebarActivity?.(JSON.stringify({ version: 1, origin: location.origin,
        token: request.token, workspaceId: request.workspaceId, owner: request.owner, sequence: number, event }));
    } catch { /* the document owner is released by the host on teardown */ }
  }
  function seed(value) {
    if (disposed) return;
    if (typeof value === 'string') { try { value = JSON.parse(value); } catch { return; } }
    if (value) latestSeed = value;
    const bridge = window.LearnGraphSidebarActivity;
    if (bridge?.version !== 1 || !latestSeed || latestSeed.revision <= appliedRevision) return;
    const ack = latestSeed.acknowledged?.[request.owner] || 0;
    for (const [id, number] of pending) if (number <= ack) pending.delete(id);
    if (bridge.seed(scope, { activities: latestSeed.activities || {}, preserveIds: [...pending.keys()] })) appliedRevision = latestSeed.revision;
  }
  function connect() {
    if (disposed) return;
    const bridge = window.LearnGraphSidebarActivity;
    if (bridge?.version === 1) {
      seed();
      unsubscribe = bridge.subscribe(scope, send);
      if (unsubscribe) { clearInterval(timer); return; }
    }
    if (Date.now() - started > 15000) clearInterval(timer);
  }
  function dispose() {
    if (disposed) return;
    send({ kind: 'release' });
    disposed = true; clearInterval(timer); unsubscribe?.();
    window.removeEventListener('pagehide', dispose);
    if (window.__lgSidebarOwner === request.owner) {
      delete window.__lgSidebarOwner; delete window.__lgSidebarSeed; delete window.__lgSidebarDispose;
    }
  }
  window.__lgSidebarSeed = seed;
  window.__lgSidebarDispose = dispose;
  window.addEventListener('pagehide', dispose);
  timer = setInterval(connect, 100);
  connect();
})
