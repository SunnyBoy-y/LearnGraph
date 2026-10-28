/* Public composer contract first; old servers expose only existing-session storage. */
(function () {
  const route = location.pathname.match(/^\/w\/([^/]+)\/chat\/([^/]+)$/);
  const result = { url: location.href, draft: document.querySelector('textarea[aria-label="输入消息"]')?.value ?? null, composer: null };
  if (!route) return JSON.stringify(result);
  const scope = { workspaceId: decodeURIComponent(route[1]), sessionId: decodeURIComponent(route[2]) };
  const bridge = window.LearnGraphComposer;
  const sync = window.__lgComposerSync;
  let outboundApplied = sync?.outboundApplied === true;
  let prefs = null, source = 'unavailable';
  if (bridge?.version === 1) {
    const snapshot = bridge.snapshot(scope);
    if (snapshot?.ready) {
      const requested = sync?.workspaceId === scope.workspaceId && sync.requestedPrefs;
      // React may commit before the handoff's next polling tick. Its request ID
      // is authoritative; an empty draft acknowledgement alone confirms no prefs.
      if (requested && typeof sync.requestId === 'string' && sync.requestId && snapshot.appliedRequestId === sync.requestId) outboundApplied = true;
      if (requested && !outboundApplied) source = 'live-unconfirmed';
      else { prefs = snapshot.prefs; source = 'live'; }
    }
  } else if (scope.sessionId !== 'new') {
    try {
      const stored = JSON.parse(localStorage.getItem('learngraph:session-composer-prefs') || '{}')[scope.sessionId];
      if (stored && typeof stored === 'object' && !Array.isArray(stored)) { prefs = stored; source = 'storage'; }
      if (prefs && sync?.workspaceId === scope.workspaceId && sync.sessionId === scope.sessionId && sync.requestedPrefs && !sync.outboundApplied) {
        // Do not replace native selections with untouched, stale Web storage.
        // Import only fields the Web changed after opening the old server.
        const keys = ['responseMode', 'thinkingMode', 'searchRoute', 'generationMode', 'providerId', 'modelId', 'imageProviderId', 'imageModelId'];
        const before = sync.initialPrefs;
        const changed = before ? keys.filter(key => (before[key] ?? null) !== (prefs[key] ?? null)) : [];
        if (before && changed.length) {
          const merged = { ...sync.requestedPrefs };
          changed.forEach(key => { merged[key] = prefs[key] ?? null; });
          prefs = merged; source = 'legacy-merge';
        } else { prefs = null; source = before ? 'legacy-unchanged' : 'legacy-unavailable'; }
      }
    } catch { /* Invalid storage does not change native preferences. */ }
  }
  result.composer = { version: 1, ...scope, prefs, source, limited: source !== 'live', outboundApplied };
  return JSON.stringify(result);
})()
