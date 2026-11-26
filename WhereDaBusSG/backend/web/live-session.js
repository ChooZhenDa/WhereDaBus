// Real readings received while this browser session is open. Never backfilled.
export function liveScore(bus) {
  const score = {SEA:0,SDA:1,LSD:2}[bus?.load];
  const eta = bus?.etaSecondsNow ?? bus?.etaSeconds;
  return bus?.monitored === 1 && bus?.eta && Number.isFinite(eta) && eta >= 0 && eta <= 1800 && score !== undefined ? score : null;
}

export class LiveSession {
  constructor(maxStops=5,maxPoints=180){this.maxStops=maxStops;this.maxPoints=maxPoints;this.stops=new Map();}
  clear(){this.stops.clear();}
  add(snapshot,now=Date.now()){
    const code=snapshot?.stopCode,time=Date.parse(snapshot?.capturedAt);
    if(!/^[0-9]{5}$/.test(code||'')||!Number.isFinite(time)||!['available','no_data'].includes(snapshot.status))return false;
    const until=Date.parse(snapshot.freshUntil);
    if((Number.isFinite(until)&&until<=now)||time>now+5000||now-time>120000)return false;
    const stored=this.stops.get(code)||{last:0,points:[],services:new Set()};
    if(time<=stored.last)return false;
    const values={};
    for(const row of Array.isArray(snapshot.observations)?snapshot.observations:[]){
      const service=row?.meta?.serviceNo,operator=row?.meta?.operator||'';
      if(!/^[A-Za-z0-9]{1,12}$/.test(service||''))continue;
      const key=service+'|'+operator;stored.services.add(key);
      values[key]=liveScore((Array.isArray(row.arrivals)?row.arrivals:[]).find(bus=>bus.rank===1));
    }
    // Missing/unknown services stay as gaps rather than empty-bus estimates.
    for(const key of stored.services)if(!(key in values))values[key]=null;
    stored.last=time;stored.points.push({time,values});
    if(stored.points.length>this.maxPoints)stored.points.splice(0,stored.points.length-this.maxPoints);
    this.stops.delete(code);this.stops.set(code,stored);
    while(this.stops.size>this.maxStops)this.stops.delete(this.stops.keys().next().value);
    return true;
  }
  services(code){return [...(this.stops.get(code)?.services||[])].sort();}
  series(code,key){return (this.stops.get(code)?.points||[]).map(point=>({time:point.time,score:point.values[key]??null}));}
}
