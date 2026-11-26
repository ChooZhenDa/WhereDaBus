import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const {parse} = await import(process.env.YAML_MODULE || 'yaml');
const root = new URL('../', import.meta.url);
const compose = parse(await readFile(new URL('compose.yaml',root),'utf8'));
const dockerfile = await readFile(new URL('Dockerfile',root),'utf8');

test('fresh deployment takes its unique installation identity and port from generated config',()=>{
  assert.equal(compose.name,'${WDB_PROJECT:?Run Setup.cmd first}');
  assert.deepEqual(Object.keys(compose.volumes).sort(),['mongo_data','postgres_data']);
  assert.deepEqual(compose.services.api.ports,['127.0.0.1:${APP_PORT:?Run Setup.cmd first}:8080']);
  for(const volume of Object.values(compose.volumes))assert.equal(volume,null);
});

test('application images and unnamed volumes cannot share the old fixed project',()=>{
  for(const name of ['api','setup','sampler'])assert.equal(compose.services[name].image,'${WDB_PROJECT:?Run Setup.cmd first}-app:local');
  assert.doesNotMatch(JSON.stringify(compose),/wheredabus-fresh|container_name|external/);
});

test('database services expose no host ports and use isolated internal network',()=>{
  for(const name of ['postgres','mongo']){
    assert.equal(compose.services[name].ports,undefined);
    assert.deepEqual(compose.services[name].networks,['database']);
    assert.ok(compose.services[name].healthcheck);
  }
  assert.equal(compose.networks.database.internal,true);
});

test('API and sampler can reach the provider but initialization has no outbound network',()=>{
  assert.deepEqual(compose.services.api.networks,['database','outbound']);
  assert.deepEqual(compose.services.sampler.networks,['database','outbound']);
  assert.deepEqual(compose.services.setup.networks,['database']);
});

test('startup waits for healthy databases and a successful bootstrap',()=>{
  for(const name of ['postgres','mongo'])assert.equal(compose.services.setup.depends_on[name].condition,'service_healthy');
  for(const name of ['api','sampler'])assert.equal(compose.services[name].depends_on.setup.condition,'service_completed_successfully');
  assert.deepEqual(compose.services.setup.command,['python','-m','backend.bootstrap']);
  assert.equal(compose.services.setup.restart,'no');
});

test('all application services share credentials and retain optional analytics',()=>{
  for(const name of ['setup','sampler'])assert.deepEqual(compose.services[name].environment,compose.services.api.environment);
  assert.deepEqual(compose.services.sampler.profiles,['analytics']);
  assert.deepEqual(compose.services.sampler.command,['python','-m','backend.sampler']);
  assert.match(compose.services.api.environment.SQL_DSN,/\$\{POSTGRES_PASSWORD:\?/);
  assert.match(compose.services.api.environment.MONGO_URI,/authSource=admin/);
  assert.equal(compose.services.api.environment.LTA_ACCOUNT_KEY,'${LTA_ACCOUNT_KEY:-}');
});

test('container runs the actual API as a non-root user and copies web and SQL',()=>{
  assert.match(dockerfile,/USER app/);
  assert.match(dockerfile,/COPY --chown=app:app backend \.\/backend/);
  assert.match(dockerfile,/COPY --chown=app:app sql \.\/sql/);
  assert.match(dockerfile,/"backend\.api:app"/);
  assert.doesNotMatch(dockerfile,/COPY\s+\.env/);
});

test('no startup command seeds fixtures or deletes data volumes',()=>{
  const text=JSON.stringify(compose);
  assert.doesNotMatch(text,/seed-demo|down --volumes|down -v/);
  assert.equal(compose.services.postgres.volumes[0],'postgres_data:/var/lib/postgresql/data');
  assert.equal(compose.services.mongo.volumes[0],'mongo_data:/data/db');
});
