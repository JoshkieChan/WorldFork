'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const state = { bootstrap: null, run: null, policy: null, node: null, busy: false, dirty: false, token: '', view: 'experiment' };
const esc = (value) => String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
const num = (value, digits = 1) => Number.isFinite(Number(value)) ? Number(value).toLocaleString('en-US', {minimumFractionDigits: digits, maximumFractionDigits: digits}) : '—';
const signed = (value, digits = 1) => { const rounded=Number(Number(value).toFixed(digits)); return `${rounded > 0 ? '+' : ''}${num(Object.is(rounded,-0)?0:rounded,digits)}`; };
const pct = value => `${num(value)}%`;
const selectedPolicy = () => state.run?.policies.find(policy => policy.id === state.policy) || state.run?.policies[0];
const recommendedPolicy = () => state.run?.policies.find(policy => policy.id === state.run.recommended_policy) || state.run?.policies[0];
const baselinePolicy = () => state.run?.policies.find(policy => policy.id === 'baseline');
const nice = value => String(value).replaceAll('_', ' ').replace(/^./, c => c.toUpperCase());

async function api(path, options = {}) {
  const headers = {Accept: 'application/json', ...options.headers};
  if (options.body) { headers['Content-Type'] = 'application/json'; headers['X-WorldFork-Token'] = state.token; }
  let response;
  try { response = await fetch(path, {...options, headers, signal: AbortSignal.timeout(90000)}); }
  catch (error) { throw new Error(error.name === 'TimeoutError' ? 'The request timed out. Refresh the archive before retrying; the server may have saved the run.' : 'The local server could not be reached. Start WorldFork and refresh this page.'); }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(typeof data.error === 'string' ? data.error : `The request failed (${response.status}).`);
  return data;
}

function notify(message, error = false) { const node = $('#notice'); node.textContent = message; node.className = `notice${error ? ' error' : ''}`; node.hidden = false; }
function clearNotice() { $('#notice').hidden = true; }

function setView(view) {
  state.view = view;
  $$('.view').forEach(node => { node.hidden = node.id !== `view-${view}`; node.classList.toggle('active', !node.hidden); });
  $$('.rail-link').forEach(node => { const active = node.dataset.view === view; node.classList.toggle('active', active); if (active) node.setAttribute('aria-current', 'page'); else node.removeAttribute('aria-current'); });
  const headings = {
    experiment: ['A decision. Many possible futures.', 'Change the conditions. Challenge a plan. Follow the evidence.'],
    evidence: ['Every conclusion leaves a trail.', 'Inspect the calculations, constraints, and tradeoffs behind this run.'],
    model: ['Intelligence you can inspect.', 'A learned forecast, a bounded planner, and explicit assumptions.'],
    archive: ['Keep the experiment. Keep the evidence.', 'Revisit saved inputs, compare strategies, and reproduce a decision.']
  };
  $('#page-title').textContent = headings[view][0]; $('#page-subtitle').textContent = headings[view][1];
  if (view === 'archive') loadArchive();
  if (view === 'experiment') requestAnimationFrame(() => { renderTimeline(); renderNetwork(); });
}

function readScenario() {
  return {name: $('#run-name').value.trim() || 'Custom experiment', demand_multiplier: Number($('#demand').value), disruption: Number($('#disruption').value), reserve_units: Number($('#reserve').value), objective: $('#objective').value, seed: Number($('#seed').value), worlds: Number($('#worlds').value)};
}

function fillScenario(scenario) {
  $('#run-name').value = scenario.name;
  $('#demand').value = scenario.demand_multiplier;
  $('#disruption').value = scenario.disruption;
  $('#reserve').value = scenario.reserve_units;
  $('#objective').value = scenario.objective;
  $('#seed').value = scenario.seed;
  if (![...$('#worlds').options].some(option => Number(option.value) === scenario.worlds)) {
    const option = document.createElement('option'); option.value = scenario.worlds; option.textContent = scenario.worlds; $('#worlds').append(option);
  }
  $('#worlds').value = scenario.worlds;
  updateControlLabels();
}

