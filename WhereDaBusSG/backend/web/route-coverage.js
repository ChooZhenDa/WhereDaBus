/* Route coverage is data quality, not a promise that a bus is operating now. */
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const count = value => value !== null && value !== '' && value !== undefined && Number.isSafeInteger(Number(value)) && Number(value) >= 0 ? Number(value) : null;
const number = value => Number(value).toLocaleString('en-SG');
const examples = (rows, total) => {
  const list = Array.isArray(rows) ? rows : [];
  return list.length ? list.map(row => `${escape(row?.serviceNo)} · direction ${escape(row?.direction)}`).join('; ') + (total > list.length ? '; and more' : '') : '';
};

export function routeCoverage(coverage) {
  if (!coverage) return '';
  if (coverage.status !== 'available') return '<div class="notice route-coverage-note"><span>Route coverage could not be checked. These results use imported route data. <a href="#status">Check Help &amp; status</a>.</span></div>';
  const available = count(coverage.availableDirections), total = count(coverage.serviceDirections);
  const partial = count(coverage.partialDirections), gapFree = count(coverage.gapFreeDirections), missing = count(coverage.unavailableDirections);
  const current = partial !== null && gapFree !== null && available === partial + gapFree;
  const label = available === 0 ? 'No usable route directions imported' : `${number(available ?? 0)} of ${number(total ?? 0)} directions have route data`;
  const summary = current ? `${label}${partial ? ` · ${number(partial)} partial` : ''}${missing ? ` · ${number(missing)} unavailable` : ''}` : `${label} · coverage details limited`;
  const text = current
    ? `${number(gapFree)} direction${gapFree === 1 ? ' has' : 's have'} no detected gaps; ${number(partial)} ${partial === 1 ? 'has' : 'have'} partial data. Missing results do not prove that no service exists. Route data can change after import.`
    : 'This server does not report partial-route coverage. Some journeys may be missing. Update the app and run Refresh-Data.cmd to retain usable sections of incomplete routes.';
  const partialList = examples(coverage.partialExamples, partial), missingList = examples(coverage.unavailableExamples, missing);
  let timestamp = '';
  if (coverage.lastImportAt) {
    const date = new Date(coverage.lastImportAt);
    if (!Number.isNaN(date.getTime())) timestamp = `<p>Last import: ${escape(new Intl.DateTimeFormat('en-SG',{timeZone:'Asia/Singapore',dateStyle:'medium',timeStyle:'short'}).format(date))} SGT.</p>`;
  }
  return `<details class="route-coverage"><summary>${escape(summary)}</summary><p>${escape(text)}</p>${partialList ? `<p><strong>Partial data:</strong> ${partialList}</p>` : ''}${missingList ? `<p><strong>No usable route data:</strong> ${missingList}</p>` : ''}<p>Run Refresh-Data.cmd to check for newer route data. A refresh cannot recreate stops absent from the source.</p>${timestamp}</details>`;
}

export function routeFacts(row, type) {
  const direct = type === 'direct';
  const segmentPartial = row.segment_coverage === 'partial';
  const routePartial = row.route_coverage === 'partial';
  const partial = direct ? segmentPartial : routePartial;
  let message = '';
  if (partial) message = direct
    ? 'Both selected stops occur in this order, but intermediate route data is incomplete. Stop count and distance are unavailable; verify this journey before travelling.'
    : 'This service has incomplete route data. The endpoints below are the first and last recorded stops; full-route totals are unavailable.';
  else if (direct && routePartial) message = 'No gaps were detected in your selected section. Other parts of this service have incomplete data.';
  const distance = partial ? null : direct ? (row.distance_km ?? row.travel_distance_km) : (row.route_length_km ?? row.distance_km);
  // Do not let stale or inconsistent fields claim exact totals for partial data.
  const details = Object.entries(row).filter(([key]) => !['_id','source','segment_coverage','route_coverage'].includes(key))
    .filter(([key]) => !(direct && ['route_length_km','stop_count','observed_stop_count','known_distance_span_km'].includes(key)))
    .map(([key,value]) => [key, partial && ['distance_km','travel_distance_km','route_length_km','stop_count','stops_between'].includes(key) ? null : value]);
  return {partial, message, distance, details};
}
