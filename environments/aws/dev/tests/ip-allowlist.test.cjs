const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(
  path.join(__dirname, '../functions/ip-allowlist.js.tftpl'), 'utf8'
);
function createHandler(addresses) {
  const code = template.replace('${allowed_ips}', JSON.stringify(addresses));
  return vm.runInNewContext(code + '\nhandler;');
}
const handler = createHandler(['203.0.113.10']);

function event(ip, uri = '/health', method = 'GET', headers = {}) {
  return { viewer: { ip }, request: { uri, method, headers } };
}
function assertDenied(input, run = handler) {
  const response = run(input);
  assert.equal(response.statusCode, 403);
  assert.equal(response.headers['cache-control'].value, 'no-store');
  assert.equal(response.body, 'Forbidden');
}

test('the allowed connection retains the entire original request', () => {
  const input = event('203.0.113.10', '/api/v1/auth/token', 'POST', {
    authorization: { value: 'Bearer example' }
  });
  input.request.body = { data: 'example-form-body' };
  assert.equal(handler(input), input.request);
});

test('all app paths and methods deny other connections, including health and sign-up', () => {
  for (const uri of ['/health', '/ready', '/api/v1/docs/', '/api/v1/auth/register',
    '/api/v1/auth/token', '/api/v1/items']) {
    for (const method of ['GET', 'HEAD', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']) {
      assertDenied(event('198.51.100.20', uri, method));
    }
  }
});

test('spoofed forwarded and client IP headers do not bypass the connection check', () => {
  assertDenied(event('198.51.100.20', '/health', 'GET', {
    'x-forwarded-for': { value: '203.0.113.10' },
    'x-real-ip': { value: '203.0.113.10' },
    'cloudfront-viewer-address': { value: '203.0.113.10:443' },
    'forwarded': { value: 'for=203.0.113.10' }
  }));
});

test('matching requires the complete address, not a prefix or a substring', () => {
  for (const ip of ['203.0.113.1', '203.0.113.100', '203.0.113.11', '203.0.113.10.evil']) {
    assertDenied(event(ip));
  }
});

test('IPv6 and IPv4-mapped IPv6 addresses fail closed', () => {
  for (const ip of ['2001:db8::10', '::ffff:203.0.113.10', '::1']) {
    assertDenied(event(ip));
  }
});

test('missing viewer information and an empty allowlist fail closed', () => {
  assertDenied({ request: {} });
  assertDenied(event(undefined));
  assertDenied(event(''));
  assertDenied(event('203.0.113.10'), createHandler([]));
});

test('each configured exact address can be allowed', () => {
  const run = createHandler(['203.0.113.10', '198.51.100.20']);
  for (const ip of ['203.0.113.10', '198.51.100.20']) {
    const input = event(ip);
    assert.equal(run(input), input.request);
  }
  assertDenied(event('192.0.2.5'), run);
});