function updateControlLabels() {
  $('#demand-value').textContent = `${Number($('#demand').value).toLocaleString('en-US', {maximumFractionDigits: 4})}×`;
  $('#disruption-value').textContent = `${(Number($('#disruption').value) * 100).toLocaleString('en-US', {maximumFractionDigits: 3})}%`;
  $('#reserve-value').textContent = `${Number($('#reserve').value).toLocaleString('en-US', {maximumFractionDigits: 4})} units/h`;
}

function markDirty() { updateControlLabels(); state.dirty = true; if (state.run) { $('#run-status').textContent = 'INPUTS CHANGED · RERUN TO APPLY'; $('#run-status').className = 'run-status pending'; $('#run-help').textContent = 'Results below still describe the previous run.'; } }

function metric(label, value, unit, note, symbol = '↗') { return `<article class="metric"><div class="metric-label">${esc(label)}<span aria-hidden="true">${symbol}</span></div><div class="metric-value">${esc(value)}<small>${esc(unit)}</small></div><div class="metric-note">${note}</div></article>`; }

function renderMetrics() {
  const policy = selectedPolicy(), baseline = baselinePolicy();
  if (!policy) { $('#metrics').innerHTML = [metric('Delivery coverage','—','%','Awaiting experiment'),metric('P10 coverage','—','%','Lower-tail outcome'),metric('Dispatch cost','—','cr','Synthetic cost units'),metric('District equity gap','—','pp','Lower is more even coverage')].join(''); return; }
  const delta = policy.service_rate - baseline.service_rate;
  $('#metrics').innerHTML = [
    metric('Delivery coverage',num(policy.service_rate),'%',`<span class="metric-delta ${delta < 0 ? 'negative' : ''}">${signed(delta,2)} pp</span> vs. baseline`),
    metric('P10 coverage',num(policy.p10_service),'%','10th percentile across futures','↘'),
    metric('Dispatch cost',num(policy.cost,0),'cr','Synthetic transport + reserve cost','↔'),
    metric('District equity gap',num(policy.equity_gap),'pp',`<span class="metric-delta ${policy.equity_gap > baseline.equity_gap ? 'negative' : ''}">${signed(policy.equity_gap-baseline.equity_gap)} pp</span> vs. baseline`,'⤴')
  ].join('');
}

