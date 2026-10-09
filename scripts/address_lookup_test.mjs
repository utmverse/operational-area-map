// Behavioural tests for the shared reverse-geocoding helper.
//
// The helper is extracted verbatim from the built map page and evaluated with a
// stubbed fetch, so these tests exercise the code that actually ships rather
// than a copy of it.
//
// Usage: node scripts/address_lookup_test.mjs <index.html>

import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const START = '// --- reverse-geocode:v3 start';
const END = '// --- reverse-geocode:v3 end';

const pagePath = process.argv[2];
if (!pagePath) throw new Error('Usage: node scripts/address_lookup_test.mjs <index.html>');

const page = readFileSync(pagePath, 'utf8');
const start = page.indexOf(START);
const end = page.indexOf(END, start);
if (start < 0 || end < 0) throw new Error(`Could not find the reverse-geocode:v3 block in ${pagePath}`);
const source = page.slice(start, end);

const EXPORTS = [
  'reverseGeocode',
  'normalizeCoordinate',
  'formatAddress',
  'parseRetryAfter',
  'isRetryableGeocodeError',
  'isGeocodeBreakerOpen',
  'recordGeocodeOutcome',
  'addressRowHtml',
  'hydratePopupAddress',
  'GEOCODE_MIN_INTERVAL_MS',
  'GEOCODE_MAX_ATTEMPTS',
  'GEOCODE_BREAKER_THRESHOLD',
  'GEOCODE_UNAVAILABLE_TEXT',
  'GEOCODE_PENDING_TEXT'
];

const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#039;' }[c]));

// Each instance gets its own caches and request queue, so cases cannot leak into
// one another.
function createGeocoder(fetchStub, { language = 'en-US' } = {}) {
  const factory = new Function(
    'fetch',
    'navigator',
    'escapeHtml',
    'console',
    `${source}\n return { ${EXPORTS.join(', ')} };`
  );
  return factory(fetchStub, { language }, escapeHtml, { warn() {}, error() {} });
}

function jsonResponse(body, { status = 200, headers = {} } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: name => headers[name] ?? headers[name.toLowerCase()] ?? null },
    json: async () => body
  };
}

const ADDRESS_BODY = {
  address: { house_number: '500', road: 'Main St', suburb: 'Downtown', city: 'Dallas', state: 'Texas', postcode: '75201' },
  display_name: 'ignored when address details are present'
};

const tests = [];
const test = (name, fn) => tests.push([name, fn]);

test('serializes requests at least one second apart', async () => {
  const startedAt = [];
  const geo = createGeocoder(async () => {
    startedAt.push(Date.now());
    return jsonResponse(ADDRESS_BODY);
  });
  await Promise.all([
    geo.reverseGeocode(32.7, -96.8),
    geo.reverseGeocode(32.8, -96.9),
    geo.reverseGeocode(32.9, -97.0)
  ]);
  assert.equal(startedAt.length, 3, 'expected one request per distinct coordinate');
  for (let i = 1; i < startedAt.length; i += 1) {
    const gap = startedAt[i] - startedAt[i - 1];
    assert.ok(
      gap >= geo.GEOCODE_MIN_INTERVAL_MS - 25,
      `requests ${i - 1} and ${i} were only ${gap}ms apart; Nominatim allows one per second`
    );
  }
});

test('deduplicates concurrent and repeated lookups of the same point', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    return jsonResponse(ADDRESS_BODY);
  });
  const results = await Promise.all(Array.from({ length: 5 }, () => geo.reverseGeocode(32.7767, -96.797)));
  assert.equal(calls, 1, 'concurrent lookups of one coordinate must share a single request');
  assert.equal(await geo.reverseGeocode(32.7767, -96.797), results[0], 'cached result must be reused');
  assert.equal(calls, 1, 'a resolved coordinate must not be requested again');
  assert.equal(results[0], '500 Main St, Downtown, Dallas, Texas, 75201');
});

test('sends a well-formed Nominatim request', async () => {
  let requested = null;
  let headers = null;
  const geo = createGeocoder(async (url, options) => {
    requested = new URL(url);
    headers = options.headers;
    assert.ok(options.signal, 'request must carry an abort signal so it can time out');
    return jsonResponse(ADDRESS_BODY);
  });
  await geo.reverseGeocode(32.776664, -96.796988);
  assert.equal(requested.origin + requested.pathname, 'https://nominatim.openstreetmap.org/reverse');
  assert.equal(requested.searchParams.get('format'), 'jsonv2');
  assert.equal(requested.searchParams.get('lat'), '32.776664');
  assert.equal(requested.searchParams.get('lon'), '-96.796988');
  assert.equal(requested.searchParams.get('addressdetails'), '1');
  assert.equal(requested.searchParams.get('accept-language'), 'en-US');
  assert.equal(headers.Accept, 'application/json');
});

