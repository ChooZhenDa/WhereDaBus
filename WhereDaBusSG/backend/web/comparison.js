import {escapeHTML as esc, formatNumber, formatSG} from './insights-model.js';

const capacity = {
  SEA: ['Seats available', 'seats'],
  SDA: ['Standing available', 'standing'],
  LSD: ['Limited standing', 'limited'],
};
const noTime = {
  stale: 'Last arrival update is stale',
  unavailable: 'Live arrivals unavailable',
  no_prediction: 'No boarding estimate',
  unconfirmed: 'Could not confirm this bus serves your destination',
};

export function comparisonFacts(row, asOf) {
  const {live, history, fare} = row;
  const eta = Date.parse(live?.eta);
  const delay = eta - Date.parse(asOf);
  let boarding = '<p class="fact-value">' + esc(noTime[live?.status] || 'No boarding estimate') + '</p>';
  if (live?.status === 'available' && Number.isFinite(delay) && delay >= 0) {
    const [label, color] = capacity[live.load] || ['Capacity unknown', ''];
    const wait = delay < 60000 ? 'Arriving soon' : Math.ceil(delay / 60000) + ' min';
    boarding = `<p class="fact-value">${esc(wait)}</p>
      <p class="fact-note">${esc(formatSG(eta))} SGT${live.monitored === 0 ? ' · Scheduled' : ''}</p>
      <p class="comparison-load"><i class="load-dot ${color}" aria-hidden="true"></i> ${label}</p>`;
  }
  let past = '<p class="fact-value">Historical data unavailable</p>';
  if (history?.status === 'limited' || history?.status === 'available') {
    const count = formatNumber(history.validSamples), days = formatNumber(history.daysWithValidSamples);
    const enough = history.status === 'available' && Number.isFinite(history.seatsAvailablePct);
    const result = enough ? formatNumber(history.seatsAvailablePct, 1) + '% of eligible observations reported seats available' : 'Limited history';
    const detail = history.observations === 0 ? 'No observations in the selected mornings.'
      : `${count} eligible of ${formatNumber(history.observations)} observations across ${days} weekday morning${history.daysWithValidSamples === 1 ? '' : 's'}`;
    past = `<p class="fact-value">${esc(result)}</p> <p class="fact-note">${esc(detail)}</p>`;
  }
  const quoted = fare?.status === 'available' && Number.isInteger(fare.cents) && fare.cents >= 0;
  const amount = quoted ? 'S$' + (fare.cents / 100).toFixed(2) : 'Fare unavailable';
  const note = quoted ? 'Estimated for one direct ride' : fare?.reason || 'A fare could not be calculated for this journey.';
  return `<dl class="comparison-facts">
    <div class="comparison-fact"><dt>Boarding estimate</dt> <dd>${boarding}</dd></div>
    <div class="comparison-fact"><dt>Adult card fare</dt> <dd><p class="fact-value">${esc(amount)}</p> <p class="fact-note">${esc(note)}</p></dd></div>
    <div class="comparison-fact"><dt>Weekday morning history</dt> <dd>${past}</dd></div>
  </dl>`;
}

export function comparisonHeading(snapshot, demo) {
  const {origin, destination, asOf, capturedAt, historyWindow} = snapshot;
  const first = snapshot.routes[0];
  const from = first?.origin_description || origin, to = first?.destination_description || destination;
  const recent = capturedAt ? ` · Arrival feed updated ${esc(formatSG(capturedAt))} SGT` : '';
  const period = historyWindow ? `<p class="field-hint">History for each service and operator at boarding stop ${esc(origin)}, across directions. Weekdays 07:00–08:59 SGT, ${esc(historyWindow.dateFrom)} to ${esc(historyWindow.dateTo)} inclusive.</p>` : '';
  return `<div class="comparison-heading"><div><h2>${esc(from)} → ${esc(to)}</h2>
    <p>${demo ? 'Synthetic demo' : 'Live data mode'} · Checked ${esc(formatSG(asOf))} SGT${recent}</p>${period}
    <p><a href="https://www.ptc.gov.sg/fares/public-transport-fares-and-passes/" target="_blank" rel="noopener">Public Transport Council fare table</a></p></div>
    <div class="comparison-actions"><button type="button" class="button secondary" data-compare-refresh>Refresh</button>
    <button type="button" class="button ghost" data-compare-export>Download snapshot</button></div></div>`;
}

export function downloadComparison(snapshot, demo) {
  const data = {
    source: demo ? 'SYNTHETIC demo' : 'WhereDaBus SQL routes and MongoDB arrivals and history',
    exportedAt: new Date().toISOString(),
    query: {origin: snapshot.origin, destination: snapshot.destination,
      date_from: snapshot.historyWindow?.dateFrom, date_to: snapshot.historyWindow?.dateTo},
    comparison: snapshot,
  };
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], {type: 'application/json'}));
  const link = document.createElement('a');
  link.href = url;
  link.download = `WhereDaBus-${demo ? 'DEMO-' : ''}${snapshot.origin}-${snapshot.destination}-${snapshot.asOf.replace(/[^\w-]/g, '-')}.json`;
  document.body.append(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