function renderNetwork() {
  const network = state.run?.network || state.bootstrap?.network;
  if (!network) return;
  const policy = selectedPolicy();
  const scenario = state.run?.scenario || readScenario();
  const nodes = network.nodes;
  const width = $('#network').clientWidth || 650;
  const fontScale = Math.max(1, 600 / width);
  const actual = new Map((policy?.allocations || []).map(item => [`${item.source}-${item.target}`, item.units]));
  const maxFlow = Math.max(1, ...actual.values());
  const districtMetrics = new Map((policy?.district_service || []).map(item => [item.id, item]));
  const grid = `<defs><pattern id="map-grid" width="50" height="50" patternUnits="userSpaceOnUse"><path d="M50 0H0V50" fill="none" stroke="#354638" stroke-width=".7" opacity=".42"/></pattern><radialGradient id="map-glow"><stop offset="0" stop-color="#354a2c" stop-opacity=".45"/><stop offset="1" stop-color="#1c2a23" stop-opacity="0"/></radialGradient></defs><rect width="1000" height="600" fill="url(#map-grid)"/><ellipse cx="520" cy="285" rx="490" ry="290" fill="url(#map-glow)"/>`;
  const land = `<g fill="none" stroke="#3d503a" stroke-width="1" opacity=".45"><path d="M60 240 150 115 320 85 435 115 615 65 795 90 930 200 900 355 780 480 610 510 440 475 250 505 120 395Z"/><path d="M80 245 165 130 320 100 435 133 615 83 785 107 913 205 882 350 773 462 610 490 440 458 261 486 138 388Z"/><path d="M305 100 330 212 270 325 315 475M625 90 600 210 659 325 615 487M125 350 305 310 452 340 650 295 890 330" stroke-dasharray="4 7"/></g><g fill="#526948" opacity=".3"><rect x="410" y="185" width="70" height="40" rx="4"/><rect x="488" y="190" width="35" height="35" rx="3"/><rect x="410" y="232" width="44" height="45" rx="4"/><rect x="465" y="232" width="58" height="45" rx="4"/><rect x="734" y="255" width="75" height="45" rx="4"/><rect x="743" y="307" width="50" height="35" rx="4"/><rect x="350" y="390" width="54" height="35" rx="4"/></g><g font-family="Consolas,monospace" font-size="11" fill="#506647" letter-spacing="3"><text x="95" y="70">WEST SECTOR</text><text x="430" y="65">CENTRAL</text><text x="795" y="70">EAST SECTOR</text></g>`;
  const edges = network.edges.map(edge => {
    const source = nodes.find(node => node.id === edge.source), target = nodes.find(node => node.id === edge.target);
    if (!source || !target) return '';
    const units = actual.get(`${edge.source}-${edge.target}`) || 0;
    const active = policy && units > .01;
    const selected = state.node === source.id || state.node === target.id;
    const strokeWidth = active ? 1.2 + 4 * units / maxFlow : 1;
    return `<path d="M${source.x} ${source.y} Q${(source.x + target.x)/2} ${(source.y + target.y)/2 - 17} ${target.x} ${target.y}" fill="none" stroke="${selected ? '#d1f092' : active ? '#87ad57' : '#536b45'}" stroke-width="${strokeWidth}" opacity="${selected ? .95 : active ? .45 + .3*units/maxFlow : .24}" ${active ? '' : 'stroke-dasharray="4 6"'}><title>${esc(source.name)} → ${esc(target.name)}: ${policy ? `${num(units,0)} allocated units / 12h` : 'potential route'}</title></path>`;
  }).join('');
  const nodeMarkup = nodes.map(node => {
    const district = districtMetrics.get(node.id);
    const isHub = node.type === 'hub';
    const west = isHub && /west/i.test(node.id + node.name);
    const stressed = west && scenario.disruption > 0;
    const color = stressed ? '#d0aa6e' : isHub ? '#c4ed8e' : district && district.service_rate < 65 ? '#c2a473' : '#a8c580';
    const label = isHub ? stressed ? `${num(scenario.disruption*100,0)}% capacity lost` : `${num(node.capacity,0)} units/h` : district ? `${num(district.service_rate,0)}% served` : 'demand district';
    const radius = isHub ? 16 : 12;
    const shape = isHub ? `<rect class="node-outline" x="-14" y="-14" width="28" height="28" rx="5" fill="#283e29" stroke="${color}" stroke-width="1.8" transform="rotate(45)"/><path d="M-5 4V-4H5V4Z" stroke="${color}" fill="none" stroke-width="1.4"/>` : `<circle class="node-outline" r="${radius}" fill="#273c2a" stroke="${color}" stroke-width="1.8"/><circle r="4" fill="${color}"/>`;
    return `<g class="network-node ${state.node === node.id ? 'selected' : ''}" transform="translate(${node.x},${node.y})" role="button" tabindex="0" data-node="${esc(node.id)}" aria-label="${esc(node.name)}, ${esc(label)}"><circle r="${radius+11}" fill="${color}" opacity=".05"/>${shape}<text class="node-name" x="0" y="${36*fontScale}" text-anchor="middle" style="font-size:${16*fontScale}px">${esc(node.name)}</text><text class="node-meta" x="0" y="${53*fontScale}" text-anchor="middle" style="font-size:${11*fontScale}px">${esc(label)}</text></g>`;
  }).join('');
  $('#network').innerHTML = `<title>Synthetic supply network for ${esc(policy?.name || 'initial conditions')}</title><desc>Three hubs send resources to six districts. Route width represents mean allocations over the full twelve-hour horizon. Select a node for details.</desc>${grid}${land}${edges}${nodeMarkup}`;
  $('#network-caption').textContent = policy ? `${policy.name.toUpperCase()} / MEAN FLOW ACROSS ${state.run.search.evaluation_worlds} FUTURES` : 'SCHEMATIC NETWORK / NOT A GEOGRAPHIC MAP';
  if (state.node) {
    const node = nodes.find(item => item.id === state.node), district = districtMetrics.get(state.node);
    $('#node-detail').textContent = district ? `${node.name}: ${num(district.delivered,0)} / ${num(district.demand,0)} units delivered` : node ? `${node.name}: ${num(node.capacity,0)} units/h nominal capacity` : 'Select a node to inspect';
  } else $('#node-detail').textContent = 'Select a node to inspect';
}

