'use strict';
// Owner-key handling stays in app.js; this read-only component receives a request function.
globalThis.createNFLTraceUI = function ({ document, request, epoch, unlocked }) {
  const $ = id => document.getElementById(id);
  let searchRun = 0, detailRun = 0;
  function text(tag, value, className) {
    const node = document.createElement(tag); node.textContent = value;
    if (className) node.className = className;
    return node;
  }
  function reset() {
    searchRun++; detailRun++;
    $('trace-form').reset();
    $('trace-state').textContent = 'Locked. Internal forecast evidence has not been loaded.';
    $('trace-list').replaceChildren();
    $('trace-detail').textContent = 'Select a forecast after unlocking.';
  }
  async function inspect(receiptID) {
    const run = ++detailRun, auth = epoch();
    $('trace-detail').textContent = 'Loading the pinned receipt and correction history…';
    try {
      const result = await request('/api/v1/nfl/forecast-trace/' + encodeURIComponent(receiptID));
      if (run !== detailRun || auth !== epoch()) return;
      if (result?.version !== 1 || result?.kind !== 'nfl.forecast_trace.v1' || result?.read_only !== true) throw new Error('Unsupported trace response.');
      const pre = text('pre', JSON.stringify(result, null, 2));
      $('trace-detail').replaceChildren(
        text('h3', 'Internal forecast trace'),
        text('p', 'Public availability, source freshness and model adoption remain unverified. Pending means no final in this snapshot.'),
        pre);
    } catch (_) {
      if (run === detailRun && auth === epoch()) {
        $('trace-detail').textContent = 'Trace unavailable. Retry the search; no final outcome or active model is inferred.';
      }
    }
  }
  async function load() {
    const run = ++searchRun, auth = epoch();
    detailRun++;
    const button = $('trace-search');
    button.disabled = true;
    $('trace-list').replaceChildren();
    $('trace-detail').textContent = 'Select an internal forecast to inspect its evidence.';
    $('trace-state').textContent = 'Checking the internal forecast journal…';
    try {
      const query = $('trace-query').value.trim();
      const result = await request('/api/v1/nfl/forecasts?query=' + encodeURIComponent(query) + '&limit=25');
      if (run !== searchRun || auth !== epoch()) return;
      if (result?.version !== 1 || result?.kind !== 'nfl.forecast_catalog.v1' || result?.read_only !== true || !Array.isArray(result.records)) {
        throw new Error('Unsupported forecast response.');
      }
      $('trace-state').textContent = 'Internal journal: ' + result.total_forecasts + ' forecasts; ' +
        result.matched_forecasts + ' matching; watermark ' + result.watermark + '. Production serving and learning are not verified here.';
      if (!result.records.length) {
        $('trace-list').textContent = 'No matching internal forecast receipts in this snapshot.';
        return;
      }
      for (const record of result.records) {
        const row = text('div', '', 'record');
        row.append(text('strong', record.market + ' · ' + record.line + ' · ' + record.outcome_state));
        row.append(text('p', record.event_id, 'record-meta'));
        row.append(text('code', record.receipt_id));
        const open = text('button', 'Inspect evidence', 'secondary');
        open.type = 'button'; open.dataset.private = '';
        open.addEventListener('click', () => inspect(record.receipt_id));
        row.append(open); $('trace-list').append(row);
      }
    } catch (_) {
      if (run === searchRun && auth === epoch()) {
        $('trace-list').replaceChildren();
        $('trace-state').textContent = 'NFL journal unavailable or unsupported. Retry after backend configuration; no freshness or accuracy is inferred.';
      }
    } finally {
      if (run === searchRun && auth === epoch()) button.disabled = !unlocked();
    }
  }
  $('trace-form').addEventListener('submit', event => { event.preventDefault(); load(); });
  return Object.freeze({ load, reset });
};

