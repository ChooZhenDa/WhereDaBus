import {escapeHTML as esc, formatSG} from './insights-model.js';
import {browserKey} from './journeys.js?v=fares-feedback';

const issues = {full:'Bus full', delayed:'Bus delayed', skipped:'Bus skipped stop'};
const demoStore = 'wheredabus-demo-arrival-feedback-v1';

export function feedbackButton(stop, service, arrival) {
  if (!arrival?.feedbackKey || !arrival.eta) return '';
  return `<div class="arrival-feedback" data-feedback-key="${esc(arrival.feedbackKey)}" data-stop="${esc(stop)}"
    data-service="${esc(service)}" data-eta="${esc(arrival.eta)}">
    <p class="rider-reports" hidden></p>
    <button type="button" class="feedback-link" data-open-feedback aria-haspopup="dialog"
      aria-label="Feedback for service ${esc(service)} at ${esc(formatSG(arrival.eta))} SGT">Feedback</button>
  </div>`;
}

function countsText(counts = {}) {
  return Object.entries(issues).filter(([key]) => counts[key] > 0)
    .map(([key, label]) => `${label} (${Number(counts[key])})`).join(' · ');
}

export async function feedbackRequest(mode, stop, method = 'GET', key, issue) {
  if (mode === 'demo') {
    const rows = JSON.parse(localStorage.getItem(demoStore) || '[]')
      .filter(row => Date.parse(row.eta) > Date.now() - 15 * 60_000);
    const summary = row => ({counts:{[row.issue]:1}, mine:row.issue});
    if (method === 'GET') return {reports:Object.fromEntries(rows.filter(r => r.stop === stop)
      .map(row => [row.key, summary(row)]))};
    const remaining = rows.filter(row => !(row.stop === stop && row.key === key));
    if (method === 'PUT') {
      const eta = key.split('|').at(-1);
      if (Date.parse(eta) <= Date.now()) throw new Error('This estimate has passed. Refresh arrivals.');
      remaining.push({stop, key, issue, eta});
    }
    localStorage.setItem(demoStore, JSON.stringify(remaining));
    return method === 'PUT' ? summary({issue}) : {counts:{},mine:null};
  }
  const token = browserKey(method !== 'GET');
  const headers = {Accept:'application/json'};
  if (token) headers['X-Journey-Key'] = token;
  if (issue) headers['Content-Type'] = 'application/json';
  const path = '/feedback/' + encodeURIComponent(stop) + (key ? '/' + encodeURIComponent(key) : '');
  const response = await fetch(path, {method, headers, credentials:'same-origin', cache:'no-store',
    body:issue ? JSON.stringify({issue}) : undefined});
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(typeof error.detail === 'string' ? error.detail : 'Could not load rider feedback. Please try again.');
  }
  return response.json();
}

function drawCounts(container, summary) {
  const output = container.querySelector('.rider-reports');
  const counts = countsText(summary?.counts);
  output.textContent = counts ? 'Riders reported: ' + counts : '';
  output.hidden = !counts;
}

export function createFeedback(getMode) {
  const dialog = document.getElementById('feedback-dialog');
  const form = dialog.querySelector('form');
  const submit = form.querySelector('[type="submit"]');
  const remove = form.querySelector('[data-clear-report]');
  const select = form.elements.issue;
  const message = dialog.querySelector('[role="status"]');
  let selected = null;
  let revision = 0;

  function setBusy(busy) { submit.disabled = remove.disabled = select.disabled = busy; }
  function drawSummary(summary) {
    dialog.querySelector('.feedback-counts').textContent = countsText(summary?.counts) || 'No rider reports for this arrival yet.';
    select.value = summary?.mine || '';
    remove.hidden = !summary?.mine;
    submit.textContent = summary?.mine ? 'Update feedback' : 'Send feedback';
  }
  async function refresh(region, stop) {
    if (!region.querySelector('[data-feedback-key]')) return;
    const mode = getMode(), stamp = revision;
    try {
      const {reports} = await feedbackRequest(mode, stop);
      if (mode !== getMode() || stamp !== revision) return;
      for (const slot of region.querySelectorAll('[data-feedback-key]')) {
        if (slot.dataset.stop === stop) drawCounts(slot, reports[slot.dataset.feedbackKey]);
      }
    } catch { /* Route results remain usable. Opening Feedback displays any API error. */ }
  }
  async function open(slot) {
    const scope = {...slot.dataset, mode:getMode()};
    selected = scope;
    dialog.querySelector('.feedback-arrival').textContent = `Service ${scope.service} · Stop ${scope.stop} · ${formatSG(scope.eta)} SGT`;
    dialog.querySelector('.feedback-counts').textContent = 'Loading rider reports…';
    message.textContent = scope.mode === 'demo' ? 'Demo feedback is stored in this browser.' : '';
    select.value = '';
    remove.hidden = true;
    setBusy(true);
    if (!dialog.open) dialog.showModal();
    try {
      const {reports} = await feedbackRequest(scope.mode, scope.stop);
      if (selected !== scope) return;
      drawSummary(reports[scope.feedbackKey]);
      setBusy(false);
    } catch (error) {
      if (selected !== scope) return;
      dialog.querySelector('.feedback-counts').textContent = '';
      message.textContent = error.message;
    }
  }
  async function change(method) {
    const scope = selected;
    if (!scope || select.disabled) return;
    const issue = method === 'PUT' ? select.value : undefined;
    if (method === 'PUT' && !Object.hasOwn(issues, issue)) return;
    setBusy(true);
    message.textContent = 'Saving…';
    try {
      const result = await feedbackRequest(scope.mode, scope.stop, method, scope.feedbackKey, issue);
      revision++;
      if (scope.mode === getMode()) {
        for (const slot of document.querySelectorAll('[data-feedback-key]')) {
          if (slot.dataset.feedbackKey === scope.feedbackKey && slot.dataset.stop === scope.stop) drawCounts(slot, result);
        }
      }
      if (selected !== scope) return;
      drawSummary(result);
      message.textContent = method === 'DELETE' ? 'Your feedback was cleared.' : 'Your feedback was saved.';
    } catch (error) {
      if (selected === scope) message.textContent = error.message;
    } finally {
      if (selected === scope) setBusy(false);
    }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-open-feedback]');
    if (button) open(button.closest('[data-feedback-key]'));
  });
  form.addEventListener('submit', event => {event.preventDefault();change('PUT');});
  remove.addEventListener('click', () => change('DELETE'));
  dialog.querySelector('[data-close-feedback]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => {selected = null;});
  return {refresh, reset() {revision++;selected=null;if (dialog.open) dialog.close();}};
}
