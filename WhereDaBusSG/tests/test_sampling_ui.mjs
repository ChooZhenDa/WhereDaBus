import test from 'node:test';
import assert from 'node:assert/strict';
import {samplingOptions, samplingChoice} from '../backend/web/sampling.js';
import {demoRequest} from '../backend/web/demo.js';

test('named options retain leading zeroes and safely discard invalid codes and services', () => {
  const result = samplingOptions({stops: [
    {stopCode:'01001',description:'Campus',roadName:'Road',services:['15','15','7A','bad input']},
    {stopCode:1001,services:['15']}, {stopCode:'01001',description:'duplicate'}, null,
  ]});
  assert.deepEqual(result.stops, [{stopCode:'01001',description:'Campus',roadName:'Road',services:['15','7A']}]);
});

test('first tracked stop and its real service are selected without typed codes', () => {
  const stops = samplingOptions({stops:[{stopCode:'01001',description:'Campus',services:['15','7A']}]}).stops;
  assert.deepEqual(samplingChoice(stops), {stopCode:'01001',services:['15','7A'],service:'15'});
});

test('refresh preserves a valid chosen stop and service but replaces removed choices', () => {
  const stops = samplingOptions({stops:[
    {stopCode:'01001',services:['15']}, {stopCode:'01002',services:['7A','20']},
  ]}).stops;
  assert.equal(samplingChoice(stops,'01002','20').service,'20');
  assert.equal(samplingChoice(stops,'01002','15').service,'7A');
  assert.equal(samplingChoice(stops,'99999','15').stopCode,'01001');
});

test('missing route coverage never invents a bus service or a tracked stop', () => {
  const stops = samplingOptions({stops:[{stopCode:'01001'}]}).stops;
  assert.deepEqual(samplingChoice(stops), {stopCode:'01001',services:[],service:''});
  assert.deepEqual(samplingChoice([]), {stopCode:'',services:[],service:''});
  assert.deepEqual(samplingOptions(null), {stops:[],warning:''});
});

test('demo named options stay synthetic, cover known services and cannot mutate fixtures', async () => {
  const first = await demoRequest('/sampling/options');
  assert.equal(first.source,'SYNTHETIC demo');
  assert.equal(first.stops[0].description,'Demo Campus');
  assert.deepEqual(first.stops[0].services,['DEMO1','DEMO2']);
  first.stops[0].services.push('BROKEN');
  assert.deepEqual((await demoRequest('/sampling/options')).stops[0].services,['DEMO1','DEMO2']);
});