function renderRecommendation() {
  const run = state.run, policy = recommendedPolicy();
  if (!run || !policy) return;
  const checksPass = run.checks.every(check => check.passed);
  $('#recommendation').innerHTML = `<h4 class="recommendation-name">${esc(policy.name)}</h4><p class="recommendation-copy">${esc(run.summary)}</p><div class="pipeline"><div class="pipeline-step"><span class="step-check">✓</span><span>Learn demand</span><span class="step-detail">${num(run.model.train_samples,0)} samples</span></div><div class="pipeline-step"><span class="step-check">✓</span><span>Search alternatives</span><span class="step-detail">${num(run.search.candidates,0)} candidates</span></div><div class="pipeline-step"><span class="step-check">✓</span><span>Test on new futures</span><span class="step-detail">${num(run.search.evaluation_worlds,0)} worlds</span></div><div class="pipeline-step"><span class="step-check">${checksPass ? '✓' : '!'}</span><span>Verify constraints</span><span class="step-detail">${run.checks.filter(check => check.passed).length}/${run.checks.length} pass</span></div></div><button class="evidence-link" data-view="evidence">Inspect the full evidence <span>→</span></button>`;
}

function renderTimeline() {
  const policy = selectedPolicy(), baseline = baselinePolicy();
  if (!policy) { $('#timeline').innerHTML = '<p class="empty-note">The first experiment will populate this chart.</p>'; return; }
  const points = policy.timeline;
  const width = Math.max(265, $('#timeline').clientWidth - 32), height = 176;
  const margin = {left:34, right:12, top:15, bottom:29};
  const x = index => margin.left + index / Math.max(1, points.length - 1) * (width-margin.left-margin.right);
  const y = value => height-margin.bottom-Math.max(0,Math.min(100,value))/100*(height-margin.top-margin.bottom);
  const path = (values, key) => values.map((point,index) => `${index ? 'L' : 'M'}${x(index).toFixed(2)} ${y(point[key]).toFixed(2)}`).join(' ');
  const area = `${path(points,'high')} ${[...points].reverse().map((point,index) => `L${x(points.length-1-index).toFixed(2)} ${y(point.low).toFixed(2)}`).join(' ')} Z`;
  const yTicks = [0,25,50,75,100].map(value => `<g><line x1="${margin.left}" y1="${y(value)}" x2="${width-margin.right}" y2="${y(value)}" stroke="#e7ece1" stroke-dasharray="3 4"/><text x="${margin.left-8}" y="${y(value)+3}" text-anchor="end" fill="#95a187" font-size="9">${value}${value===100 ? '%' : ''}</text></g>`).join('');
  const xTicks = points.map((point,index) => index%3===0 || index===points.length-1 ? `<text x="${x(index)}" y="${height-8}" text-anchor="middle" fill="#95a187" font-size="9">${String(point.hour).padStart(2,'0')}:00</text>` : '').join('');
  const dots = points.map((point,index) => `<circle cx="${x(index)}" cy="${y(point.service_rate)}" r="3" fill="#718e54" stroke="#fcfdf9" stroke-width="1.5"><title>${point.hour}:00 — ${pct(point.service_rate)} delivered; P10 ${pct(point.low)}, P90 ${pct(point.high)}</title></circle>`).join('');
  $('#timeline').innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Hourly delivery coverage for ${esc(policy.name)} compared with baseline; shaded band is the 10th to 90th percentile across simulation worlds"><title>Hourly delivery coverage</title>${yTicks}<path d="${area}" fill="#dce8cb" opacity=".65"/><path d="${path(baseline.timeline,'service_rate')}" fill="none" stroke="#aab69b" stroke-width="1.5" stroke-dasharray="4 4"/><path d="${path(points,'service_rate')}" fill="none" stroke="#718e54" stroke-width="2"/>${dots}${xTicks}</svg>`;
}