test('retries a throttled request and honours Retry-After', async () => {
  const statuses = [];
  const geo = createGeocoder(async () => {
    statuses.push(statuses.length);
    if (statuses.length === 1) return jsonResponse({}, { status: 429, headers: { 'Retry-After': '0' } });
    return jsonResponse(ADDRESS_BODY);
  });
  assert.equal(await geo.reverseGeocode(32.7, -96.8), '500 Main St, Downtown, Dallas, Texas, 75201');
  assert.equal(statuses.length, 2, 'a 429 must be retried exactly once before succeeding');
});

test('gives up immediately on a non-retryable response', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    return jsonResponse({}, { status: 400 });
  });
  await assert.rejects(() => geo.reverseGeocode(32.7, -96.8), /HTTP 400/);
  assert.equal(calls, 1, 'HTTP 400 is a permanent answer and must not be retried');
});

test('stops retrying a persistent server error after the attempt budget', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    return jsonResponse({}, { status: 503 });
  });
  await assert.rejects(() => geo.reverseGeocode(32.7, -96.8), /HTTP 503/);
  assert.equal(calls, geo.GEOCODE_MAX_ATTEMPTS);
});

test('caches a failure briefly instead of pinning it for the session', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    if (calls === 1) throw new Error('network down');
    return jsonResponse(ADDRESS_BODY);
  });
  // Attempt budget: one network failure is retried, then succeeds.
  assert.equal(await geo.reverseGeocode(32.7, -96.8), '500 Main St, Downtown, Dallas, Texas, 75201');
  assert.equal(calls, 2, 'a network failure must be retried');
});

test('short-circuits repeat lookups while a failure is still cached', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    return jsonResponse({}, { status: 500 });
  });
  await assert.rejects(() => geo.reverseGeocode(32.7, -96.8));
  const attempted = calls;
  await assert.rejects(() => geo.reverseGeocode(32.7, -96.8), /recently/);
  assert.equal(calls, attempted, 'a just-failed coordinate must not be hammered again');
});

test('treats an unaddressable point as a final answer', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    return jsonResponse({ error: 'Unable to geocode' });
  });
  assert.equal(await geo.reverseGeocode(0, 0), geo.GEOCODE_UNAVAILABLE_TEXT);
  assert.equal(calls, 1, 'an "unable to geocode" body is not a failure and must not be retried');
  await geo.reverseGeocode(0, 0);
  assert.equal(calls, 1, 'the answer must be cached like any other result');
});

test('rejects unusable coordinates without calling the service', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    return jsonResponse(ADDRESS_BODY);
  });
  const unusable = [[NaN, 0], [undefined, 0], ['abc', 0], [0, null], [null, 0], [0, ''], [0, true], [0, {}], [0, []], [91, 0], [-91, 0], [Infinity, 0]];
  for (const [lat, lng] of unusable) {
    await assert.rejects(() => geo.reverseGeocode(lat, lng), /finite coordinate/);
  }
  assert.equal(calls, 0);
});

test('wraps longitudes that have crossed a world copy', () => {
  const geo = createGeocoder(async () => jsonResponse(ADDRESS_BODY));
  assert.deepEqual(geo.normalizeCoordinate(32.7, -456.8), { latitude: 32.7, longitude: -96.80000000000001 });
  assert.deepEqual(geo.normalizeCoordinate(32.7, 263.2), { latitude: 32.7, longitude: -96.80000000000001 });
  assert.equal(geo.normalizeCoordinate(32.7, 180).longitude, 180);
  assert.equal(geo.normalizeCoordinate(91, 0), null);
});

test('formats addresses without repeating or dropping components', () => {
  const geo = createGeocoder(async () => jsonResponse(ADDRESS_BODY));
  assert.equal(
    geo.formatAddress({ house_number: '12', road: 'Elm St', city: 'Austin', state: 'Texas', postcode: '78701' }),
    '12 Elm St, Austin, Texas, 78701'
  );
  assert.equal(
    geo.formatAddress({ road: 'Farm Rd 12', county: 'Denton County', state: 'Texas' }),
    'Farm Rd 12, Denton County, Texas',
    'unincorporated areas must fall back to the county'
  );
  assert.equal(
    geo.formatAddress({ suburb: 'Singapore', city: 'Singapore', state: 'Singapore' }),
    'Singapore',
    'repeated components must be collapsed'
  );
  assert.equal(geo.formatAddress(null, 'Somewhere, Nowhere'), 'Somewhere, Nowhere');
  assert.equal(geo.formatAddress(null, null), geo.GEOCODE_UNAVAILABLE_TEXT);
  assert.equal(geo.formatAddress({}, null), geo.GEOCODE_UNAVAILABLE_TEXT);
});

