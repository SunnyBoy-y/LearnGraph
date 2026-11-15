const {test} = require('node:test');
const assert = require('node:assert/strict');
const http = require('node:http');
const net = require('node:net');
const {startApprovedProxy} = require('../../app/services/browser_egress.cjs');

const digest = 'a'.repeat(64);
const allowed = value => {
  try { return new URL(value).hostname === 'approved.example'; } catch { return false; }
};
const listen = server => new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const close = server => new Promise(resolve => server.close(resolve));

test('HTTP requests carry the policy identity only to the configured upstream proxy', async () => {
  const seen = [];
  const upstream = http.createServer((req, res) => {
    seen.push({url: req.url, method: req.method, headers: req.headers});
    res.end('approved result');
  });
  await listen(upstream);
  const proxy = await startApprovedProxy(`http://127.0.0.1:${upstream.address().port}`, digest, allowed);
  try {
    const result = await new Promise((resolve, reject) => {
      // Actual proxy clients use an absolute request-target.
      const url = new URL(proxy.server);
      const req = http.request({hostname: url.hostname, port: url.port, path: 'http://approved.example/page',
        headers: {'Proxy-Authorization': 'do-not-forward', Authorization: 'do-not-forward'}}, response => {
        let body = ''; response.on('data', chunk => body += chunk); response.on('end', () => resolve(body));
      });
      req.on('error', reject); req.end();
    });
    assert.equal(result, 'approved result');
    assert.equal(seen.length, 1);
    assert.equal(seen[0].headers['x-learngraph-policy-digest'], digest);
    assert.equal(seen[0].headers.authorization, undefined);
    assert.equal(seen[0].headers['proxy-authorization'], undefined);
  } finally { await proxy.close(); await close(upstream); }
});

test('HTTPS CONNECT authenticates the approved proxy and tunnels only approved hosts', async () => {
  const seen = [];
  const sockets = new Set();
  const upstream = http.createServer();
  upstream.on('connection', socket => { sockets.add(socket); socket.on('close', () => sockets.delete(socket)); });
  upstream.on('connect', (request, socket) => {
    seen.push({target: request.url, digest: request.headers['x-learngraph-policy-digest']});
    socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');
    socket.on('data', data => socket.write(data));
  });
  await listen(upstream);
  const proxy = await startApprovedProxy(`http://127.0.0.1:${upstream.address().port}`, digest, allowed);
  async function connect(host, payload) {
    const address = new URL(proxy.server);
    return new Promise((resolve, reject) => {
      const socket = net.connect({host: address.hostname, port: Number(address.port)});
      socket.setTimeout(5000, () => socket.destroy(new Error('test timeout')));
      socket.on('error', reject);
      socket.once('connect', () => socket.write(`CONNECT ${host}:443 HTTP/1.1\r\nHost: ${host}:443\r\n\r\n`));
      let data = '', sent = false;
      socket.on('data', chunk => {
        data += chunk.toString();
        if (data.includes('403')) { socket.destroy(); resolve(data); return; }
        if (data.includes('200') && !sent) { sent = true; socket.write(payload); }
        if (sent && data.includes(payload)) { socket.destroy(); resolve(data); }
      });
    });
  }
  try {
    assert.match(await connect('approved.example', 'fixture tunnel bytes'), /fixture tunnel bytes/);
    assert.match(await connect('outside.example', 'must not leave'), /403/);
    assert.deepEqual(seen, [{target: 'approved.example:443', digest}]);
  } finally {
    await proxy.close();
    for (const socket of sockets) socket.destroy();
    await close(upstream);
  }
});

test('a missing policy identity cannot create a direct-connect fallback', async () => {
  await assert.rejects(startApprovedProxy('http://127.0.0.1:9', '', allowed), /approved_browser_egress_required/);
  await assert.rejects(startApprovedProxy('https://127.0.0.1:9', digest, allowed), /approved_browser_egress_required/);
});
