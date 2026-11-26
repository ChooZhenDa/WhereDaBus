/* Synthetic UI examples only. This module never writes to a database. */
import {sgDate, validDateRange} from './insights-model.js';
const stops = [
  {stop_code: '01001', description: 'Demo Campus', road_name: 'Demo Road', latitude: 1.300, longitude: 103.800},
  {stop_code: '01002', description: 'Demo Library', road_name: 'Demo Road', latitude: 1.301, longitude: 103.801},
  {stop_code: '01003', description: 'Demo Station', road_name: 'Test Avenue', latitude: 1.302, longitude: 103.802},
];
const routes = [
  {service_no: 'DEMO1', operator: 'DEMO', direction: 1, origin_code: '01001', destination_code: '01001', route_length_km: 4, stop_count: 4,
    origin_description: 'Demo Campus', destination_description: 'Demo Campus', loop_description: 'Demo Loop', codes: ['01001', '01002', '01003', '01001'], distances: [0, 1, 2.5, 4]},
  {service_no: 'DEMO2', operator: 'DEMO', direction: 1, origin_code: '01001', destination_code: '01003', route_length_km: 3, stop_count: 2,
    origin_description: 'Demo Campus', destination_description: 'Demo Station', loop_description: '', codes: ['01001', '01003'], distances: [0, 3]},
];
const iso = milliseconds => new Date(milliseconds).toISOString();
const clone = value => JSON.parse(JSON.stringify(value));
const publicRoute = ({codes, distances, ...route}) => ({...route,route_coverage:'no_detected_gaps',observed_stop_count:route.stop_count,known_distance_span_km:route.route_length_km});