function renderSensitivity() {
  if (!state.run) return;
  $('#sensitivity').innerHTML = state.run.sensitivity.map(item => `<div class="sensitivity-row"><span>${esc(item.label)}</span><span class="sensitivity-change">${esc(item.change)} · ${pct(item.service_rate)} coverage</span><strong class="sensitivity-delta ${item.delta >= 0 ? 'positive' : ''}">${signed(item.delta)} pp</strong></div>`).join('');
}

function renderPolicies() {
  if (!state.run) return;
  $('#policy-select').innerHTML = state.run.policies.map(policy => `<option value="${esc(policy.id)}" ${policy.id === state.policy ? 'selected' : ''}>${esc(policy.name)}</option>`).join('');
  $('#policy-select').disabled = false;
  $('#policy-table').innerHTML = state.run.policies.map(policy => `<tr class="${policy.id === state.policy ? 'selected' : ''}"><td><button class="policy-name" data-policy="${esc(policy.id)}" aria-pressed="${policy.id === state.policy}"><span class="policy-mark"></span><span>${esc(policy.name)}${policy.id === state.run.recommended_policy ? '<span class="winner-label">SEARCH PICK</span>' : ''}</span></button></td><td>${pct(policy.service_rate)}</td><td>${pct(policy.p10_service)}</td><td>${num(policy.cost,0)} cr</td><td>${num(policy.equity_gap)} pp</td><td class="score-cell">${num(policy.score)}</td></tr>`).join('');
  $('#comparison-subtitle').textContent = `${state.run.search.evaluation_worlds} shared evaluation futures · score reflects the ${state.run.scenario.objective} objective, not a probability.`;
}

function renderEvidence() {
  const run = state.run;
  if (!run) { $('#evidence-content').innerHTML = '<div class="empty-state"><h2>No evidence without an experiment.</h2><p>Run a scenario to inspect its computations and constraint checks.</p><button class="button button-quiet" data-view="experiment">Go to the lab →</button></div>'; return; }
  const stages = run.evidence.map(item => `<article class="evidence-stage"><div class="evidence-stage-heading"><span class="status-badge ${esc(item.status)}">${esc(item.role)}</span><h3>${esc(item.title)}</h3></div><p>${esc(item.detail)}</p><div class="evidence-metrics">${Object.entries(item.metrics || {}).map(([key,value]) => `<span>${esc(nice(key))}: <strong>${esc(typeof value === 'number' ? num(value,Number.isInteger(value)?0:2) : typeof value === 'object' ? JSON.stringify(value) : value)}</strong></span>`).join('')}</div></article>`).join('');
  const checks = run.checks.map(check => `<div class="check-row"><span class="check-symbol ${check.passed?'':'failed'}">${check.passed?'✓':'!'}</span><div>${esc(check.name)}<small>${esc(check.detail)}</small></div></div>`).join('');
  $('#evidence-content').innerHTML = `<div class="evidence-header"><div><h2>${esc(run.scenario.name)}</h2><p>${num(run.search.candidates,0)} candidates · ${num(run.search.training_worlds,0)} search worlds · ${num(run.search.evaluation_worlds,0)} evaluation worlds · seed ${run.scenario.seed}</p></div><div class="evidence-id">RUN ${esc(run.id)}<br>${esc(new Date(run.created_at).toLocaleString())}</div></div><div class="evidence-layout"><section class="panel"><div class="card-heading"><div><h3>The computation trail</h3><p>Algorithmic stages with recorded results</p></div><span class="tag">REPRODUCIBLE</span></div>${stages}</section><div><section class="panel"><div class="card-heading"><div><h3>Constraint checks</h3><p>${run.checks.filter(check=>check.passed).length} of ${run.checks.length} passed</p></div></div><div class="checks-list">${checks}</div></section><section class="panel spaced-panel"><div class="card-heading"><div><h3>What this experiment cannot tell you</h3></div></div><ul class="limitations-list">${run.limitations.map(item=>`<li>${esc(item)}</li>`).join('')}</ul></section><section class="panel spaced-panel"><div class="card-heading"><div><h3>Search method</h3></div></div><div class="checks-list"><p class="method-copy">${esc(run.search.method)}</p><p class="field-note">Measured compute time: ${num(run.search.elapsed_ms/1000,2)} seconds. The recommendation is selected before evaluation, so it can lose to a baseline on new worlds.</p></div></section></div></div>`;
}

