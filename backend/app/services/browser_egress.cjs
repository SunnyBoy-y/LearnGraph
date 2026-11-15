// Fixed sandbox-local forwarder. Chromium never connects outside the approved
// egress proxy; the policy digest is added to the proxy hop, not the webpage.
const http = require('node:http');
const net = require('node:net');

async function startApprovedProxy(rawProxy, digest, allowed) {
  const upstream = new URL(rawProxy);
  if (upstream.protocol !== 'http:' || !upstream.hostname || !/^[A-Za-z0-9._-]{1,128}$/.test(digest)) {
    throw new Error('approved_browser_egress_required');
  }
  const hostname = upstream.hostname.replace(/^\[|\]$/g, '');
  const port = Number(upstream.port || 80);
  const sockets = new Set();
  const track = socket => {
    sockets.add(socket);
    socket.setTimeout(25000, () => socket.destroy());
    socket.once('close', () => sockets.delete(socket));
    return socket;
  };
  const server = http.createServer((req, res) => {
    if (!['GET', 'HEAD'].includes(req.method) || !allowed(req.url)) {
      res.writeHead(403); res.end(); return;
    }
    const headers = {...req.headers, host: new URL(req.url).host,
      'x-learngraph-policy-digest': digest};
    delete headers['proxy-authorization'];
    delete headers.authorization;
    const outbound = http.request({hostname, port, method: req.method, path: req.url, headers}, response => {
      res.writeHead(response.statusCode || 502, response.headers);
      response.pipe(res);
    });
    outbound.setTimeout(25000, () => outbound.destroy());
    outbound.on('socket', track);
    outbound.on('error', () => { if (!res.headersSent) res.writeHead(502); res.end(); });
    req.on('aborted', () => outbound.destroy());
    req.pipe(outbound);
  });
  server.on('connection', track);
  server.on('connect', (req, client, head) => {
    if (!allowed('https://' + req.url) || /[\r\n]/.test(req.url || '')) {
      client.end('HTTP/1.1 403 Forbidden\r\n\r\n'); return;
    }
    const remote = track(net.connect({host: hostname, port}));
    remote.once('connect', () => {
      remote.write(`CONNECT ${req.url} HTTP/1.1\r\nHost: ${req.url}\r\nX-LearnGraph-Policy-Digest: ${digest}\r\n\r\n`);
    });
    let buffer = Buffer.alloc(0);
    const handshake = chunk => {
      buffer = Buffer.concat([buffer, chunk]);
      if (buffer.length > 16384) { remote.destroy(); client.destroy(); return; }
      const end = buffer.indexOf('\r\n\r\n');
      if (end < 0) return;
      remote.removeListener('data', handshake);
      if (!/^HTTP\/1\.[01] 200(?: |\r|$)/.test(buffer.toString('latin1', 0, end))) {
        remote.destroy(); client.end('HTTP/1.1 502 Bad Gateway\r\n\r\n'); return;
      }
      client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
      if (buffer.length > end + 4) client.write(buffer.subarray(end + 4));
      if (head.length) remote.write(head);
      client.pipe(remote); remote.pipe(client);
    };
    remote.on('data', handshake);
    remote.on('error', () => client.destroy());
    client.on('error', () => remote.destroy());
    client.on('close', () => remote.destroy());
    remote.on('close', () => client.destroy());
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  return {
    server: `http://127.0.0.1:${server.address().port}`,
    close: () => new Promise(resolve => {
      for (const socket of sockets) socket.destroy();
      server.close(resolve);
    }),
  };
}
module.exports = {startApprovedProxy};
