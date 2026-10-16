'use strict';
(() => {
  const $ = id => document.getElementById(id);
  let ownerKey = '';
  let generation = 0;
  const forecastTrace = createNFLTraceUI({ document, request, epoch: () => generation, unlocked: () => Boolean(ownerKey) });
  $('mcp-url').textContent = `${location.origin}/mcp`;
  function notice(message, error = false) {
    $('notice').textContent = message;
    $('notice').classList.toggle('error', error);
  }
  function lock() {
    ownerKey = ''; generation++;
    $('unlock').disabled = false;
    $('access-form').hidden = false; $('connected').hidden = true;
    $('access-form').reset(); $('record-form').reset(); $('upload-form').reset();
    document.querySelectorAll('[data-private]').forEach(el => { el.disabled = true; });
    for (const id of ['prediction-count', 'outcome-count', 'model-count']) $(id).textContent = '—';
    $('records').textContent = 'Workspace locked. No private records are displayed.';
    $('uploads').textContent = 'Unlock to view private uploads.';
    forecastTrace.reset();
    notice('Workspace locked. Your access key and displayed data have been cleared.');
  }
  async function request(path, options = {}, binary = false) {
    const requestEpoch = generation;
    const response = await fetch(path, {
      ...options, credentials: 'omit', cache: 'no-store', signal: AbortSignal.timeout(45000),
      headers: { ...options.headers, Authorization: `Bearer ${ownerKey}` }
    });
    if (!response.ok) {
      let detail = 'Request failed. Please retry.';
      try { detail = (await response.json()).detail || detail; } catch (_) { /* no upstream HTML */ }
      if (response.status === 401 && requestEpoch === generation) lock();
      throw new Error(typeof detail === 'string' ? detail : 'Invalid request.');
    }
    return binary ? response.blob() : response.json();
  }
  function element(tag, text, className) {
    const el = document.createElement(tag); el.textContent = text;
    if (className) el.className = className;
    return el;
  }
  function renderRecords(records) {
    $('records').replaceChildren();
    if (!records.length) { $('records').textContent = 'No predictions recorded yet. Add a structured record below to start a traceable history.'; return; }
    for (const record of records) {
      const details = document.createElement('details'); details.className = 'record';
      const summary = document.createElement('summary');
      summary.append(element('strong', record.task), element('span', record.id, 'record-meta'), element('span', 'Unverified import', 'pill'));
      details.append(summary, element('pre', JSON.stringify(record, null, 2)));
      $('records').append(details);
    }
  }
  function renderUploads(assets) {
    $('uploads').replaceChildren();
    if (!assets.length) { $('uploads').textContent = 'No screenshots stored yet.'; return; }
    for (const asset of assets) {
      const row = element('div', '', 'upload-item');
      row.append(element('span', `${asset.id.slice(0, 12)}… · ${asset.status}`));
      const download = element('button', 'Download', 'secondary'); download.type = 'button';
      download.addEventListener('click', async () => {
        const epoch = generation;
        try {
          const blob = await request(`/api/uploads/${encodeURIComponent(asset.id)}`, {}, true);
          if (epoch !== generation) return;
          const url = URL.createObjectURL(blob); const a = document.createElement('a');
          a.href = url; a.download = asset.id; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
        } catch (error) { if (epoch === generation) notice(error.message, true); }
      });
      row.append(download); $('uploads').append(row);
    }
  }
  async function refresh() {
    const epoch = generation;
    const [statusResult, records, uploads] = await Promise.allSettled([
      request('/api/status'), request('/api/predictions'), request('/api/uploads')
    ]);
    if (epoch !== generation) return false;
    if (statusResult.status !== 'fulfilled') throw statusResult.reason;
    const status = statusResult.value;
    $('prediction-count').textContent = status.store.predictions;
    $('outcome-count').textContent = status.store.outcomes;
    $('model-count').textContent = status.store.models;
    if (records.status === 'fulfilled') renderRecords(records.value.records);
    else $('records').textContent = 'Prediction records unavailable. Refresh to retry; no records are inferred.';
    if (uploads.status === 'fulfilled') renderUploads(uploads.value.assets);
    else $('uploads').textContent = 'Private uploads unavailable. Verify the private bucket configuration before retrying.';
    return true;
  }
  $('access-form').addEventListener('submit', async event => {
    event.preventDefault(); ownerKey = $('access-key').value.trim(); generation++;
    const epoch = generation;
    $('access-key').value = ''; $('unlock').disabled = true; notice('Checking private workspace…');
    try {
      if (!await refresh() || epoch !== generation) return;
      $('access-form').hidden = true; $('connected').hidden = false;
      document.querySelectorAll('[data-private]').forEach(el => { el.disabled = false; });
      forecastTrace.load();
      notice('Connected to the shared store. Automatic learning and model promotion are off.');
    } catch (error) { if (epoch === generation) { lock(); notice(error.message, true); } }
    finally { if (epoch === generation) $('unlock').disabled = false; }
  });
  $('lock').addEventListener('click', lock);
  $('refresh').addEventListener('click', async () => {
    const epoch = generation;
    try { if (await refresh() && epoch === generation) { forecastTrace.load(); notice('Workspace refreshed.'); } }
    catch (error) { if (epoch === generation) notice(error.message, true); }
  });
  $('upload-form').addEventListener('submit', async event => {
    event.preventDefault(); const file = $('upload-file').files[0]; if (!file) return;
    if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type) || file.size > 3 * 1024 * 1024) {
      notice('Choose a PNG, JPEG, or WebP image no larger than 3 MB.', true); return;
    }
    const button = event.submitter; button.disabled = true; const epoch = generation;
    try {
      const result = await request('/api/uploads', { method: 'POST', headers: { 'Content-Type': file.type }, body: file });
      if (epoch !== generation) return;
      $('upload-form').reset();
      if (await refresh() && epoch === generation) notice(result.status + '. No AI analysis was performed.');
    } catch (error) { if (epoch === generation) notice(error.message, true); }
    finally { if (epoch === generation) button.disabled = !ownerKey; }
  });
  $('record-form').addEventListener('submit', async event => {
    event.preventDefault(); const button = event.submitter; button.disabled = true; const epoch = generation;
    try {
      const value = JSON.parse($('record-json').value);
      await request(`/api/${$('record-type').value}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(value) });
      if (epoch !== generation) return;
      if (await refresh() && epoch === generation) notice('Record saved (or identical replay confirmed). Imported data remains unverified.');
    } catch (error) { if (epoch === generation) notice(error instanceof SyntaxError ? 'Enter valid JSON before saving.' : error.message, true); }
    finally { if (epoch === generation) button.disabled = !ownerKey; }
  });
  $('example').addEventListener('click', () => {
    const ts = Math.floor(Date.now() / 1000) - 86400;
    const example = $('record-type').value === 'predictions' ? {
      id: 'example-' + crypto.randomUUID(), ts, model_version: 'manual-example-v1', task: 'example.binary',
      features: { source: 'manual example, not a live prediction' }, prediction: { probability: 0.6 },
      horizon_s: 3600, meta: { purpose: 'example_only' }
    } : { prediction_id: 'replace-with-a-recorded-prediction-id', resolved_ts: ts + 3600,
      realized: { hit: true }, meta: { source: 'replace-with-verifiable-outcome-source' } };
    $('record-json').value = JSON.stringify(example, null, 2);
    notice('Example loaded, not saved. Replace sample fields before submitting.');
  });
  window.addEventListener('pagehide', lock);
})();