function renderModel() {
  const model = state.run?.model || state.bootstrap?.model;
  if (!model) return;
  const maxError = Math.max(model.mae,model.baseline_mae,1);
  const detail = (label,value) => `<div><dt>${esc(label)}</dt><dd>${esc(value)}</dd></div>`;
  $('#model-content').innerHTML = `<section class="model-intro"><div><span class="eyebrow">THE LEARNING LAYER</span><h2>A forecast trained here.<br>A result you can reproduce.</h2><p>WorldFork learns demand patterns from a generated history. It compares the forecast with a simple baseline on a later time period. A good fit to synthetic data is evidence about this model, not a guarantee about the world.</p></div><div class="model-formula">history → forecast → allocation<small>LEARNED PREDICTION + CONSTRAINED OPTIMIZATION</small></div></section><div class="model-grid"><section class="panel"><div class="card-heading"><div><h3>Forecast evaluation</h3><p>MAE: resource units per district per hour · lower is better</p></div></div><div class="panel-body"><dl class="definition-list">${detail('Model',model.name)}${detail('Training examples',num(model.train_samples,0))}${detail('Held-out examples',num(model.test_samples,0))}${detail('R² on held-out data',num(model.r2,3))}</dl><div class="model-comparison"><div class="model-bar-row"><div class="model-bar-label"><span>Learned forecast MAE</span><strong>${num(model.mae,2)}</strong></div><div class="model-bar-track"><div class="model-bar-fill" style="width:${model.mae/maxError*100}%"></div></div></div><div class="model-bar-row"><div class="model-bar-label"><span>Previous-day baseline MAE</span><strong>${num(model.baseline_mae,2)}</strong></div><div class="model-bar-track"><div class="model-bar-fill baseline" style="width:${model.baseline_mae/maxError*100}%"></div></div></div></div></div></section><section class="panel"><div class="card-heading"><div><h3>Data and features</h3><p>Generated observations, with a chronological split</p></div></div><div class="panel-body"><p class="method-copy">${esc(model.data_source)}</p><p class="method-copy" style="margin-top:12px">${esc(model.split)}</p><div class="feature-tags">${model.features.map(feature=>`<span>${esc(feature)}</span>`).join('')}</div></div></section><section class="panel"><div class="card-heading"><div><h3>Where the intelligence lives</h3></div></div><div class="panel-body"><dl class="definition-list">${detail('Forecast','Ridge regression learned from data')}${detail('Planning','Bounded policy search')}${detail('Evaluation','Monte Carlo on independent worlds')}${detail('Verification','Supply and flow constraints')}${detail('Language interface',state.bootstrap?.capabilities.language_model ? `${state.bootstrap.capabilities.model} · configured` : 'Optional · not configured')}</dl><p class="field-note">The computation stages are specialized algorithms. The optional language model translates scenario descriptions; it does not calculate the evidence or execute actions.</p></div></section><section class="panel"><div class="card-heading"><div><h3>Assumptions that matter</h3></div></div><ul class="limitations-list"><li>The network, demand history, route costs, and uncertainty are synthetic.</li><li>Resource flows are divisible quantities. This is not vehicle routing, a digital twin, or a dispatch service.</li><li>Policy search is bounded. The selected policy is a candidate, not a proof of global optimality.</li><li>Percentile bands show variation within the simulator, not calibrated real-world confidence.</li><li>Only explicit use of the optional language interface sends your prompt to OpenAI. Runs otherwise compute on this machine.</li></ul></section></div>`;
}

function displayRun(run, syncControls = true) {
  state.run = run; state.policy = run.recommended_policy; state.node = null; state.dirty = false;
  if (syncControls) { fillScenario(run.scenario); $('#preset-select').value = 'custom'; $('#preset-description').textContent = 'Controls reflect the saved experiment shown below.'; }
  $('#run-status').textContent = `${run.search.evaluation_worlds} FUTURES · SEED ${run.scenario.seed}`; $('#run-status').className = 'run-status complete';
  $('#run-help').textContent = 'Reproducible. Local. No API key required.';
  $('#export-button').disabled = false;
  renderMetrics(); renderNetwork(); renderRecommendation(); renderTimeline(); renderSensitivity(); renderPolicies(); renderEvidence(); renderModel();
}

