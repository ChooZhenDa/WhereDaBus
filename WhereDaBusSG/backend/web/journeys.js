import {escapeHTML as esc} from './insights-model.js';

const clientKeyName = 'wheredabus-journey-key-v1';
const demoListName = 'wheredabus-demo-journeys-v1';

function demoRequest(method, id, body) {
  const list = JSON.parse(localStorage.getItem(demoListName) || '[]');
  if (method === 'GET') return list;
  let item = list.find(row => row.id === id);
  if (method === 'POST') {
    if (list.some(row => row.origin === body.origin && row.destination === body.destination)) throw new Error('This journey is already saved.');
    item = {...body, id: crypto.randomUUID()};
    list.unshift(item);
  } else {
    if (!item) throw new Error('This journey is no longer saved.');
    if (method === 'PATCH') item.label = body.label;
    if (method === 'DELETE') list.splice(list.indexOf(item), 1);
  }
  localStorage.setItem(demoListName, JSON.stringify(list));
  return item;
}

export function browserKey(required = true) {
  try {
    let key = localStorage.getItem(clientKeyName);
    if (!key && required) {
      key = crypto.randomUUID();
      localStorage.setItem(clientKeyName, key);
    }
    return key;
  } catch {
    if (!required) return null;
    throw new Error('Allow storage for this site to manage your saved journeys and feedback.');
  }
}

export async function requestJourneys(mode, method, id, body, signal) {
  if (mode === 'demo') return demoRequest(method, id, body);
  const key = browserKey();
  const url = id ? '/journeys/' + encodeURIComponent(id) : '/journeys';
  const headers = {Accept: 'application/json', 'X-Journey-Key': key};
  if (body) headers['Content-Type'] = 'application/json';
  const response = await fetch(url, {method, headers, credentials: 'same-origin', cache: 'no-store', signal,
    body: body ? JSON.stringify(body) : undefined});
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}));
    throw new Error(typeof payload.detail === 'string' ? payload.detail : 'Saved journeys are unavailable. Try again.');
  }
  return response.status === 204 ? null : response.json();
}

export function saveJourneyForm() {
  return `<form class="panel journey-save" data-journey-save><div>
    <label for="new-journey-name">Save this journey as</label>
    <input id="new-journey-name" name="label" placeholder="Morning commute" required maxlength="80"></div>
    <button class="button secondary" type="submit">Save journey</button><a href="#journeys">View saved journeys</a>
    <p class="journey-message" role="status"></p></form>`;
}

function journeyList(rows) {
  if (!rows.length) return '<p class="field-hint">No saved journeys yet. Search for a direct route, then give your journey a name.</p>';
  return rows.map(row => {
    const id = esc(row.id);
    return `<article class="panel saved-journey" data-journey-id="${id}"><h2>${esc(row.label)}</h2>
      <p>${esc(row.originName || row.origin)} (${esc(row.origin)}) → ${esc(row.destinationName || row.destination)} (${esc(row.destination)})</p>
      <div class="comparison-actions"><button type="button" class="button primary" data-journey-open>Compare buses</button>
      <button type="button" class="button secondary" data-journey-edit>Rename</button>
      <button type="button" class="button ghost" data-journey-remove>Delete</button></div>
      <form class="journey-rename" data-journey-rename hidden><div><label for="label-${id}">Journey name</label>
      <input id="label-${id}" name="label" value="${esc(row.label)}" required maxlength="80"></div>
      <button class="button secondary" type="submit">Save name</button>
      <button class="button ghost" type="button" data-journey-cancel>Cancel</button></form>
      <p class="journey-message" role="status"></p></article>`;
  }).join('');
}

export function createJourneys({getMode, getSnapshot, open, run, toast}) {
  let loaded = [];
  let generation = 0;
  function reset() {
    loaded = [];
    generation++;
  }
  function list() {
    const mode = getMode();
    return run('journeys-result', signal => requestJourneys(mode, 'GET', null, null, signal), rows => {
      loaded = rows;
      return journeyList(rows);
    });
  }
  async function change(container, method, id, body, done) {
    if (container.dataset.pending) return;
    const mode = getMode(), version = generation;
    container.dataset.pending = '1';
    const buttons = Array.from(container.querySelectorAll('button'));
    buttons.forEach(b => b.disabled = true);
    const message = container.querySelector('.journey-message') || container.parentNode.querySelector('.journey-message');
    if (message) message.textContent = '';
    try {
      const result = await requestJourneys(mode, method, id, body);
      if (generation === version && getMode() === mode) await done(result);
    } catch (error) {
      if (generation === version && getMode() === mode && message) message.textContent = error.message;
    } finally {
      delete container.dataset.pending;
      buttons.forEach(b => b.disabled = false);
    }
  }
  document.addEventListener('submit', event => {
    const form = event.target;
    if (!form.matches('[data-journey-save], [data-journey-rename]')) return;
    event.preventDefault();
    const input = form.elements.namedItem('label'), label = input.value.trim();
    if (!label) {input.setCustomValidity('Enter a journey name.');input.reportValidity();return;}
    if (form.hasAttribute('data-journey-save')) {
      const trip = getSnapshot();
      if (!trip?.routes?.length) return;
      const {origin, destination} = trip;
      const data = {origin, destination, label};
      if (getMode() === 'demo') {
        data.originName = trip.routes[0].origin_description;
        data.destinationName = trip.routes[0].destination_description;
      }
      change(form, 'POST', null, data, () => {
        toast('Journey saved.');
        if (form.isConnected) form.querySelector('.journey-message').textContent = 'Saved. Reopen it in Saved journeys.';
        if (location.hash === '#journeys') return list();
      });
    } else {
      const card = form.closest('[data-journey-id]');
      change(card, 'PATCH', card.dataset.journeyId, {label}, () => {toast('Journey renamed.');return list();});
    }
  });
  document.addEventListener('input', event => {
    if (event.target.matches('input[name="label"]')) event.target.setCustomValidity('');
  });
  document.addEventListener('click', event => {
    const button = event.target.closest('button');
    const card = button?.closest('[data-journey-id]');
    if (!card) return;
    const id = card.dataset.journeyId, form = card.querySelector('form'), row = loaded.find(item => item.id === id);
    if (button.hasAttribute('data-journey-open') && row) open(row);
    if (button.hasAttribute('data-journey-edit')) {form.hidden = false;form.elements.namedItem('label').focus();}
    if (button.hasAttribute('data-journey-cancel')) {form.reset();form.hidden = true;card.querySelector('[data-journey-edit]').focus();}
    if (button.hasAttribute('data-journey-remove')) change(card, 'DELETE', id, null, () => {toast('Journey deleted.');return list();});
  });
  return {list, reset};
}
