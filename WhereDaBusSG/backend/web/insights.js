import {samplingOptions} from './sampling.js';
import {escapeHTML as esc, finite, formatNumber as num, datePreset, sgDate, validDateRange, formatSG, csvText, trendChart} from './insights-model.js';

const el = id => document.getElementById(id);
const rowsOf = value => Array.isArray(value) ? value : [];
const metric = (label,value,note='') => `<div class="insight-metric"><span>${esc(label)}</span><strong>${esc(value)}</strong>${note?`<small>${esc(note)}</small>`:''}</div>`;
const pct = value => finite(value) === null ? '—' : `${num(value,1)}%`;
const scope = '<p class="field-hint">Scores describe sampled predictions, not passenger counts or unique buses. Unknown loads are excluded from crowding metrics.</p>';

function toolbar(id) {
  return `<div class="report-actions"><button type="button" class="button secondary" data-export="${id}" data-format="csv" disabled>Download CSV</button><button type="button" class="button ghost" data-export="${id}" data-format="json" disabled>Download JSON</button></div>`;
}
function form(kind) {
  return `<div class="insight-intro"><p>${kind==='peak'?'Explore weekday crowding between 07:00 and 08:59 SGT.':'See how crowding changes in 30-minute periods.'} These reports use stored observations from the historical collector.</p><span class="pill">HISTORICAL ANALYSIS</span></div>
  <form id="${kind}-form" class="panel insight-form">
    <div class="form-grid insight-filters">
      <div><label for="${kind}-stop">Sampled stop code</label><input id="${kind}-stop" list="${kind}-stop-list" pattern="[0-9]{5}" maxlength="5" inputmode="numeric" placeholder="Choose or enter a stop" required autocomplete="off"><datalist id="${kind}-stop-list"></datalist><p id="${kind}-stop-name" class="field-hint"></p></div>
      <div><label for="${kind}-service">Bus service</label><input id="${kind}-service" list="${kind}-service-list" pattern="[A-Za-z0-9]{1,12}" maxlength="12" placeholder="Choose or enter a service" required autocomplete="off"><datalist id="${kind}-service-list"></datalist><p class="field-hint">Suggestions do not guarantee collected history.</p></div>
      <div><label for="${kind}-from">From date · SGT</label><input id="${kind}-from" type="date" required></div>
      <div><label for="${kind}-to">Through date · SGT</label><input id="${kind}-to" type="date" required></div>
    </div>
    <div class="insight-presets"><span>Quick dates</span><button class="button ghost" type="button" data-period="today" data-report="${kind}">Today so far</button><button class="button ghost" type="button" data-period="yesterday" data-report="${kind}">Yesterday</button><button class="button ghost" type="button" data-period="week" data-report="${kind}">Previous 7 days</button></div>
    <div class="form-footer"><p class="field-hint">Both dates are included. Up to 31 days. Today is incomplete; data before collection began is unavailable.</p><button class="button primary" type="submit">${kind==='peak'?'Analyse morning peak':'Show trend'}</button></div>
  </form>
  <div id="${kind}-options" class="result-region sampling-notice" aria-live="polite"></div>
  ${toolbar(kind)}<div id="${kind}-result" class="result-region" aria-live="polite"></div>
  <details class="panel insight-method"><summary>How to interpret this report</summary><p>Only the first predicted bus is used. A crowding sample needs a monitored prediction, a known load category, and an estimated arrival between 0 and 30 minutes after collection. Replayed observations are deduplicated.</p><p>Category scores: 0 = seats available; 1 = standing available; 2 = limited standing. A mean score is an ordinal summary, not occupancy percentage.</p>${kind==='trend'?'<p>The rolling score weights valid samples across the current and two preceding observed periods. Gaps stay gaps, so this is not necessarily a continuous 90-minute window. Operators are shown separately.</p>':'<p>Weekends and observations outside 07:00–08:59 Singapore time are excluded. Percentages describe valid sampled observations, not distinct buses.</p>'}<p>For a classroom demonstration, <a href="/docs" target="_blank" rel="noopener">open the API documentation</a> on this same installation.</p></details>`;
}