async function runExperiment() {
  if (state.busy) return;
  const scenario = readScenario();
  if (!Number.isInteger(scenario.seed) || scenario.seed < 1 || scenario.seed > 2147483647) { notify('Use a whole-number seed from 1 to 2147483647.',true); $('#seed').focus(); return; }
  clearNotice(); state.busy = true;
  $('#run-status').textContent = state.run ? 'RUNNING · PREVIOUS RESULTS SHOWN' : 'RUNNING FIRST EXPERIMENT';
  $('#run-status').className = 'run-status pending';
  $('#run-button').disabled = true; $('#run-button-text').textContent = 'Exploring futures…'; $('#busy-overlay').hidden = false;
  $('#interpret-button').disabled = true;
  const controls = $$('.scenario-panel input, .scenario-panel select, .scenario-panel textarea'); controls.forEach(control=>control.disabled=true);
  const started = performance.now(); $('#busy-time').textContent = '0s';
  const timer = setInterval(()=>{$('#busy-time').textContent=`${((performance.now()-started)/1000).toFixed(1)}s`;},100);
  try { const run = await api('/api/runs',{method:'POST',body:JSON.stringify(scenario)}); displayRun(run,false); }
  catch (error) { notify(error.message,true); $('#run-status').textContent=state.run?'RUN FAILED · PREVIOUS RESULTS SHOWN':'EXPERIMENT FAILED'; }
  finally { clearInterval(timer); state.busy=false; $('#busy-overlay').hidden=true; $('#run-button').disabled=false; $('#run-button-text').textContent='Explore futures'; controls.forEach(control=>control.disabled=false); $('#interpret-button').disabled=!state.bootstrap.capabilities.language_model; }
}

async function loadArchive() {
  $('#archive-content').innerHTML='<p class="empty-note" role="status">Loading saved experiments…</p>';
  try {
    const data = await api('/api/runs');
    if (!data.runs.length) { $('#archive-content').innerHTML='<div class="empty-state"><h2>A clean slate.</h2><p>Your experiments will be saved here with their inputs and evidence.</p><button class="button button-quiet" data-view="experiment">Start an experiment →</button></div>'; return data.runs; }
    $('#archive-content').innerHTML=`<div class="archive-grid">${data.runs.map(run=>`<article class="archive-card"><div class="archive-date">${esc(new Date(run.created_at).toLocaleString())}</div><h3>${esc(run.name)}</h3><p>${esc(nice(run.recommended_policy))} · saved experiment</p><div class="archive-stat">${pct(run.service_rate)}<small>coverage</small></div><button class="button button-quiet" data-load-run="${esc(run.id)}">Open experiment <span>→</span></button></article>`).join('')}</div>`;
    return data.runs;
  } catch(error) { notify(error.message,true); return []; }
}

async function interpretScenario() {
  if (state.busy) return;
  const prompt = $('#scenario-prompt').value.trim();
  if (!prompt) { notify('Describe the supply-network scenario you want to explore.'); $('#scenario-prompt').focus(); return; }
  clearNotice(); state.busy=true; $('#interpret-button').disabled=true; $('#interpret-button').textContent='Interpreting…'; $('#run-button').disabled=true;
  const controls=$$('.scenario-panel input, .scenario-panel select, .scenario-panel textarea'); controls.forEach(control=>control.disabled=true);
  try { const data=await api('/api/interpret',{method:'POST',body:JSON.stringify({prompt})}); fillScenario(data.scenario); $('#preset-select').value='custom'; $('#preset-description').textContent='Translated from your description. Review the controls before running.'; markDirty(); notify(`${data.interpretation} Review the translated controls, then explore futures.`); }
  catch(error) { notify(error.message,true); }
  finally { state.busy=false; controls.forEach(control=>control.disabled=false); $('#run-button').disabled=false; $('#interpret-button').disabled=!state.bootstrap?.capabilities.language_model; $('#interpret-button').textContent='Translate to controls ↗'; }
}