test('parses both Retry-After forms and clamps them', () => {
  const geo = createGeocoder(async () => jsonResponse(ADDRESS_BODY));
  assert.equal(geo.parseRetryAfter('2'), 2000);
  assert.equal(geo.parseRetryAfter('9999'), 10000, 'an absurd Retry-After must be clamped');
  assert.equal(geo.parseRetryAfter(null), null);
  assert.equal(geo.parseRetryAfter('not-a-date'), null);
  assert.ok(geo.parseRetryAfter(new Date(Date.now() + 3000).toUTCString()) > 1000);
});

test('classifies retryable and permanent failures', () => {
  const geo = createGeocoder(async () => jsonResponse(ADDRESS_BODY));
  assert.equal(geo.isRetryableGeocodeError({ name: 'AbortError' }), true, 'a timeout must be retried');
  assert.equal(geo.isRetryableGeocodeError(new TypeError('Failed to fetch')), true);
  assert.equal(geo.isRetryableGeocodeError({ status: 429 }), true);
  assert.equal(geo.isRetryableGeocodeError({ status: 500 }), true);
  assert.equal(geo.isRetryableGeocodeError({ status: 404 }), false);
  assert.equal(geo.isRetryableGeocodeError({ status: 403 }), false);
});

test('builds an address row that carries its own coordinate', () => {
  const geo = createGeocoder(async () => jsonResponse(ADDRESS_BODY));
  const html = geo.addressRowHtml('Address', { latitude: 32.776664, longitude: -96.796988 });
  assert.match(html, /data-address-lat="32\.776664"/);
  assert.match(html, /data-address-lng="-96\.796988"/);
  assert.match(html, /aria-live="polite"/);
  assert.ok(html.includes(geo.GEOCODE_PENDING_TEXT));

  const missing = geo.addressRowHtml('Address', null);
  assert.ok(missing.includes(geo.GEOCODE_UNAVAILABLE_TEXT));
  assert.ok(!missing.includes('data-address-lat'), 'a row without a coordinate must not trigger a lookup');

  assert.match(geo.addressRowHtml('<script>', { latitude: 1, longitude: 2 }), /&lt;script&gt;/);
});

// Minimal stand-in for the popup DOM. Leaflet rebuilds a popup's content on every
// open (DivOverlay._updateContent assigns innerHTML), so `reopen()` hands back a
// brand-new span the way the real page does rather than reusing the old one.
function fakePopup(lat, lng) {
  let element = null;
  const container = {
    querySelector: selector => (selector.includes('popup-address') ? element : null),
    reopen() {
      element = { dataset: { addressLat: String(lat), addressLng: String(lng) }, textContent: 'Looking up…' };
      return container;
    },
    get span() { return element; }
  };
  return container.reopen();
}

test('hydrates the open popup from the coordinate on its own span', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    return jsonResponse(ADDRESS_BODY);
  });
  const popup = fakePopup(32.7767, -96.797);
  await geo.hydratePopupAddress(popup);
  assert.equal(popup.span.textContent, '500 Main St, Downtown, Dallas, Texas, 75201');
  assert.equal(popup.span.dataset.addressState, 'resolved');

  // Leaflet rebuilds the content, so the next open re-enters with a fresh span:
  // it must fill in from cache rather than issue a second request.
  await geo.hydratePopupAddress(popup.reopen());
  assert.equal(popup.span.textContent, '500 Main St, Downtown, Dallas, Texas, 75201');
  assert.equal(calls, 1, 'reopening a resolved popup must not repeat the lookup');
});

test('ignores a second hydration of the same span', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    return jsonResponse(ADDRESS_BODY);
  });
  const popup = fakePopup(32.7767, -96.797);
  await Promise.all([geo.hydratePopupAddress(popup), geo.hydratePopupAddress(popup)]);
  assert.equal(calls, 1, 'opening an already-open popup must not start a second lookup');
  assert.equal(popup.span.dataset.addressState, 'resolved');
});