function reportHeader(kind,params,demo) {
  return `<div class="result-heading"><div><strong class="result-title">Service ${esc(params.service)} · stop ${esc(params.stop)}</strong><p>${esc(params.date_from)} through ${esc(params.date_to)} · Singapore time${params.date_to===sgDate()?' · today is incomplete':''}</p></div><span class="pill ${demo?'demo':''}">${demo?'SYNTHETIC DEMO':'STORED HISTORY'}</span></div>`;
}
function emptyReport(kind,params,demo) {
  return reportHeader(kind,params,demo)+`<div class="empty-state"><p class="empty-title">No matching ${kind==='peak'?'morning-peak observations':'historical observations'}</p><p>${kind==='peak'?'This report needs weekday samples collected between 07:00 and 08:59 SGT.':'Choose a service sampled at this stop and dates when collection was running.'} The collector cannot recover earlier data. Live-arrival lookups alone do not populate history.</p><a class="button secondary" href="${kind==='peak'?'#trend':'#status'}">${kind==='peak'?'Explore half-hour trends':'Check collection help'}</a></div>`;
}
function peakMarkup(data,params,demo) {
  const rows=rowsOf(data?.rows); if(!rows.length)return emptyReport('peak',params,demo);
  return reportHeader('peak',params,demo)+rows.map(row=>{
    const bars=[['Seats available','seats',row.seatsAvailablePct],['Standing available','standing',row.standingAvailablePct],['Limited standing','limited',row.limitedStandingPct]];
    const hasValid=finite(row.validSamples)>0;
    return `<article class="panel peak-card"><div class="panel-heading"><h2>${esc(row._id?.operator||'Operator unknown')}</h2><span class="pill">WEEKDAYS · 07:00–08:59</span></div><div class="metric-grid">${metric('Mean crowding score',num(row.meanCrowdingOrdinal,2),'Category scale: 0 to 2')}${metric('Valid samples',num(row.validSamples),`${num(row.observations)} observations considered`)}${metric('Usable observations',pct(row.validSamplePct))}${metric('Days with valid samples',num(row.daysWithValidSamples))}</div>${hasValid?`<div class="capacity-bar" role="img" aria-label="Sample distribution: ${bars.map(([label,,value])=>esc(label+' '+pct(value))).join(', ')}">${bars.map(([,cls,value])=>`<span class="${cls}" style="width:${Math.max(0,Math.min(100,finite(value)||0))}%"></span>`).join('')}</div>`:'<p class="notice">Observations exist, but none has a valid crowding sample. Unknown values are not counted as empty buses.</p>'}<div class="capacity-labels">${bars.map(([label,cls,value])=>`<span><i class="load-dot ${cls}"></i>${label} <strong>${pct(value)}</strong></span>`).join('')}</div>${scope}</article>`;
  }).join('');
}
function trendMarkup(data,params,demo) {
  const rows=rowsOf(data); if(!rows.length)return emptyReport('trend',params,demo);
  const groups=new Map();for(const row of rows){const op=row._id?.operator||'Unknown';if(!groups.has(op))groups.set(op,[]);groups.get(op).push(row);}
  const allValid=rows.reduce((sum,r)=>sum+(finite(r.validSamples)||0),0);
  const ordered=[...rows].sort((a,b)=>Date.parse(a._id?.bucket)-Date.parse(b._id?.bucket));
  return reportHeader('trend',params,demo)+`<div class="metric-grid panel report-summary">${metric('Observed periods',num(rows.length),'Per operator; missing periods stay missing')}${metric('Valid samples',num(allValid))}${metric('Operators',num(groups.size))}</div>`+[...groups].map(([op,series])=>`<article class="panel chart-panel"><div class="panel-heading"><h2>${esc(op)} · crowding over time</h2><span class="pill">SCORE 0–2</span></div><div class="chart-legend"><span><i class="chart-key observed"></i>Period mean</span><span><i class="chart-key rolling"></i>Weighted rolling mean</span></div>${trendChart(series,op)}<p class="field-hint">Times shown in Singapore time. Lines break across missing periods. Hover over a point for its value. Scroll sideways on smaller screens.</p></article>`).join('')+`${allValid===0?'<p class="notice">No valid crowding samples in this selection. Counts remain visible; unknown crowding is not plotted as zero.</p>':''}<div class="panel results-panel"><h2>Period-by-period results</h2><p class="field-hint">Showing ${Math.min(100,rows.length)} of ${rows.length} rows. Downloads include all rows.</p><div class="report-table-wrap" tabindex="0" role="region" aria-label="Historical results table"><table class="report-table"><thead><tr><th>Period start · SGT</th><th>Operator</th><th>Observations</th><th>Valid</th><th>Mean score</th><th>Rolling score</th><th>Limited standing</th></tr></thead><tbody>${ordered.slice(0,100).map(r=>`<tr><td>${esc(formatSG(r._id?.bucket))}</td><td>${esc(r._id?.operator)}</td><td>${num(r.observations)}</td><td>${num(r.validSamples)}</td><td>${num(r.meanCrowdingOrdinal,2)}</td><td>${num(r.rollingMeanOrdinal,2)}</td><td>${pct(r.limitedStandingPct)}</td></tr>`).join('')}</tbody></table></div>${scope}</div>`;
}
function longestMarkup(data,demo,coverageMarkup) {
  const rows=rowsOf(data.rows);
  if(!rows.length)return '<div class="empty-state"><p class="empty-title">No routes available for ranking</p><p>Import the route network first. Routes with detected gaps are excluded from this ranking.</p><a href="#status" class="button secondary">Check route coverage</a></div>'+coverageMarkup(data.coverage);
  return `<div class="status-line"><span class="pill ${demo?'demo':''}">${demo?'SYNTHETIC DEMO':'IMPORTED ROUTE NETWORK'}</span><span>${rows.length} service directions · ranked by recorded route distance</span></div><div class="longest-list">${rows.map((r,i)=>`<article class="panel longest-card"><span class="rank-number">${i+1}</span><div class="longest-body"><div class="route-card-heading"><strong class="service-no">${esc(r.service_no)}</strong><span class="route-meta">${esc(r.operator)} · direction ${esc(r.direction)}</span></div><p class="longest-endpoints">${esc(r.origin_description||r.origin_code)} <span aria-label="to">→</span> ${esc(r.destination_description||r.destination_code)}</p><p class="field-hint">${num(r.stop_count)} recorded stop occurrences · ${esc(r.origin_code)} to ${esc(r.destination_code)}</p></div><div class="longest-distance"><strong>${num(r.route_length_km,2)}</strong><span>km</span></div></article>`).join('')}</div><p class="field-hint">No detected sequence gaps is not proof that the provider supplied every stop. These distances do not imply fares, travel times, or current operation.</p>`+coverageMarkup(data.coverage);
}

