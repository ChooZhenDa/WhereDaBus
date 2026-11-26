// Shared selection rules for the named historical-sampling controls.
export function samplingOptions(data) {
  const stops = [];
  const seen = new Set();
  for (const row of Array.isArray(data?.stops) ? data.stops : []) {
    if (!row || typeof row.stopCode !== 'string' || !/^[0-9]{5}$/.test(row.stopCode) || seen.has(row.stopCode)) continue;
    seen.add(row.stopCode);
    stops.push({
      stopCode: row.stopCode,
      description: typeof row.description === 'string' && row.description.trim() ? row.description : `Bus stop ${row.stopCode}`,
      roadName: typeof row.roadName === 'string' ? row.roadName : '',
      services: [...new Set((Array.isArray(row.services) ? row.services : []).filter(x => typeof x === 'string' && /^[A-Za-z0-9]{1,12}$/.test(x)))],
    });
    if (stops.length === 20) break;
  }
  return {stops, warning: typeof data?.warning === 'string' ? data.warning : ''};
}

export function samplingChoice(stops, preferredStop, preferredService) {
  const stop = stops.find(row => row.stopCode === preferredStop) || stops[0];
  const services = stop?.services || [];
  return {stopCode: stop?.stopCode || '', services,
    service: services.includes(preferredService) ? preferredService : services[0] || ''};
}