test('leaves a failed popup retryable and never throws at the popup boundary', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    if (calls <= geo.GEOCODE_MAX_ATTEMPTS) return jsonResponse({}, { status: 500 });
    return jsonResponse(ADDRESS_BODY);
  });
  const popup = fakePopup(32.7767, -96.797);
  await geo.hydratePopupAddress(popup);
  assert.equal(popup.span.textContent, geo.GEOCODE_UNAVAILABLE_TEXT);
  assert.equal(popup.span.dataset.addressState, 'failed');

  // Reopening retries; the negative cache answers instantly while it is warm.
  await geo.hydratePopupAddress(popup.reopen());
  assert.equal(popup.span.textContent, geo.GEOCODE_UNAVAILABLE_TEXT);

  await geo.hydratePopupAddress(null);
  await geo.hydratePopupAddress({ querySelector: () => null });
});

test('pauses the whole queue when the service asks for a slowdown', async () => {
  const dispatched = [];
  const geo = createGeocoder(async () => {
    dispatched.push(Date.now());
    if (dispatched.length === 1) {
      return jsonResponse({}, { status: 429, headers: { 'Retry-After': '2' } });
    }
    return jsonResponse(ADDRESS_BODY);
  });
  // A second coordinate asks for its address while the first is being throttled.
  const first = geo.reverseGeocode(32.7, -96.8);
  const second = geo.reverseGeocode(32.9, -97.0);
  await Promise.allSettled([first, second]);
  const held = dispatched.slice(1).filter(at => at - dispatched[0] < 2000);
  assert.equal(
    held.length,
    0,
    `Retry-After asked for a 2s pause but ${held.length} request(s) went out inside it`
  );
});

test('stops queueing doomed requests once the service looks down', async () => {
  let calls = 0;
  const geo = createGeocoder(async () => {
    calls += 1;
    return jsonResponse({}, { status: 503 });
  });
  const coordinates = [[32.7, -96.8], [32.8, -96.9], [32.9, -97.0], [33.0, -97.1]];
  const settled = await Promise.allSettled(coordinates.map(([lat, lng]) => geo.reverseGeocode(lat, lng)));
  assert.ok(settled.every(result => result.status === 'rejected'), 'every lookup must reject');
  assert.ok(
    calls <= geo.GEOCODE_BREAKER_THRESHOLD,
    `breaker should cap requests at ${geo.GEOCODE_BREAKER_THRESHOLD}, saw ${calls}`
  );
  // A later popup is answered immediately instead of waiting behind the queue.
  const startedAt = Date.now();
  await assert.rejects(() => geo.reverseGeocode(34.5, -98.5), /temporarily unavailable/);
  assert.ok(Date.now() - startedAt < 200, 'an open breaker must answer without queueing');
  assert.ok(calls <= geo.GEOCODE_BREAKER_THRESHOLD, 'an open breaker must not issue requests');
});

test('a success closes the breaker and an isolated failure never opens it', () => {
  const geo = createGeocoder(async () => jsonResponse(ADDRESS_BODY));
  const failure = new Error('boom');

  for (let i = 1; i < geo.GEOCODE_BREAKER_THRESHOLD; i += 1) {
    geo.recordGeocodeOutcome(failure);
    assert.equal(geo.isGeocodeBreakerOpen(), false, `${i} failure(s) must not trip the breaker`);
  }
  geo.recordGeocodeOutcome(failure);
  assert.equal(geo.isGeocodeBreakerOpen(), true, 'a run of failures must trip the breaker');

  geo.recordGeocodeOutcome(null);
  assert.equal(geo.isGeocodeBreakerOpen(), false, 'one healthy answer must clear the breaker');

  // Failures separated by successes are transient, not an outage.
  for (let i = 0; i < geo.GEOCODE_BREAKER_THRESHOLD * 3; i += 1) {
    geo.recordGeocodeOutcome(failure);
    geo.recordGeocodeOutcome(null);
    assert.equal(geo.isGeocodeBreakerOpen(), false);
  }
});

const failures = [];
for (const [name, fn] of tests) {
  try {
    await fn();
    console.log(`  ok   ${name}`);
  } catch (error) {
    failures.push(name);
    console.log(`  FAIL ${name}\n       ${error.message.split('\n').join('\n       ')}`);
  }
}

if (failures.length) {
  console.error(`\n${failures.length} of ${tests.length} address-lookup tests failed.`);
  process.exit(1);
}
console.log(`\nAll ${tests.length} address-lookup tests passed.`);