document.addEventListener('click',async event=>{
  const view=event.target.closest('[data-view]'); if(view) { setView(view.dataset.view); return; }
  const policy=event.target.closest('[data-policy]'); if(policy) { state.policy=policy.dataset.policy; renderMetrics(); renderNetwork(); renderTimeline(); renderPolicies(); return; }
  const node=event.target.closest('[data-node]'); if(node) { state.node=state.node===node.dataset.node?null:node.dataset.node; renderNetwork(); return; }
  const load=event.target.closest('[data-load-run]'); if(load) {
    if(state.busy) { notify('Wait for the current experiment to finish before opening another run.'); return; }
    load.disabled=true; clearNotice();
    try { const run=await api(`/api/runs/${encodeURIComponent(load.dataset.loadRun)}`); displayRun(run); setView('experiment'); window.scrollTo({top:0,behavior:'instant'}); }
    catch(error) { notify(error.message,true); } finally { load.disabled=false; }
  }
});
$('#network').addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){const node=event.target.closest('[data-node]');if(node){event.preventDefault();state.node=state.node===node.dataset.node?null:node.dataset.node;renderNetwork();$(`[data-node="${CSS.escape(node.dataset.node)}"]`)?.focus();}}});
$('#preset-select').addEventListener('change',()=>{const preset=state.bootstrap.presets.find(item=>item.id===$('#preset-select').value);if(preset){fillScenario({...state.bootstrap.defaults,...preset.scenario});$('#preset-description').textContent=preset.description;markDirty();}});
$$('.scenario-panel input, .scenario-panel select').filter(node=>node.id!=='preset-select').forEach(node=>node.addEventListener('input',()=>{markDirty();$('#preset-select').value='custom';$('#preset-description').textContent='Custom conditions. Run the experiment to evaluate them.';}));
$('#run-button').addEventListener('click',runExperiment);
$('#interpret-button').addEventListener('click',interpretScenario);
$('#policy-select').addEventListener('change',()=>{state.policy=$('#policy-select').value;renderMetrics();renderNetwork();renderTimeline();renderPolicies();});
$('#refresh-archive').addEventListener('click',loadArchive);
$('#export-button').addEventListener('click',()=>{if(state.run){const link=document.createElement('a');link.href=`/api/runs/${encodeURIComponent(state.run.id)}/export`;link.download=`worldfork-${state.run.id}.json`;document.body.append(link);link.click();link.remove();}});
let resizeTimer;window.addEventListener('resize',()=>{clearTimeout(resizeTimer);resizeTimer=setTimeout(()=>{if(state.view==='experiment'){renderTimeline();renderNetwork();}},150);});

async function initialize() {
  renderMetrics();renderEvidence();
  try {
    const bootstrap=await api('/api/bootstrap');state.bootstrap=bootstrap;state.token=bootstrap.csrf_token;
    $('#preset-select').innerHTML=bootstrap.presets.map(preset=>`<option value="${esc(preset.id)}">${esc(preset.name)}</option>`).join('')+'<option value="custom">Custom scenario</option>';
    fillScenario(bootstrap.defaults);
    const first=bootstrap.presets.find(preset=>preset.scenario.name===bootstrap.defaults.name)||bootstrap.presets[0];
    $('#preset-select').value=first?.id||'custom';$('#preset-description').textContent=first?.description||'Adjust the inputs to create a scenario.';
    $('#run-button-text').textContent='Loading history…';
    $('#model-status').textContent=bootstrap.capabilities.language_model?'CONNECTED':'OPTIONAL';
    $('#language-help').textContent=bootstrap.capabilities.language_model?`Uses ${bootstrap.capabilities.model}. Your description is sent to OpenAI only when you select Translate.`:'Set OPENAI_API_KEY on the server to enable language input. Forecasting and simulation already work locally.';
    renderNetwork();renderModel();
    const archive=await loadArchive();
    if(archive.length){displayRun(await api(`/api/runs/${encodeURIComponent(archive[0].id)}`));}
    else await runExperiment();
    $('#run-button').disabled=false;$('#run-button-text').textContent='Explore futures';
    $('#interpret-button').disabled=!bootstrap.capabilities.language_model;
  }catch(error){notify(error.message,true);$('#run-button-text').textContent='Server unavailable';$('#run-status').textContent='CONNECTION ERROR';}
}
initialize();
