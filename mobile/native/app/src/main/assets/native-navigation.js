/* React Router already implements lazy-history message focus from location.state. */
(function () {
  const url = new URL(location.href);
  const messageId = url.searchParams.get('message_id');
  if (!messageId || !/^[A-Za-z0-9_-]{1,100}$/.test(messageId) || !/^\/w\/[^/]+\/chat\/[^/]+$/.test(url.pathname)) return;
  url.searchParams.delete('message_id');
  const previous = history.state || {};
  const state = { ...previous, usr: { ...(previous.usr || {}), targetMessageId: messageId } };
  history.replaceState(state, '', url.pathname + url.search + url.hash);
  dispatchEvent(new PopStateEvent('popstate', { state }));
})();
