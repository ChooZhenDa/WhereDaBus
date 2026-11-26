import { demoRequest } from './demo.js?v=fares-feedback';
import { createInsights } from './insights.js';
import { routeCoverage, routeFacts } from './route-coverage.js';
import { comparisonFacts, comparisonHeading, downloadComparison } from './comparison.js?v=fares-feedback';
import { createJourneys, saveJourneyForm } from './journeys.js?v=fares-feedback';
import { createFeedback, feedbackButton } from './feedback.js?v=fares-feedback';
import { sgDate, validDateRange } from './insights-model.js';

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const paths = {
  bus:'M6 18v3m12-3v3M5 10h14M5 5c0-2 14-2 14 0v11a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5ZM8 14h.01M16 14h.01',
  clock:'M12 8v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
  pin:'M20 10c0 6-8 12-8 12S4 16 4 10a8 8 0 1 1 16 0ZM15 10a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z',
  route:'M5 5h10a4 4 0 0 1 0 8H9a4 4 0 0 0 0 8h10M2 5a3 3 0 1 0 6 0 3 3 0 0 0-6 0M16 21h5v-5',
  road:'M7 3 3 21M17 3l4 18M12 3v3m0 4v4m0 4v3',
  bookmark:'M6 3h12v18l-6-4-6 4V3Z',
  help:'M9 9a3 3 0 0 1 6 0c0 2-3 2-3 4m0 4h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
  arrow:'M4 12h16m-6-6 6 6-6 6',
  refresh:'M20 7a9 9 0 0 0-15-2L2 8m0-5v5h5M4 17a9 9 0 0 0 15 2l3-3m0 5v-5h-5',
  search:'m16 16 5 5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z',
  locate:'M12 2v4m0 12v4M2 12h4m12 0h4M19 12a7 7 0 1 1-14 0 7 7 0 0 1 14 0ZM14 12a2 2 0 1 1-4 0 2 2 0 0 1 4 0Z',
  plus:'M12 5v14M5 12h14',
  swap:'M3 7h17m-4-4 4 4-4 4M21 17H4m4-4-4 4 4 4',
  menu:'M4 6h16M4 12h16M4 18h16',
  info:'M12 11v6m0-10h.01M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z',
  check:'m5 12 4 4L19 6',
  close:'m6 6 12 12M6 18 18 6'
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true"><path d="${paths[name] || paths.info}"></path></svg>`;
$$('[data-icon]').forEach(el => el.innerHTML = icon(el.dataset.icon));
const pages = {
  arrivals:['Live arrivals','clock'],
  nearby:['Nearby stops','pin'],
  direct:['Direct routes','route'],
  road:['Routes by road','road'],
  longest:['Longest routes','road'],
  peak:['Morning crowding','clock'],
  trend:['Half-hour trends','route'],
  saved:['Saved stops','bookmark'],
  journeys:['Saved journeys','route'],
  status:['Help & status','help']
};
$('#navigation').innerHTML = Object.keys(pages).map(id=>`<a href="#${id}" class="nav-link" data-page="${id}">${icon(pages[id][1])}<span>${pages[id][0]}</span><span class="nav-arrow" aria-hidden="true">${id==='arrivals'?'›':''}</span></a>`).join('');

const state = {mode:'live',page:'arrivals',config:null,activeStop:null,stopNames:new Map(),requests:new Map(),controllers:new Map(),retry:new Map(),auto:false,timer:null,sequence:0};
const feedback = createFeedback(() => state.mode);
let comparedTrip = null;
let modeInitialized = false;
let toastTimer;
function toast(message){$('#toast').textContent=message;$('#toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('#toast').hidden=true,4000);}
function getSaved(){try {const value=JSON.parse(localStorage.getItem(`wheredabus-saved-${state.mode}`)||'[]');return Array.isArray(value)?value.filter(x=>x&&/^\d{5}$/.test(x.code)&&typeof x.name==='string').slice(0,50):[];}catch{return [];}}
function toggleSave(code,name){if(!/^\d{5}$/.test(code))return;const saved=getSaved();const exists=saved.some(x=>x.code===code);const next=exists?saved.filter(x=>x.code!==code):[...saved,{code,name:name||`Stop ${code}`}];if(next.length>50){toast('You can save up to 50 stops.');return;}try{localStorage.setItem(`wheredabus-saved-${state.mode}`,JSON.stringify(next));toast(exists?'Stop removed from your saved list.':'Stop saved for your next journey.');}catch{toast('This browser cannot save stops. Check your storage settings.');}renderSaved();updateSaveButtons();}
function saveButton(code,name){const saved=getSaved().some(x=>x.code===code);return `<button class="icon-button ${saved?'saved':''}" data-save="${escape(code)}" data-name="${escape(name)}" aria-label="${saved?'Unsave':'Save'} ${escape(name||code)}" aria-pressed="${saved}">${icon('bookmark')}</button>`;}
function updateSaveButtons(){const saved=getSaved();$$('[data-save]').forEach(button=>{const yes=saved.some(x=>x.code===button.dataset.save);button.classList.toggle('saved',yes);button.setAttribute('aria-pressed',String(yes));button.setAttribute('aria-label',`${yes?'Unsave':'Save'} ${button.dataset.name||button.dataset.save}`);});}
function empty(title,text,iconName='search',extra=''){return `<div class="empty-state"><div class="empty-icon">${icon(iconName)}</div><p class="empty-title">${escape(title)}</p><p>${escape(text)}</p>${extra}</div>`;}
function initialiseEmpty(){
  for(const [id,message] of Object.entries({arrival:'Search for a stop to see its next buses.',nearby:'Use your location or enter coordinates to find stops.',direct:'Choose your starting and destination stops, then select Find a route.',road:'Enter a road name to find services.'}))$(`#${id}-result`).innerHTML=`<p class="field-hint">${escape(message)}</p>`;
  $('#arrival-matches').innerHTML='';
}

function cancelRequests(){for(const controller of state.controllers.values())controller.abort();state.controllers.clear();state.requests.clear();state.retry.clear();$$('.result-region').forEach(el=>el.removeAttribute('aria-busy'));$$('form button[type=submit]').forEach(el=>el.disabled=false);}
async function api(path,params={},signal){if(state.mode==='demo')return demoRequest(path,params);if(state.config?.mode==='preview')throw new Error('The standalone preview uses demo data. Run Setup.cmd and open the new app to use live data.');const query=new URLSearchParams(params);const response=await fetch(`${path}${query.size?'?'+query:''}`,{signal,headers:{Accept:'application/json'},credentials:'same-origin'});let data;try{data=await response.json();}catch{throw new Error('The server returned an unreadable response. Check Help & status and try again.');}if(!response.ok){const detail=data.detail;const reason=detail?.reason;let message=typeof detail==='string'?detail:detail?.message;const reasons={lta_key_not_configured:'Live arrivals are not configured yet. Your administrator needs to connect the LTA data source.',provider_unavailable:'The bus data provider is temporarily unavailable. Please try again shortly.',provider_cooldown:'The bus data provider needs a moment before the next request.',refresh_in_progress:'This stop is refreshing. Please try again shortly.'};if(!message)message=reasons[reason]||(response.status===422?'Check the values you entered and try again.':response.status===401||response.status===403?'Your session cannot access this feature. Sign in through your existing app.':'This feature is temporarily unavailable. Check Help & status and try again.');const error=new Error(message);const delay=Number(response.headers.get('Retry-After'));if(Number.isFinite(delay)&&delay>0)error.retryAfter=delay;throw error;}return data;}
async function run(region,task,render,form){state.controllers.get(region)?.abort();const controller=new AbortController();state.controllers.set(region,controller);const token=++state.sequence;state.requests.set(region,token);const el=$(`#${region}`);const previousFocus=document.activeElement;const restoreFocus=el.contains(previousFocus);el.setAttribute('aria-busy','true');el.innerHTML='<div class="loading"><span class="spinner" aria-hidden="true"></span>Loading results…</div>';if(form)$$('button[type=submit]',form).forEach(b=>b.disabled=true);const timer=setTimeout(()=>controller.abort(),25000);state.retry.set(region,()=>run(region,task,render,form));try{const data=await task(controller.signal);if(state.requests.get(region)!==token)return null;el.innerHTML=render(data);if(restoreFocus&&document.activeElement===document.body&&previousFocus?.id&&document.getElementById(previousFocus.id))document.getElementById(previousFocus.id).focus({preventScroll:true});return data;}catch(error){if(state.requests.get(region)!==token)return null;el.innerHTML=`<div class="empty-state error-state" role="alert"><div class="empty-icon">${icon('info')}</div><p class="empty-title">We couldn’t load this just yet.</p><p>${escape(error.name==='AbortError'?'The request took too long. Check your connection and try again.':error.message)}</p><button class="button secondary" data-retry="${region}">${icon('refresh')}Try again</button></div>`;return {failed:true,retryAfter:error.retryAfter||20};}finally{clearTimeout(timer);if(state.requests.get(region)===token){el.removeAttribute('aria-busy');state.controllers.delete(region);if(form)$$('button[type=submit]',form).forEach(b=>b.disabled=false);}}}

const number = (value,digits=0)=>value!==null&&value!==undefined&&value!==''&&Number.isFinite(Number(value))?Number(value).toLocaleString('en-SG',{maximumFractionDigits:digits}):'—';
const date = value=>{const d=new Date(value);return value&&!Number.isNaN(d.getTime())?new Intl.DateTimeFormat('en-SG',{timeZone:'Asia/Singapore',day:'numeric',month:'short',hour:'2-digit',minute:'2-digit',hour12:false}).format(d):'Unknown time';};
const pick = (row,...keys)=>keys.map(k=>row[k]).find(v=>v!==undefined&&v!==null);
const safeArray = value=>Array.isArray(value)?value:[];

function stopChoices(rows,attributes){
  const list=safeArray(rows).filter(row=>/^\d{5}$/.test(row.stop_code||''));
  if(!list.length)return empty('No matching stops','Try another stop name, road name or a real five-digit stop code.','pin');
  return '<p class="field-hint">Choose the correct physical stop and road. Opposite sides of a road are different stops.</p>'+list.map(row=>`<button type="button" class="match-button" ${attributes(row)} data-name="${escape(row.description)}">${icon('pin')}<span><strong>${escape(row.description)}</strong><small>${escape(row.road_name)} · ${escape(row.stop_code)}</small></span>${icon('arrow')}</button>`).join('');
}
function searchDirectStop(side){
  const query=$(`#direct-${side}`).value.trim();if(!query)return;
  run(`direct-${side}-matches`,signal=>api('/stops/search',{query,limit:15},signal),rows=>stopChoices(rows,row=>`data-direct-side="${side}" data-direct-code="${escape(row.stop_code)}" data-road="${escape(row.road_name)}"`));
}
function invalidateDirect(){
  comparedTrip = null;
  const region='direct-result';
  state.controllers.get(region)?.abort();state.controllers.delete(region);state.requests.delete(region);state.retry.delete(region);
  $('#direct-result').removeAttribute('aria-busy');
  $('#direct-result').innerHTML='<p class="field-hint">Stops changed. Select Find a route to search this journey.</p>';
  $$('button[type=submit]',$('#direct-form')).forEach(button=>button.disabled=false);
}
function clearDirectMatches(side){
  const region=`direct-${side}-matches`;
  state.controllers.get(region)?.abort();state.controllers.delete(region);state.requests.delete(region);state.retry.delete(region);
  $(`#${region}`).removeAttribute('aria-busy');$(`#${region}`).innerHTML='';
}
function chooseDirectStop(side,code,name,road){
  if(!['origin','destination'].includes(side)||!/^\d{5}$/.test(code))return;
  invalidateDirect();clearDirectMatches(side);
  $(`#direct-${side}-code`).value=code;$(`#direct-${side}`).value=name;
  const selected=$(`#direct-${side}-selected`);selected.textContent=`${road ? road+' · ' : ''}${code}`;selected.hidden=false;
}
function searchDirect(){
  const params={};
  for(const side of ['origin','destination']){
    const query=$(`#direct-${side}`).value.trim();params[side]=$(`#direct-${side}-code`).value||(/^\d{5}$/.test(query)?query:'');
    if(!params[side]){searchDirectStop(side);$('#direct-result').innerHTML=empty('Choose both stops first','Select the intended stop from each name search.','route');return;}
  }
  const from = $('#direct-date-from').value, to = $('#direct-date-to').value;
  if (from || to) {
    const error = validDateRange(from, to);
    if (error) {invalidateDirect();$('#direct-result').textContent=error;return;}
    Object.assign(params, {date_from: from, date_to: to});
  }
  comparedTrip = null;
  const mode = state.mode;
  return run('direct-result', async signal => {
    const [trip, status] = await Promise.all([
      api('/routes/compare',params,signal),
      mode === 'demo' ? null : api('/data/status',{section:'routes'},signal).catch(error => {
        if (error.name === 'AbortError') throw error;
        return {routes:{status:'unavailable'}};
      })
    ]);
    return {trip, coverage:status?.routes};
  }, ({trip,coverage}) => {
    comparedTrip = trip;
    return comparisonHeading(trip,mode === 'demo') + routesMarkup(trip.routes,'direct',trip.asOf)
      + (trip.routes.length && trip.origin !== trip.destination ? saveJourneyForm() : '') + routeCoverage(coverage);
  }, $('#direct-form')).then(result => {
    const region = $('#direct-result');
    if (result?.trip) feedback.refresh(region, result.trip.origin);
    if (result?.trip && state.page === 'direct' && region.getBoundingClientRect().top > window.innerHeight * 0.6) {
      region.scrollIntoView({block:'start'});
    }
    return result;
  });
}
function stopCards(rows){if(!rows.length)return empty('No stops found in this search','Try another name, a wider radius or different coordinates. Imported stop data may also need updating.','pin');return `<div class="stop-list">${rows.map(row=>{const code=String(pick(row,'stop_code','stopCode','BusStopCode')||'');const name=String(pick(row,'description','Description','stop_name')||`Stop ${code}`);state.stopNames.set(code,name);const road=pick(row,'road_name','RoadName')||'';const distance=pick(row,'distance_m','distance_metres','distance_meters','distance');let services=row.services;const tags=Array.isArray(services)?services.join(', '):typeof services==='string'?services:null;return `<article class="panel stop-card"><div class="stop-symbol">${icon('pin')}</div><div class="stop-card-content"><strong class="stop-name">${escape(name)}</strong><p>${escape(road)}</p><span class="stop-code">STOP ${escape(code)}</span>${distance!==undefined?` · <span class="distance">${number(distance)} m away</span>`:''}<div class="stop-tags">${tags?`<span class="stop-tag">Services: ${escape(tags)}</span>`:'<span class="field-hint">Check arrivals for services at this stop.</span>'}</div></div><div class="stop-actions">${saveButton(code,name)}<button class="button secondary" data-arrivals="${escape(code)}" data-name="${escape(name)}">View arrivals ${icon('arrow')}</button></div></article>`;}).join('')}</div>`;}
function renderSaved(){const saved=getSaved();$('#saved-preview').innerHTML=saved.length?saved.slice(0,3).map(stop=>`<button class="saved-mini" data-arrivals="${escape(stop.code)}" data-name="${escape(stop.name)}"><span><strong>${escape(stop.name)}</strong><small>STOP ${escape(stop.code)}</small></span>${icon('arrow')}</button>`).join(''):'<div class="saved-placeholder">Tap the bookmark beside a stop to add it here.</div>';$('#saved-result').innerHTML=saved.length?stopCards(saved.map(x=>({stop_code:x.code,description:x.name}))):empty('No saved stops','Save a stop from an arrival or nearby result.','bookmark','<a class="button secondary" href="#arrivals">Find a stop</a>');}
function renderBus(bus,rank,stop,service,canReport){let seconds=bus?.eta?((new Date(bus.eta).getTime()-Date.now())/1000):null;if(!Number.isFinite(seconds))seconds=null;const label=seconds===null?'—':seconds<0?'Passed':seconds<60?'Arr':Math.ceil(seconds/60);const capacity={SEA:['Seats available','seats'],SDA:['Standing available','standing'],LSD:['Limited standing','limited']}[bus?.load]||['Capacity unknown',''];const type={SD:'Single deck',DD:'Double deck',BD:'Bendy bus'}[bus?.vehicleType]||'';return `<div class="bus-time"><div class="minutes ${rank===1?'next':''} ${seconds===null?'unknown':''}" aria-label="${seconds===null?'Arrival unavailable':seconds<0?'Estimated arrival has passed':seconds<60?'Arriving soon':`${label} minutes`}">${label}${typeof label==='number'?'<span class="unit">min</span>':''}</div><div class="capacity"><i class="load-dot ${capacity[1]}"></i>${capacity[0]}</div><div class="bus-details">${escape(type)}${bus?.feature==='WAB'?' · ♿':''}${bus?.monitored===0?' · Scheduled':''}</div>${canReport ? feedbackButton(stop,service,bus) : ''}</div>`;}
function arrivalsMarkup(data){
  const code=String(data.stopCode||data._id||state.activeStop||''),name=state.stopNames.get(code)||getSaved().find(x=>x.code===code)?.name||`Bus stop ${code}`;
  const observations=safeArray(data.observations),synthetic=state.mode==='demo'||observations.some(x=>String(x.source).includes('SYNTHETIC')),stale=data.status==='stale',unavailable=data.status==='unavailable';
  const header=`<div class="result-heading"><div><strong class="result-title">${escape(name)}</strong><p>STOP ${escape(code)}</p></div><div class="result-actions">${saveButton(code,name)}<button id="arrival-refresh" class="icon-button" data-arrival-refresh aria-label="Refresh arrivals">${icon('refresh')}</button></div></div><div class="status-line"><span class="pill ${synthetic?'demo':stale?'warning':''}">${synthetic?'SYNTHETIC DEMO':stale?'STALE DATA':unavailable?'UNAVAILABLE':'LIVE DATA'}</span>${data.capturedAt?`<span>Updated ${escape(date(data.capturedAt))} SGT</span>`:''}<label><input id="auto-refresh" type="checkbox" ${state.auto?'checked':''}>Auto refresh · every 20s</label></div>`;
  if(!observations.length)return header+empty(unavailable?'Arrivals unavailable':'No arrival estimates right now','This can mean there are no operating services, the stop code is unknown, or the provider returned no estimates. Check the stop code and try again.','clock');
  return header+(stale?'<div class="notice">These estimates are from an older response. Refresh to check for newer data.</div>':'')+`<div class="panel arrival-table"><div class="arrival-columns"><span>SERVICE</span><span>NEXT BUS</span><span>2ND BUS</span><span>3RD BUS</span></div>${observations.map(obs=>`<article class="arrival-row" aria-label="Service ${escape(obs.meta?.serviceNo)}"><div><div class="service-no">${escape(obs.meta?.serviceNo||'—')}</div><div class="operator">${escape(obs.meta?.operator||'')}</div></div>${[1,2,3].map(rank=>renderBus(safeArray(obs.arrivals).find(bus=>bus.rank===rank),rank,code,obs.meta?.serviceNo,!stale && !unavailable)).join('')}</article>`).join('')}</div><p class="arrival-footnote">Arr = arriving in under a minute. “Passed” means the estimate has elapsed. Missing estimates stay blank. Times are estimates and may change.</p>`;
}
function scheduleRefresh(seconds=20){clearTimeout(state.timer);if(!state.auto||state.page!=='arrivals'||document.hidden||!state.activeStop)return;state.timer=setTimeout(()=>loadArrivals(state.activeStop),Math.min(2147483,Math.max(20,seconds))*1000);}
async function loadArrivals(code,name){clearTimeout(state.timer);state.activeStop=code;if(name)state.stopNames.set(code,name);$('#arrival-stop').value=code;$('#arrival-matches').innerHTML='';const response=await run('arrival-result',signal=>api(`/arrivals/${encodeURIComponent(code)}`,{},signal),data=>arrivalsMarkup(data),$('#arrivals-form'));if(response&&state.activeStop===code){
  if (!response.failed) feedback.refresh($('#arrival-result'),code);
  scheduleRefresh(response.refreshAfterSeconds||response.retryAfter||20);
}}
function goToArrivals(code,name){if(!/^\d{5}$/.test(code))return;if(location.hash!=='#arrivals')location.hash='arrivals';loadArrivals(code,name);}

const columnNames={service_no:'Service',serviceNo:'Service',operator:'Operator',direction:'Direction',route_length_km:'Route length (km)',distance_km:'Distance (km)',distance_m:'Distance (m)',stop_code:'Stop code',stop_count:'Stops',origin_code:'From stop',destination_code:'To stop',origin_description:'From',destination_description:'To',origin_sequence:'Board at sequence',destination_sequence:'Alight at sequence',stops_between:'Stops after boarding (including destination)',observed_stops_between:'Recorded stops after boarding',observed_stop_count:'Recorded route stops',known_distance_span_km:'Distance between recorded endpoints (km)',road_name:'Road',meanCrowdingOrdinal:'Crowding score',rollingMeanOrdinal:'Rolling score',validSamples:'Valid samples',observations:'Observations',daysWithValidSamples:'Days with valid samples',limitedStandingPct:'Limited standing (%)',seatsAvailablePct:'Seats available (%)',standingAvailablePct:'Standing available (%)'};
function humanKey(key){return columnNames[key]||key.replace(/_/g,' ').replace(/([a-z])([A-Z])/g,'$1 $2').replace(/^./,c=>c.toUpperCase());}
function displayValue(value){if(value==null)return '—';if(Array.isArray(value))return value.map(displayValue).join(', ');if(typeof value==='object')return Object.entries(value).map(([k,v])=>`${humanKey(k)}: ${displayValue(v)}`).join(' · ');return typeof value==='number'?number(value,2):String(value);}
async function routeRequest(path,params,signal){
  if(state.mode==='demo')return {rows:await api(path,params,signal),coverage:null};
  const [rows,status]=await Promise.all([api(path,params,signal),api('/data/status',{section:'routes'},signal).catch(error=>{if(error.name==='AbortError')throw error;return null;})]);
  return {rows,coverage:status?.routes||{status:'unavailable'}};
}
function routesWithCoverage(data,type){return routesMarkup(data.rows,type)+routeCoverage(data.coverage);}
function routesMarkup(data,type,asOf){
  const rows=safeArray(data);
  if(!rows.length)return empty('No matching routes found',type==='direct'?'No direct connection was found in the imported data for these physical stops. Check the road and travel direction. Missing route data may hide a service; transfers are not included.':'Try a different search. Route data must be imported before this feature can return results.','route');
  return `<p class="route-count">${rows.length} result${rows.length===1?'':'s'} · ${state.mode==='demo'?'Synthetic demonstration':'Imported route network'}</p><div class="route-grid">${rows.map(row=>{
    const service=pick(row,'service_no','serviceNo','ServiceNo')||'Bus service';
    const origin=pick(row,'origin_description','origin_name','origin_code','origin_stop')||(type==='direct'?$('#direct-origin').value:null);
    const destination=pick(row,'destination_description','destination_name','destination_code','destination_stop')||(type==='direct'?$('#direct-destination').value:null);
    const {live, history, fare, ...staticRoute} = row;
    const facts=routeFacts(staticRoute,type),distance=facts.distance;
    const decision = asOf && type === 'direct' ? comparisonFacts(row,asOf) + feedbackButton(row.origin_code,service,live) : '';
    return `<article class="panel route-card${facts.partial?' partial-route':''}"><div class="route-card-heading"><div class="route-service">${icon('bus')}<strong>${escape(service)}</strong></div><div class="route-meta">${row.operator?escape(row.operator):'Route'}${row.direction!=null?` · Direction ${escape(row.direction)}`:''}</div>${distance!=null?`<span class="route-distance">${number(distance,2)} <small>km</small></span>`:''}</div>${facts.message?`<p class="route-data-note${facts.partial?' partial':''}">${facts.partial?'<strong>Partial route data.</strong> ':''}${escape(facts.message)}</p>`:''}${origin&&destination?`<div class="route-stops"><div><span class="route-stop-dot"></span><span>${escape(origin)}</span></div><div><span class="route-stop-dot end"></span><span>${escape(destination)}</span></div></div>`:'<p class="route-description">Service in the imported bus network</p>'}${decision}<details><summary>View route details</summary><dl class="route-details">${facts.details.map(([key,value])=>`<div><dt>${escape(humanKey(key))}</dt><dd>${escape(displayValue(value))}</dd></div>`).join('')}</dl></details></article>`;
  }).join('')}</div>`;
}
async function loadStatus(){
  await run('status-result',async signal=>{
    if(state.mode==='demo')return {demo:true};
    if(state.config?.mode==='preview')return {preview:true};
    const config=await api('/ui/config',{},signal);state.config=config;
    const [health,status]=await Promise.all([
      api('/health',{},signal).then(data=>data.status==='ok').catch(error=>{if(error.name==='AbortError')throw error;return false;}),
      api('/data/status',{section:'routes'},signal).catch(error=>{if(error.name==='AbortError')throw error;return null;})
    ]);
    return {config,health,status};
  },data=>{
    if(data.demo)return empty('You’re exploring the demo','All data in this mode is synthetic. Searches, saved stops and navigation work without touching your database.','check');
    if(data.preview)return empty('Standalone preview','You can try all screens in demo mode. For real arrivals and routes, run Setup.cmd and open the app on its configured port.','info','<button class="button secondary" data-demo>Try demo</button>');
    const config=data.config.configured||{};
    return `<div class="notice">${icon(data.health?'check':'info')}<span>${data.health?'Database connection check passed.':'The connection check did not pass. Some features may be unavailable.'} A configured connection does not guarantee data is available.</span></div><div class="connection-grid">${[['sql','Stop & route data','Nearby stops and route searches'],['mongo','Arrival storage','Arrival cache and historical observations'],['lta','Live bus provider','On-demand bus arrival updates']].map(([key,label,description])=>`<article class="panel connection-card"><div class="connection-label">${label}</div><strong class="connection-value">${config[key]?'Configured':'Not configured'}</strong><p>${description}</p></article>`).join('')}</div>`+routeCoverage(data.status?.routes||{status:'unavailable'});
  });
}
function setMenu(open){$('#sidebar').classList.toggle('open',open);$('#menu-scrim').hidden=!open;$('#menu-toggle').setAttribute('aria-expanded',String(open));if(open)$('.nav-link[aria-current=page]')?.focus();}
function navigate(focus=false){
  const requested=location.hash.slice(1),page=Object.hasOwn(pages,requested)?requested:'arrivals';
  state.page=page;$$('.view').forEach(el=>el.hidden=el.id!==`view-${page}`);
  $$('.nav-link').forEach(link=>{const active=link.dataset.page===page;if(active)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');$('.nav-arrow',link).textContent=active?'›':'';});
  $('#breadcrumb').textContent=pages[page][0];$('#page-title').textContent=pages[page][0];document.title=`WhereDaBus SG · ${pages[page][0]}`;
  setMenu(false);clearTimeout(state.timer);if(page==='arrivals')scheduleRefresh();if(page==='saved')renderSaved();if(page==='journeys' && modeInitialized)journeys.list();if(page==='status')loadStatus();insights.enter(page);
  if(focus){$('#main').focus({preventScroll:true});window.scrollTo({top:0,behavior:'instant'});}
}
let userInteracted=false;
function setMode(mode,initial=false){
  if(!initial)userInteracted=true;if(!initial&&state.mode===mode)return;
  modeInitialized = true;
  cancelRequests();clearTimeout(state.timer);
  feedback.reset();journeys.reset();comparedTrip=null;state.mode=mode;state.activeStop=null;state.auto=false;state.stopNames.clear();
  $('#live-mode').setAttribute('aria-pressed',String(mode==='live'));$('#demo-mode').setAttribute('aria-pressed',String(mode==='demo'));
  $('#journeys-scope').textContent = mode === 'demo' ? 'Demo journeys are stored in this browser.' : 'Saved in PostgreSQL for this browser. Reopen a journey to get updated boarding estimates.';
  $('#mode-notice').hidden=mode==='live'&&state.config?.ready;
  $('#mode-notice').innerHTML=mode==='demo'?`${icon('info')}<span><strong>You’re in demo mode.</strong> All stops, services and estimates shown here are synthetic examples.</span>`:`${icon('info')}<span>${state.config?.mode==='preview'?'Standalone preview. Choose “Try demo” to explore the interface.':'Live data connects to your bus data service. Check <a href="#status">Help &amp; status</a> if unavailable.'}</span>`;
  $$('form').forEach(form=>form.reset());for(const side of ['origin','destination']){$(`#direct-${side}-code`).value='';$(`#direct-${side}-matches`).innerHTML='';$(`#direct-${side}-selected`).textContent='';$(`#direct-${side}-selected`).hidden=true;}
  if(mode==='demo'){$('#arrival-stop').value='01001';$('#nearby-lat').value='1.300';$('#nearby-lon').value='103.800';$('#road-name').value='Demo Road';state.stopNames.set('01001','Demo Campus');}
  $('#location-message').textContent=mode==='demo'?'Demo stops are near 1.300, 103.800. Your actual location is not needed.':'Location is only requested when you choose “Use my location”.';$('#locate-button').disabled=mode==='demo';
  initialiseEmpty();insights.reset();$('#status-result').innerHTML='';renderSaved();navigate();
  if(mode==='demo')loadArrivals('01001','Demo Campus');if(!initial)toast(mode==='demo'?'Demo mode · synthetic examples':'Live data mode');
}

function invalidateReport(region){
  state.controllers.get(region)?.abort();state.controllers.delete(region);state.requests.delete(region);state.retry.delete(region);
  document.getElementById(region)?.removeAttribute('aria-busy');
  const form=document.getElementById(region.replace('-result','-form'));if(form)$$('button[type=submit]',form).forEach(b=>b.disabled=false);
}
const insights=createInsights({api,run,getMode:()=>state.mode,routeRequest,routeCoverage,invalidate:invalidateReport});
const journeys = createJourneys({getMode: () => state.mode, getSnapshot: () => comparedTrip, run, toast,
  open(row) {
    chooseDirectStop('origin',row.origin,row.originName || row.origin,'');
    chooseDirectStop('destination',row.destination,row.destinationName || row.destination,'');
    $('#direct-date-from').value='';$('#direct-date-to').value='';
    location.hash='direct';
    searchDirect();
  }
});
for (const field of ['#direct-date-from','#direct-date-to']) {
  $(field).max = sgDate();
  $(field).addEventListener('change', () => {
    invalidateDirect();
    $('#direct-result').textContent='Select Find a route to apply these historical dates.';
  });
}
$('#direct-result').addEventListener('click',event => {
  const button = event.target.closest('button');
  if (button?.hasAttribute('data-compare-refresh')) searchDirect();
  if (button?.hasAttribute('data-compare-export') && comparedTrip) downloadComparison(comparedTrip,state.mode === 'demo');
});

$('#arrivals-form').addEventListener('submit',async event=>{event.preventDefault();const query=$('#arrival-stop').value.trim();if(!query)return;clearTimeout(state.timer);if(/^\d{5}$/.test(query)){loadArrivals(query);return;}state.activeStop=null;await run('arrival-result',signal=>api('/stops/search',{query,limit:30},signal),rows=>{const list=safeArray(rows);return list.length?`<p class="result-summary">${list.length} matching stop${list.length===1?'':'s'}${list.length===30?' · Showing the first 30. Refine your search for more specific results.':''}</p>`+stopCards(list):empty('No matching stops','Try a five-digit stop code, another name or a road. Stop-name search requires imported stop data.','search');},event.currentTarget);});
$('#nearby-form').addEventListener('submit',event=>{event.preventDefault();const params=Object.fromEntries(new FormData(event.currentTarget));run('nearby-result',signal=>api('/stops/nearby',params,signal),data=>`<p class="result-summary">${safeArray(data).length} stops within ${number(params.radius)} metres · Distances are straight-line estimates.</p>`+stopCards(safeArray(data)),event.currentTarget);});
$('#direct-form').addEventListener('submit',event=>{event.preventDefault();searchDirect();});
$('#road-form').addEventListener('submit',event=>{event.preventDefault();const name=$('#road-name').value.trim();if(!name)return;run('road-result',signal=>routeRequest('/routes/road',{name},signal),data=>routesWithCoverage(data,'road'),event.currentTarget);});
for(const side of ['origin','destination']){
  $(`#direct-${side}`).addEventListener('input',()=>{$(`#direct-${side}-code`).value='';$(`#direct-${side}-selected`).textContent='';$(`#direct-${side}-selected`).hidden=true;clearDirectMatches(side);invalidateDirect();});
  $(`#direct-${side}-search`).addEventListener('click',()=>searchDirectStop(side));
}
$('#swap-stops').addEventListener('click',()=>{
  invalidateDirect();for(const side of ['origin','destination'])clearDirectMatches(side);
  for(const suffix of ['','-code']){const el=$(`#direct-origin${suffix}`),other=$(`#direct-destination${suffix}`),value=el.value;el.value=other.value;other.value=value;}
  const from=$('#direct-origin-selected'),to=$('#direct-destination-selected'),label=from.textContent,hidden=from.hidden;from.textContent=to.textContent;from.hidden=to.hidden;to.textContent=label;to.hidden=hidden;toast('Starting and destination stops swapped.');
});
$('#locate-button').addEventListener('click',()=>{if(!navigator.geolocation){$('#location-message').textContent='This browser cannot access your location. Enter coordinates instead.';return;}const button=$('#locate-button');const mode=state.mode;button.disabled=true;$('#location-message').textContent='Finding your location…';navigator.geolocation.getCurrentPosition(position=>{if(state.mode!==mode)return;button.disabled=false;$('#nearby-lat').value=position.coords.latitude.toFixed(6);$('#nearby-lon').value=position.coords.longitude.toFixed(6);$('#location-message').textContent='Location added. Select “Find nearby stops” to search.';},error=>{if(state.mode!==mode)return;button.disabled=false;$('#location-message').textContent=error.code===1?'Location permission was not granted. You can enter coordinates instead.':'Your location could not be found. Enter coordinates or try again.';},{enableHighAccuracy:false,timeout:10000,maximumAge:60000});});
$('#status-refresh').addEventListener('click',loadStatus);
$('#live-mode').addEventListener('click',()=>setMode('live'));$('#demo-mode').addEventListener('click',()=>setMode('demo'));
$('#menu-toggle').addEventListener('click',()=>setMenu(!$('#sidebar').classList.contains('open')));$('#menu-scrim').addEventListener('click',()=>{setMenu(false);$('#menu-toggle').focus();});
document.addEventListener('click',event=>{const button=event.target.closest('button');if(!button)return;if(button.hasAttribute('data-save'))toggleSave(button.dataset.save,button.dataset.name);if(button.hasAttribute('data-arrivals'))goToArrivals(button.dataset.arrivals,button.dataset.name);if(button.hasAttribute('data-arrival-refresh')&&state.activeStop)loadArrivals(state.activeStop);if(button.hasAttribute('data-retry'))state.retry.get(button.dataset.retry)?.();if(button.hasAttribute('data-demo'))setMode('demo');if(button.dataset.directCode)chooseDirectStop(button.dataset.directSide,button.dataset.directCode,button.dataset.name,button.dataset.road);});
document.addEventListener('change',event=>{if(event.target.id==='auto-refresh'){state.auto=event.target.checked;scheduleRefresh();}});
document.addEventListener('keydown',event=>{if(!$('#sidebar').classList.contains('open'))return;if(event.key==='Escape'){setMenu(false);$('#menu-toggle').focus();}if(event.key==='Tab'){const items=$$('a', $('#sidebar'));const first=items[0],last=items.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}});
document.addEventListener('visibilitychange',()=>scheduleRefresh());window.addEventListener('hashchange',()=>navigate(true));
window.addEventListener('storage',()=>{renderSaved();updateSaveButtons();});
document.addEventListener('input',()=>{userInteracted=true;});
$('.skip-link').addEventListener('click',event=>{event.preventDefault();$('#main').focus();});
initialiseEmpty();renderSaved();navigate();
try{const controller=new AbortController();const timer=setTimeout(()=>controller.abort(),5000);try{const response=await fetch('/ui/config',{signal:controller.signal});if(response.ok)state.config=await response.json();}finally{clearTimeout(timer);}}catch{/* A readable shell remains usable during an API outage. */}
const requestedMode=new URLSearchParams(location.search).get('mode');
if(!userInteracted)setMode(requestedMode==='demo'||state.config?.mode==='preview'?'demo':'live',true);
else if (!modeInitialized) {modeInitialized=true;if(state.page==='journeys')journeys.list();}
