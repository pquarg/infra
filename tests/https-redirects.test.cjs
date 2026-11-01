const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const handler = vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, '../functions/https-redirects.js'), 'utf8') + '\nhandler;'
);

function run(location, statusCode = 307, host = 'dexample.cloudfront.net') {
  const event = {
    request: { headers: host ? { host: { value: host } } : {} },
    response: { statusCode, headers: location ? { location: { value: location } } : {} },
  };
  const response = handler(event);
  assert.equal(response, event.response);
  assert.equal(response.statusCode, statusCode);
  return response.headers.location?.value;
}

test('slash redirects retain the viewer host, path, query and fragment over HTTPS', () => {
  assert.equal(run('http://dexample.cloudfront.net/api/v1/docs/?a=1#fragment'),
    'https://dexample.cloudfront.net/api/v1/docs/?a=1#fragment');
});

test('all ordinary redirect status codes are handled', () => {
  for (const code of [301, 302, 303, 307, 308]) {
    assert.equal(run('HTTP://DEXAMPLE.CLOUDFRONT.NET/path', code),
      'https://dexample.cloudfront.net/path');
  }
});

test('external authorities and misleading viewer-host prefixes are preserved', () => {
  for (const url of ['http://elsewhere.example/path',
    'http://dexample.cloudfront.net.evil.example/path',
    'http://attacker@dexample.cloudfront.net/path']) {
    assert.equal(run(url), url);
  }
});

test('relative, protocol-relative and HTTPS redirect locations are preserved', () => {
  for (const url of ['/api/v1/docs/', '//elsewhere.example/path',
    'https://dexample.cloudfront.net/api/v1/docs/']) {
    assert.equal(run(url), url);
  }
});

test('non-redirect responses and absent headers remain unchanged', () => {
  assert.equal(run('http://dexample.cloudfront.net/path', 200),
    'http://dexample.cloudfront.net/path');
  assert.equal(run(undefined), undefined);
  assert.equal(run('http://dexample.cloudfront.net/path', 307, null),
    'http://dexample.cloudfront.net/path');
});
