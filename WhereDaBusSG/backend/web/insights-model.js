/* Pure formatting and date helpers shared by the report screens and demo. */
export const escapeHTML = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const finite = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null;
export const formatNumber = (value, digits = 0) => finite(value) === null ? '—' : Number(value).toLocaleString('en-SG', {maximumFractionDigits: digits});
export const sgDate = (now = Date.now()) => new Date(now + 8 * 3600000).toISOString().slice(0, 10);
export const shiftDate = (date, days) => new Date(Date.parse(`${date}T00:00:00Z`) + days * 86400000).toISOString().slice(0, 10);
export function datePreset(preset, now = Date.now()) {
  const today = sgDate(now), yesterday = shiftDate(today, -1);
  return preset === 'today' ? {from:today,to:today} : preset === 'yesterday' ? {from:yesterday,to:yesterday} : {from:shiftDate(today,-7),to:yesterday};
}
export function validDateRange(from, to, now = Date.now()) {
  const valid = s => /^\d{4}-\d{2}-\d{2}$/.test(s) && Number.isFinite(Date.parse(`${s}T00:00:00Z`)) && new Date(`${s}T00:00:00Z`).toISOString().slice(0,10) === s;
  if (!valid(from) || !valid(to)) return 'Choose a start date and an end date.';
  if (from > to) return 'The start date must be on or before the end date.';
  if (to > sgDate(now)) return 'The end date cannot be in the future in Singapore.';
  if (Date.parse(to) - Date.parse(from) >= 31 * 86400000) return 'Choose at most 31 calendar days.';
  return '';
}
export function formatSG(value) {
  const time = typeof value === 'number' ? value : Date.parse(value);
  if (value == null || !Number.isFinite(time)) return 'Unknown time';
  return new Intl.DateTimeFormat('en-SG',{timeZone:'Asia/Singapore',day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(new Date(time));
}
export function csvText(rows) {
  if (!rows.length) return '';
  const keys = [...new Set(rows.flatMap(row => Object.keys(row)))];
  const cell = value => {
    let s = value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
    if (typeof value === 'string' && /^[\s]*[=+@-]/.test(s)) s = "'" + s;
    return '"' + s.replace(/"/g,'""') + '"';
  };
  return '\uFEFF' + [keys.map(cell).join(','), ...rows.map(row => keys.map(k => cell(row[k])).join(','))].join('\r\n');
}
export function chartSegments(rows, key) {
  const result = []; let current = [], previous = null;
  for (const row of rows) {
    const time = Date.parse(row._id?.bucket), value = finite(row[key]);
    if (value === null || value < 0 || value > 2 || !Number.isFinite(time)) {
      if (current.length) result.push(current);
      current = []; previous = null; continue;
    }
    if (previous !== null && time - previous !== 1800000) {
      if (current.length) result.push(current);
      current = [];
    }
    current.push({time,value}); previous = time;
  }
  if (current.length) result.push(current);
  return result;
}
export function trendChart(rows, operator) {
  const sorted = [...rows].sort((a,b) => Date.parse(a._id.bucket)-Date.parse(b._id.bucket));
  const times = sorted.map(r => Date.parse(r._id.bucket)).filter(Number.isFinite);
  if (!times.length) return '';
  const start = times[0], end = times.at(-1), left = 48, width = 660, top = 24, height = 170;
  const x = t => end === start ? left + width / 2 : left + width * (t-start)/(end-start);
  const y = v => top + height * (1-v/2);
  const grid = [0,1,2].map(v => `<line x1="${left}" y1="${y(v)}" x2="${left+width}" y2="${y(v)}" class="chart-grid"/><text x="32" y="${y(v)+4}" text-anchor="end">${v}</text>`).join('');
  const labels = [0,.5,1].map((p,i) => `<text x="${left+p*width}" y="221" text-anchor="${['start','middle','end'][i]}">${escapeHTML(formatSG(start+p*(end-start)))}</text>`).join('');
  const series = [['meanCrowdingOrdinal','observed'],['rollingMeanOrdinal','rolling']].map(([key,cls]) => chartSegments(sorted,key).map(segment => `<polyline class="chart-line ${cls}" points="${segment.map(p=>`${x(p.time).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ')}"/>` + segment.map(p=>`<circle class="chart-point ${cls}" cx="${x(p.time).toFixed(2)}" cy="${y(p.value).toFixed(2)}" r="${cls==='observed'?3:2}"><title>${escapeHTML(formatSG(p.time))} SGT · ${cls==='observed'?'Mean':'Rolling mean'} ${p.value.toFixed(2)}</title></circle>`).join('')).join('')).join('');
  return `<div class="trend-chart-scroll" tabindex="0" role="region" aria-label="Crowding chart; scroll horizontally on smaller screens"><svg class="trend-chart" viewBox="0 0 756 245" role="img" aria-label="Crowding scores from zero to two over time for ${escapeHTML(operator)}; missing periods remain gaps">${grid}${labels}${series}</svg></div>`;
}
