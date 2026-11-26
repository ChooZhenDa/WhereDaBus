import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {routeCoverage, routeFacts} from '../backend/web/route-coverage.js';
import {comparisonFacts,comparisonHeading} from '../backend/web/comparison.js';
import {requestJourneys,saveJourneyForm} from '../backend/web/journeys.js';
import {feedbackButton,feedbackRequest} from '../backend/web/feedback.js';
import {sgDate,validDateRange} from '../backend/web/insights-model.js';

// Run the real app event handlers with a small DOM boundary. Pending requests
// intentionally ignore AbortSignal so token invalidation is also exercised.
const source = (await readFile(new URL('../backend/web/app.js', import.meta.url),'utf8'))
  .replace(/^import .*;\r?\n/gm,'').split('initialiseEmpty();renderSaved();navigate();')[0];
function harness(t) {
  const elements = new Map(), timers = new Set();
  const element = key => {
    if (!elements.has(key)) elements.set(key, {
      id:key.replace(/^#/,''),value:'',textContent:'',innerHTML:'',hidden:false,disabled:false,
      dataset:{},attributes:new Map(),listeners:new Map(),
      addEventListener(type,handler){this.listeners.set(type,handler);},
      setAttribute(key,value){this.attributes.set(key,value);},
      removeAttribute(key){this.attributes.delete(key);},
      contains(){return false;},focus(){},reset(){},
      classList:{toggle(){},contains(){return false;}},
      querySelector(){return null;},
      querySelectorAll(selector){return key==='#direct-form'&&selector==='button[type=submit]'?[element('#submit')]:[];},
    });
    return elements.get(key);
  };
  const document={querySelector:element,querySelectorAll:()=>[],addEventListener(){},activeElement:null,body:{},hidden:false,getElementById:id=>element('#'+id)};
  const context=vm.createContext({document,window:{addEventListener(){},scrollTo(){}},location:{hash:'#direct',search:''},navigator:{},
    localStorage:{getItem(){return null;}},AbortController,URLSearchParams,Intl,Date,console,routeCoverage,routeFacts,comparisonFacts,comparisonHeading,
    createJourneys:()=>({list(){},reset(){}}),createFeedback:()=>({refresh(){},reset(){}}),feedbackButton,
    saveJourneyForm,sgDate,validDateRange,createInsights:()=>({enter(){},reset(){}}),
    setTimeout(fn,delay){const timer=setTimeout(fn,delay);timers.add(timer);return timer;},clearTimeout,
  });
  vm.runInContext(source+'\nglobalThis.hooks={state,run,routesMarkup,routesWithCoverage,chooseDirectStop,setMode};',context);
  t.after(()=>{for(const timer of timers)clearTimeout(timer);});
  return {element,...context.hooks};
}
const coverage={status:'available',serviceDirections:4,availableDirections:3,gapFreeDirections:2,partialDirections:1,unavailableDirections:1,
  partialExamples:[{serviceNo:'100',direction:1}],unavailableExamples:[{serviceNo:'200',direction:2}]};
const direct={service_no:'100',operator:'BUS',direction:1,origin_description:'Origin',destination_description:'Destination',
  segment_coverage:'no_detected_gaps',route_coverage:'no_detected_gaps',distance_km:2.5,stops_between:3,observed_stops_between:3};

test('partial direct results hide exact totals even if inconsistent fields arrive',t=>{
  const app=harness(t);
  const html=app.routesMarkup([{...direct,segment_coverage:'partial',route_coverage:'partial',distance_km:987,stops_between:876,observed_stops_between:2}],'direct');
  assert.match(html,/Partial route data/);
  assert.match(html,/Stop count and distance are unavailable/);
  assert.doesNotMatch(html,/route-distance|987|876/);
  assert.match(html,/Recorded stops after boarding<\/dt><dd>2/);
  assert.doesNotMatch(html,/no_detected_gaps/);
});

test('a usable segment keeps its distance while identifying gaps elsewhere',t=>{
  const app=harness(t);
  const html=app.routesMarkup([{...direct,route_coverage:'partial'}],'direct');
  assert.match(html,/No gaps were detected in your selected section/);
  assert.match(html,/2\.5 <small>km/);
  assert.doesNotMatch(html,/<strong>Partial route data/);
});

test('partial road routes distinguish recorded extent from unknown full totals',t=>{
  const app=harness(t);
  const html=app.routesMarkup([{service_no:'100',route_coverage:'partial',route_length_km:999,stop_count:777,observed_stop_count:9,known_distance_span_km:4.5,
    origin_description:'First recorded',destination_description:'Last recorded'}],'road');
  assert.match(html,/first and last recorded stops/);
  assert.match(html,/Recorded route stops<\/dt><dd>9/);
  assert.match(html,/Distance between recorded endpoints \(km\)<\/dt><dd>4.5/);
  assert.doesNotMatch(html,/route-distance|999|777/);
});

test('results precede compact collapsed coverage and provider strings are escaped',t=>{
  const app=harness(t);
  const html=app.routesWithCoverage({rows:[{...direct,service_no:'<img onerror="bad">'}],coverage:{...coverage,partialExamples:[{serviceNo:'<script>',direction:'<b>'}]}},'direct');
  assert(html.indexOf('route-card')<html.indexOf('class="route-coverage"'));
  assert.match(html,/<details class="route-coverage"><summary>/);
  assert.match(html,/3 of 4 directions have route data · 1 partial · 1 unavailable/);
  assert.match(html,/&lt;img onerror=&quot;bad&quot;&gt;/);
  assert.match(html,/&lt;script&gt;/);
  assert.doesNotMatch(html,/<img|<script>|<b>/);
});

test('older or unavailable status never claims complete coverage',()=>{
  const legacy=routeCoverage({status:'available',serviceDirections:804,availableDirections:639,unavailableDirections:165});
  assert.match(legacy,/coverage details limited/);
  assert.doesNotMatch(legacy,/complete imported route coverage|no detected gaps/);
  assert.match(routeCoverage({status:'unavailable'}),/could not be checked/);
});

test('editing a stop clears selected metadata and invalidates a late route response',async t=>{
  const app=harness(t);app.chooseDirectStop('origin','01001','Origin','Demo Road');
  let resolve;
  const pending=app.run('direct-result',()=>new Promise(r=>resolve=r),()=>'<p>OLD RESULT</p>',app.element('#direct-form'));
  const controller=app.state.controllers.get('direct-result');
  app.element('#direct-origin').value='Changed';app.element('#direct-origin').listeners.get('input')();
  resolve([]);await pending;
  assert(controller.signal.aborted);
  assert.equal(app.element('#direct-origin-code').value,'');
  assert.equal(app.element('#direct-origin-selected').hidden,true);
  assert.equal(app.element('#submit').disabled,false);
  assert.equal(app.state.retry.has('direct-result'),false);
  assert.equal(app.element('#direct-result').attributes.has('aria-busy'),false);
  assert.match(app.element('#direct-result').innerHTML,/Stops changed/);
  assert.doesNotMatch(app.element('#direct-result').innerHTML,/OLD RESULT/);
});

test('swap preserves visible road/code identity and suppresses stale name matches',async t=>{
  const app=harness(t);app.chooseDirectStop('origin','01001','Campus','First Road');app.chooseDirectStop('destination','01002','Library','Second Road');
  const resolvers=[];
  const pending=['origin','destination'].map(side=>app.run(`direct-${side}-matches`,()=>new Promise(r=>resolvers.push(r)),()=>'<button>OLD CHOICE</button>'));
  app.element('#swap-stops').listeners.get('click')();
  resolvers.forEach(resolve=>resolve([]));await Promise.all(pending);
  assert.equal(app.element('#direct-origin').value,'Library');
  assert.equal(app.element('#direct-origin-code').value,'01002');
  assert.equal(app.element('#direct-origin-selected').textContent,'Second Road · 01002');
  assert.equal(app.element('#direct-destination-selected').textContent,'First Road · 01001');
  for(const side of ['origin','destination'])assert.equal(app.element(`#direct-${side}-matches`).innerHTML,'');
});

test('choosing a new named stop cancels the old journey and writes road as text',async t=>{
  const app=harness(t);let resolve;
  const pending=app.run('direct-result',()=>new Promise(r=>resolve=r),()=>'<p>OLD JOURNEY</p>');
  app.chooseDirectStop('destination','01003','Station','<unsafe> Road');resolve([]);await pending;
  assert.equal(app.element('#direct-destination-code').value,'01003');
  assert.equal(app.element('#direct-destination-selected').textContent,'<unsafe> Road · 01003');
  assert.equal(app.element('#direct-destination-selected').innerHTML,'');
  assert.doesNotMatch(app.element('#direct-result').innerHTML,/OLD JOURNEY/);
});

test('empty direct results explain uncertainty without claiming no bus exists',t=>{
  const app=harness(t);
  assert.match(app.routesMarkup([],'direct'),/Missing route data may hide a service; transfers are not included/);
});

test('comparisons keep stale/ambiguous estimates and limited history out of commuter claims',()=>{
  const at='2026-11-19T00:00:00Z';
  const service={live:{status:'available',eta:'2026-11-19T00:03:00Z',load:'SEA',monitored:1},
    history:{status:'available',validSamples:40,observations:48,daysWithValidSamples:4,seatsAvailablePct:75}};
  assert.match(comparisonFacts(service,at),/3 min/);
  assert.match(comparisonFacts(service,at),/75% of eligible observations/);
  for(const status of ['stale','unconfirmed','no_prediction','unavailable']) {
    const html=comparisonFacts({...service,live:{...service.live,status},history:{...service.history,status:'limited'}},at);
    assert.match(html,/Limited history/);
    assert.match(html,/40 eligible of 48 observations across 4 weekday mornings/);
    assert.doesNotMatch(html,/3 min|Seats available|75%/);
  }
});

test('route facts separate labels from values in semantic blocks even before the stylesheet loads',()=>{
  const html=comparisonFacts({fare:{status:'available',cents:128},history:{status:'limited',validSamples:0,observations:0,daysWithValidSamples:0}});
  assert.match(html,/<dt>Adult card fare<\/dt>\s+<dd><p[^>]*>S\$1\.28<\/p>/);
  assert.match(html,/No observations in the selected mornings/);
  assert.doesNotMatch(html,/0 eligible of 0/);
  assert.match(comparisonFacts({fare:{status:'unavailable',reason:'Missing exact distance'}}),/Missing exact distance/);
});

test('demo feedback follows the specific arrival across views and clears only the selected report',async t=>{
  const store=new Map();
  Object.defineProperty(globalThis,'localStorage',{value:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},configurable:true});
  t.after(()=>Reflect.deleteProperty(globalThis,'localStorage'));
  const eta=new Date(Date.now()+600000).toISOString(), key='01001|DEMO1|'+eta;
  const second='01001|DEMO2|'+eta;
  await feedbackRequest('demo','01001','PUT',key,'full');
  await feedbackRequest('demo','01001','PUT',second,'skipped');
  const changed=await feedbackRequest('demo','01001','PUT',key,'delayed');
  assert.deepEqual(changed,{mine:'delayed',counts:{delayed:1}});
  await feedbackRequest('demo','01001','DELETE',key);
  assert.deepEqual(Object.keys((await feedbackRequest('demo','01001')).reports),[second]);
  assert.equal(feedbackButton('01001','DEMO1',{eta}), '');
});

test('saved journeys scope identifiers to same-origin requests and isolate synthetic records',async t=>{
  const store=new Map(), calls=[];
  t.mock.method(globalThis,'fetch',async(url,options)=>{
    calls.push({url,...options});
    return new Response(null,{status:204});
  });
  Object.defineProperty(globalThis,'localStorage',{value:{getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)},configurable:true});
  t.after(()=>Reflect.deleteProperty(globalThis,'localStorage'));
  await requestJourneys('live','GET');
  await requestJourneys('live','DELETE','id');
  assert.equal(calls[0].url,'/journeys');
  assert.equal(calls[1].url,'/journeys/id');
  assert.match(calls[0].headers['X-Journey-Key'],/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i);
  assert.equal(calls[1].headers['X-Journey-Key'],calls[0].headers['X-Journey-Key']);
  const item=await requestJourneys('demo','POST',null,{origin:'01001',destination:'01003',label:'Example'});
  await requestJourneys('demo','PATCH',item.id,{label:'Renamed'});
  assert.equal((await requestJourneys('demo','GET'))[0].label,'Renamed');
  await requestJourneys('demo','DELETE',item.id);
  assert.deepEqual(await requestJourneys('demo','GET'),[]);
  assert.equal(calls.length,2);
});