function distanceMetres(lat, lon, stop) {
  const radians = degrees => degrees * Math.PI / 180;
  const a = Math.sin(radians(stop.latitude - lat) / 2) ** 2 +
    Math.cos(radians(lat)) * Math.cos(radians(stop.latitude)) * Math.sin(radians(stop.longitude - lon) / 2) ** 2;
  return 6371000 * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

function observations(code, now) {
  if (!stops.some(stop => stop.stop_code === code)) return [];
  return routes.filter(route => route.codes.includes(code)).map((route, index) => ({
    meta: {stopCode: code, serviceNo: route.service_no, operator: route.operator},
    observedAt: iso(now), source: 'SYNTHETIC demo',
    arrivals: [1, 2, 3].map(rank => {
      const interval = ((index ? 8 : 3) + (rank - 1) * 9) * 60000;
      const eta = iso(Math.floor(now / 60000) * 60000 + interval);
      const seconds = (Date.parse(eta) - now) / 1000;
      const load = ['SEA', 'SDA', 'LSD'][(rank + index - 1) % 3];
      return {rank, eta, feedbackKey:[code, route.service_no, eta].join('|'), etaSeconds: seconds, etaSecondsNow: seconds,
        load, loadScore: {SEA: 0, SDA: 1, LSD: 2}[load], vehicleType: index ? 'SD' : 'DD',
        feature: 'WAB', monitored: 1, originCode: route.origin_code, destinationCode: route.destination_code,
        visitNumber: 1, location: null};
    }),
  }));
}

function sevenDayWindow(now) {
  const singaporeOffset = 8 * 60 * 60 * 1000;
  const end = Math.floor((now + singaporeOffset) / 86400000) * 86400000 - singaporeOffset;
  return {start: end - 7 * 86400000, end};
}

function hasCohort(params) {
  return routes.some(route => route.service_no.toLowerCase() === String(params.service || '').toLowerCase() && route.codes.includes(params.stop));
}

function metrics(score, count = 40) {
  const limited = Math.round(count * score * 0.4);
  const standing = Math.round(count * score) - 2 * limited;
  const seats = count - standing - limited;
  return {observations: count, validSamples: count, scoreTotal: standing + 2 * limited,
    seatsAvailable: seats, standingAvailable: standing, limitedStanding: limited,
    meanCrowdingOrdinal: (standing + 2 * limited) / count,
    seatsAvailablePct: seats / count * 100, standingAvailablePct: standing / count * 100,
    limitedStandingPct: limited / count * 100, validSamplePct: 100,
    daysWithSamples: 5, daysWithValidSamples: 5};
}

export async function demoRequest(path, params = {}) {
  const now = Date.now();
  if (params instanceof URLSearchParams) params = Object.fromEntries(params);
  if (path === '/ui/config') return {mode: 'preview', configured: {sql: false, mongo: false, lta: false}, ready: false, timezone: 'Asia/Singapore'};
  if (path === '/health') return {status: 'ok', source: 'SYNTHETIC demo'};
  if (path === '/sampling/options') return clone({configured:true,source:'SYNTHETIC demo',services:[],stops:stops.map(stop=>({stopCode:stop.stop_code,description:stop.description,roadName:stop.road_name,services:routes.filter(route=>route.codes.includes(stop.stop_code)).map(route=>route.service_no)}))});
  if (path === '/stops/search') {
    const query = String(params.query || '').trim().toLowerCase();
    return clone(stops.filter(stop => [stop.stop_code, stop.description, stop.road_name].some(value => value.toLowerCase().includes(query))));
  }
  if (path === '/stops/nearby') {
    const lat = Number(params.lat), lon = Number(params.lon), radius = Number(params.radius ?? 500);
    return clone(stops.map(stop => ({...stop, distance_m: distanceMetres(lat, lon, stop)}))
      .filter(stop => stop.distance_m <= radius).sort((a, b) => a.distance_m - b.distance_m));
  }
  if (path === '/routes/examples') return clone(routes.map(route => ({service_no:route.service_no,direction:route.direction,origin_code:route.codes[0],origin_description:stops.find(stop=>stop.stop_code===route.codes[0])?.description,destination_code:route.codes.find(code=>code!==route.codes[0]),destination_description:stops.find(stop=>stop.stop_code===route.codes.find(code=>code!==route.codes[0]))?.description})));
  if (path === '/routes/longest') return clone(routes.map(publicRoute));
  if (path === '/routes/compare') {
    const direct = await demoRequest('/routes/direct', params);
    const atStop = observations(params.origin, now);
    let period = sevenDayWindow(now);
    if (params.date_from || params.date_to) {
      const error = validDateRange(params.date_from || '',params.date_to || '',now);
      if (error) throw new Error(error);
      period = {start:Date.parse(params.date_from+'T00:00:00+08:00'),
        end:Math.min(Date.parse(params.date_to+'T00:00:00+08:00')+86400000,now)};
    }
    const items = [];
    for (const row of direct) {
      const sample = atStop.find(entry => entry.meta.serviceNo === row.service_no)?.arrivals[0];
      const peak = await demoRequest('/analytics/peak', {...params, service:row.service_no,stop:params.origin});
      const counts = peak.rows[0];
      const enough = counts?.validSamples >= 30 && counts.daysWithValidSamples >= 3;
      items.push({...row,
        fare: row.distance_km <= 3.2 ? {status:'available',cents:128,currency:'SGD'} :
          {status:'unavailable',cents:null,currency:'SGD',reason:'No demo fare for this distance.'},
        live: {status:sample ? 'available' : 'no_prediction',eta:sample?.eta ?? null,load:sample?.load ?? null,monitored:sample?.monitored ?? null,
          feedbackKey: sample?.feedbackKey ?? null},
        history: {status:enough ? 'available' : 'limited',validSamples:counts?.validSamples ?? 0,observations:counts?.observations ?? 0,
          daysWithValidSamples:counts?.daysWithValidSamples ?? 0,seatsAvailablePct:enough ? counts.seatsAvailablePct : null},
      });
    }
    return {origin:params.origin,destination:params.destination,asOf:iso(now),liveStatus:'available',
      capturedAt:iso(now),freshUntil:iso(now+20000),source:'SYNTHETIC demo',
      historyWindow:{dateFrom:params.date_from || sgDate(period.start),dateTo:params.date_to || sgDate(period.end-1),
        start:iso(period.start),endExclusive:iso(period.end),timezone:'Asia/Singapore',startHour:7,endHour:9},
      routes:items.sort((a,b)=>(Date.parse(a.live.eta)||Infinity)-(Date.parse(b.live.eta)||Infinity))};
  }
  if (path === '/routes/road') {
    const road = String(params.name || '').trim().toLowerCase();
    if (!road) return [];
    const codes = new Set(stops.filter(stop => stop.road_name.toLowerCase().includes(road)).map(stop => stop.stop_code));
    return clone(routes.filter(route => route.codes.some(code => codes.has(code))).map(publicRoute));
  }
  if (path === '/routes/direct') {
    const result = [];
    for (const route of routes) {
      // The first origin and first later destination preserve forward travel on a loop.
      const start = route.codes.indexOf(params.origin);
      const end = route.codes.findIndex((code, index) => index > start && code === params.destination);
      if (start >= 0 && end > start) result.push({...publicRoute(route), origin_code: params.origin, destination_code: params.destination,
        origin_description: stops.find(stop => stop.stop_code === params.origin)?.description,
        destination_description: stops.find(stop => stop.stop_code === params.destination)?.description,
        origin_sequence: start + 1, destination_sequence: end + 1, stops_between: end - start,
        segment_coverage:'no_detected_gaps',observed_stops_between:end-start,
        distance_km: route.distances[end] - route.distances[start]});
    }
    return clone(result);
  }
  if (path.startsWith('/arrivals/') || path.startsWith('/sampled-arrivals/')) {
    const code = path.split('/').pop();
    const sampled = path.startsWith('/sampled-arrivals/');
    const rows = observations(code, now);
    return {status: rows.length ? 'available' : sampled ? 'unavailable' : 'no_data', stopCode: code,
      capturedAt: iso(now), freshUntil: iso(now + 20000), expiresAt: iso(now + 120000),
      ageSeconds: 0, refreshAfterSeconds: 20, observations: rows, source: 'SYNTHETIC demo'};
  }
  if (path === '/analytics/peak' || path === '/analytics/trend') {
    let {start,end}=sevenDayWindow(now);
    if(params.date_from || params.date_to){
      const error=validDateRange(params.date_from||'',params.date_to||'',now);if(error)throw new Error(error);
      start=Date.parse(`${params.date_from}T00:00:00+08:00`);
      end=Math.min(Date.parse(`${params.date_to}T00:00:00+08:00`)+86400000,now);
    }
    const points=[];
    if(hasCohort(params))for(let day=start;day<end;day+=86400000){
      const scores=[0.35,0.65,1.05,1.45,1.15,0.85,0.65,0.4];
      for(const [index,score] of scores.entries()){
        const timestamp=day+7*3600000+index*1800000;
        if(timestamp>=end)continue;
        points.push({_id:{bucket:iso(timestamp),operator:'DEMO'},...metrics(score),daysWithSamples:1,daysWithValidSamples:1,source:'SYNTHETIC demo'});
      }
    }
    if(path==='/analytics/trend')return points.map((row,index)=>{
      const window=points.slice(Math.max(0,index-2),index+1);
      const rollingScore=window.reduce((s,r)=>s+r.scoreTotal,0),rollingValid=window.reduce((s,r)=>s+r.validSamples,0);
      return {...row,rollingScore,rollingValid,rollingMeanOrdinal:rollingValid?rollingScore/rollingValid:null};
    });
    const peak=points.filter(row=>{
      const local=new Date(Date.parse(row._id.bucket)+8*3600000);
      return local.getUTCDay()>=1&&local.getUTCDay()<=5&&local.getUTCHours()>=7&&local.getUTCHours()<9;
    });
    const totals={observations:0,validSamples:0,scoreTotal:0,seatsAvailable:0,standingAvailable:0,limitedStanding:0};
    for(const row of peak)for(const key of Object.keys(totals))totals[key]+=row[key];
    const days=new Set(peak.map(row=>sgDate(Date.parse(row._id.bucket)))).size;
    const row={_id:{stopCode:params.stop,serviceNo:String(params.service).toUpperCase(),operator:'DEMO'},...totals,
      meanCrowdingOrdinal:totals.validSamples?totals.scoreTotal/totals.validSamples:null,
      validSamplePct:totals.observations?100*totals.validSamples/totals.observations:null,
      seatsAvailablePct:totals.validSamples?100*totals.seatsAvailable/totals.validSamples:null,
      standingAvailablePct:totals.validSamples?100*totals.standingAvailable/totals.validSamples:null,
      limitedStandingPct:totals.validSamples?100*totals.limitedStanding/totals.validSamples:null,
      daysWithSamples:days,daysWithValidSamples:days};
    return {start:iso(start),endExclusive:iso(end),timezone:'Asia/Singapore',
      metric:'Ordinal crowding 0=SEA, 1=SDA, 2=LSD; not occupancy percent',source:'SYNTHETIC demo',rows:peak.length?[row]:[]};
  }
  throw new Error('This feature is not available in the demo.');
}