export function createInsights({api,run,getMode,routeRequest,routeCoverage,invalidate}) {
  const state={options:{},loaded:new Set(),exports:{}};
  for(const kind of ['peak','trend']) el(`view-${kind}`).innerHTML=form(kind);
  el('view-longest').innerHTML=`<div class="insight-intro"><p>Explore the ten longest service directions in the imported network. Only directions with no detected route gaps are ranked.</p><span class="pill">ROUTE EXPLORER</span></div><div class="panel feature-toolbar"><button id="longest-refresh" class="button primary">Load longest routes</button></div>${toolbar('longest')}<div id="longest-result" class="result-region" aria-live="polite"></div><details class="panel insight-method"><summary>About this ranking</summary><p>Distances are derived from recorded route endpoints. Each service direction is ranked separately; a loop may begin and end at the same physical stop.</p><p>For a classroom demonstration, <a href="/docs" target="_blank" rel="noopener">open the API documentation</a>. Transfer planning and fare calculation are outside this release.</p></details>`;
  const buttons=kind=>document.querySelectorAll(`[data-export="${kind}"]`);
  function clear(kind,message='Selection changed. Run the report to update the results.') {
    invalidate(`${kind}-result`);delete state.exports[kind];buttons(kind).forEach(b=>b.disabled=true);
    el(`${kind}-result`).innerHTML=`<p class="field-hint">${esc(message)}</p>`;
  }
  function setDates(kind,preset) {
    const range=datePreset(preset);el(`${kind}-from`).value=range.from;el(`${kind}-to`).value=range.to;
    el(`${kind}-to`).setCustomValidity('');
  }
  function updateServices(kind,choose=false) {
    const options=state.options[kind]||[],stop=options.find(s=>s.stopCode===el(`${kind}-stop`).value);
    el(`${kind}-service-list`).innerHTML=(stop?.services||[]).map(s=>`<option value="${esc(s)}"></option>`).join('');
    el(`${kind}-stop-name`).textContent=stop?`${stop.description} · ${stop.roadName}`:'Enter the five-digit code of a stop with collected history.';
    if(choose&&!el(`${kind}-service`).value&&stop?.services?.length)el(`${kind}-service`).value=stop.services[0];
  }
  async function loadOptions(kind) {
    await run(`${kind}-options`,async signal=>{
      const [choices,status]=await Promise.all([api('/sampling/options',{},signal),getMode()==='demo'?Promise.resolve(null):api('/data/status',{section:'sampling'},signal).catch(error=>{if(error.name==='AbortError')throw error;return null;})]);
      return {choices,status};
    },({choices,status})=>{
      const parsed=samplingOptions(choices);state.options[kind]=parsed.stops;
      el(`${kind}-stop-list`).innerHTML=parsed.stops.map(s=>`<option value="${esc(s.stopCode)}">${esc(s.description)} · ${esc(s.roadName)}</option>`).join('');
      if(!el(`${kind}-stop`).value&&parsed.stops.length)el(`${kind}-stop`).value=parsed.stops[0].stopCode;
      updateServices(kind,true);
      const s=status?.sampling;
      return `<p class="field-hint">${getMode()==='demo'?'Synthetic examples run in your browser; they are not evidence of database execution.':s?.configured?`Collector configured for ${s.stops.length} stops. Recent snapshots: ${(s.freshStops||[]).length}. ${s.latestSnapshotAt?'Last snapshot: '+esc(formatSG(s.latestSnapshotAt))+' SGT.':''} Configuration alone does not prove collection is running.`:'Collector status unavailable or not configured. You can still enter a stop and service to inspect previously collected history.'}${parsed.warning?' '+esc(parsed.warning):''}</p>`;
    });
  }
  function saveExport(kind,rows,params,response=null) {
    state.exports[kind]={source:getMode()==='demo'?'SYNTHETIC browser demo':'Application database query',generatedAt:new Date().toISOString(),timezone:'Asia/Singapore',query:params,metric:kind==='longest'?'Recorded route distance in kilometres':'Ordinal crowding: 0=SEA, 1=SDA, 2=LSD; not occupancy percent',...(response?.start?{start:response.start,endExclusive:response.endExclusive}:{}),rows};
    buttons(kind).forEach(b=>b.disabled=!rows.length);
  }
  function report(kind) {
    const formEl=el(`${kind}-form`),from=el(`${kind}-from`).value,to=el(`${kind}-to`).value;
    el(`${kind}-to`).setCustomValidity(validDateRange(from,to));
    if(!formEl.reportValidity())return;
    const params={stop:el(`${kind}-stop`).value.trim(),service:el(`${kind}-service`).value.trim(),date_from:from,date_to:to};
    clear(kind,'Loading historical observations…');
    run(`${kind}-result`,signal=>api(`/analytics/${kind}`,params,signal),data=>{
      saveExport(kind,kind==='peak'?rowsOf(data.rows):rowsOf(data),params,data);
      return kind==='peak'?peakMarkup(data,params,getMode()==='demo'):trendMarkup(data,params,getMode()==='demo');
    },formEl);
  }
  function longest() {
    clear('longest','Loading route ranking…');
    run('longest-result',signal=>routeRequest('/routes/longest',{},signal),data=>{
      saveExport('longest',rowsOf(data.rows),{endpoint:'/routes/longest'});
      return longestMarkup(data,getMode()==='demo',routeCoverage);
    });
  }
  for(const kind of ['peak','trend']) {
    el(`${kind}-form`).addEventListener('submit',event=>{event.preventDefault();report(kind);});
    el(`${kind}-form`).addEventListener('input',()=>{el(`${kind}-to`).setCustomValidity('');clear(kind);});
    el(`${kind}-stop`).addEventListener('input',()=>updateServices(kind));
    el(`${kind}-stop`).addEventListener('change',()=>updateServices(kind,true));
  }
  el('longest-refresh').addEventListener('click',longest);
  document.addEventListener('click',event=>{
    const b=event.target.closest('button');if(!b)return;
    if(b.dataset.period){setDates(b.dataset.report,b.dataset.period);clear(b.dataset.report);}
    if(b.dataset.export){
      const report=state.exports[b.dataset.export];if(!report?.rows.length)return;
      const json=b.dataset.format==='json';
      const flat=report.rows.map(row=>{const {_id,...rest}=row;return {dataSource:report.source,generatedAt:report.generatedAt,timezone:report.timezone,...report.query,...(_id&&typeof _id==='object'?_id:{}),...rest};});
      const blob=new Blob([json?JSON.stringify(report,null,2):csvText(flat)],{type:json?'application/json':'text/csv;charset=utf-8'});
      const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=`WhereDaBus-${b.dataset.export}-${getMode()}-${sgDate()}.${json?'json':'csv'}`;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(a.href),1000);
    }
  });
  function reset() {
    state.options={};state.loaded.clear();state.exports={};
    for(const kind of ['peak','trend']){el(`${kind}-stop-list`).innerHTML='';el(`${kind}-service-list`).innerHTML='';el(`${kind}-stop-name`).textContent='';el(`${kind}-options`).innerHTML='';for(const field of ['from','to'])el(`${kind}-${field}`).max=sgDate();setDates(kind,kind==='trend'?'today':'week');clear(kind,'Choose a sampled stop and service, then run the report.');}
    clear('longest','Load the longest routes to explore the imported network.');
  }
  function enter(page) {
    if(!['peak','trend','longest'].includes(page)||state.loaded.has(page))return;
    state.loaded.add(page);
    if(page==='longest')longest();else loadOptions(page);
  }
  function showRecordedMorning() {
    el('peak-stop').value = '59059';
    el('peak-service').value = '860';
    el('peak-from').value = '2026-11-18';
    el('peak-to').value = '2026-11-18';
    updateServices('peak');
    report('peak');
  }
  reset();return {enter,reset,showRecordedMorning};
}
