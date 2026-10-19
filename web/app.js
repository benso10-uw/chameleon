'use strict';
const $ = id => document.getElementById(id);
const state = {config: null, task: null, run: null, selected: null, poll: null, busy: false, version: 0};
const escapeHTML = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const niceName = s => String(s || '').replace('chameleon_sample_', '').replace(/_/g, ' ').replace(/\blevel(\d)/, 'Level $1 ·');
const seconds = s => s >= 60 ? `${Math.floor(Math.round(s) / 60)}m ${Math.round(s) % 60}s` : `${Number(s || 0).toFixed(1)}s`;
const count = n => Number(n || 0).toLocaleString();
function message(text, error = false) { $('message').textContent = text; $('message').classList.toggle('error', error); }
async function api(path, data) {
  const response = await fetch(path, data === undefined ? {} : {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(data)});
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `Request failed (${response.status})`);
  return body;
}
function clearRun() {
  clearTimeout(state.poll); state.run = null; state.selected = null;
  $('timeline').innerHTML = '<p class="empty">Your run will appear here, one step at a time.</p>';
  $('candidates').replaceChildren(); $('downloads').replaceChildren(); $('insight').hidden = true;
  $('result-image').hidden = true; $('result-empty').hidden = false; $('result-title').textContent = 'Target canvas';
  $('result-foot').textContent = ''; $('live-dot').hidden = true; $('state').textContent = 'Ready';
  $('state').classList.remove('bad'); updateMetrics();
}
function showWarnings(warnings = []) {
  $('warnings').replaceChildren(...warnings.map(w => { const p = document.createElement('p'); p.className = 'warning'; p.textContent = w; return p; }));
}
async function selectTask() {
  if (state.busy) return;
  const version = ++state.version; clearRun(); message('Loading source preview…');
  try {
    const item = await api(`/api/tasks/${encodeURIComponent($('task').value)}`);
    if (version !== state.version) return;
    state.task = item.task; $('title').textContent = niceName(item.name);
    $('width').value = item.task.target_canvas.width; $('height').value = item.task.target_canvas.height;
    showSource(item.task, item.source_image); showWarnings(item.warnings); updateTarget(); message('Ready to run.');
  } catch (e) { message(e.message, true); }
}
function showSource(task, src) {
  $('source-size').textContent = `${task.source.canvas.width} × ${task.source.canvas.height}`;
  $('source-image').hidden = !src; $('source-empty').hidden = !!src;
  if (src) $('source-image').src = src;
}
function updateTarget() { $('target-size').textContent = `${$('width').value} × ${$('height').value}`; }
function providerChanged() {
  const p = $('provider').value;
  $('model-settings').hidden = p === 'preset'; $('url-field').hidden = !['openai','ollama'].includes(p);
  $('key-field').hidden = p === 'ollama'; $('refresh-models').hidden = p !== 'gemini';
  $('reasoning').disabled = p === 'ollama';
  $('key-state').textContent = (p === 'gemini' ? state.config.gemini_key_saved : state.config.hosted_key_saved) ? '· saved locally' : '';
  $('api-key').value = '';
  if (p === 'gemini') { $('model').value = 'gemini-3.5-flash-lite'; $('reasoning').value = 'minimal'; }
  if (p === 'openai') { $('model').value = state.config.hosted_model || ''; $('base-url').value = state.config.hosted_url || ''; $('reasoning').value = /qwen/i.test($('model').value) ? 'off' : 'auto'; }
  if (p === 'ollama') { $('model').value = 'qwen2.5:7b'; $('base-url').value = 'http://127.0.0.1:11434'; }
  reasoningNote();
}
function reasoningNote() {
  $('reasoning-note').textContent = $('provider').value === 'gemini'
    ? 'Start with 3.5 Flash-Lite / minimal for speed. Minimal does not guarantee zero thinking. Older 2.5 models may be retired for your key.'
    : $('provider').value === 'ollama' ? 'Ollama uses its model defaults.'
    : 'Reasoning controls depend on your host. Qwen uses enable_thinking; other models use reasoning_effort. Choose provider default if rejected.';
}
async function history() {
  const rows = await api('/api/history');
  $('history').innerHTML = rows.length ? rows.map(r => `<button type="button" class="history-item" data-run="${escapeHTML(r.id)}"><strong>${escapeHTML(niceName(r.name))}</strong><span>${escapeHTML(r.model)} · ${escapeHTML(r.reasoning || 'saved')} ${r.seconds ? '· ' + seconds(r.seconds) : ''}</span></button>`).join('') : '<p class="hint">No saved runs yet.</p>';
}
function busy(value) {
  state.busy = value; $('run').disabled = value; $('task').disabled = value; $('upload').disabled = value;
  $('cancel').hidden = !value; $('live-dot').hidden = !value;
}
function settings() {
  return {task_id: $('task').value, provider: $('provider').value, model: $('model').value.trim(),
    api_key: $('api-key').value, base_url: $('base-url').value.trim(), reasoning: $('reasoning').value,
    target: {width:Number($('width').value),height:Number($('height').value)}, max_tokens:Number($('tokens').value),
    timeout:Number($('timeout').value), attempts:Number($('attempts').value), refine:$('refine').checked,
    model_understanding:$('understanding').checked, llm_tiebreak:$('tiebreak').checked};
}
async function startRun() {
  if (state.busy) return;
  clearRun(); busy(true); message('Starting run…');
  try {
    const {id} = await api('/api/runs', settings());
    state.run = {id, steps:[], images:{}, state:'running'}; state.selected = null;
    await pollRun(id);
  } catch (e) { busy(false); message(e.message, true); }
}
async function pollRun(id) {
  if (state.run?.id !== id) return;
  try {
    const after = state.run.steps.length;
    const data = await api(`/api/runs/${encodeURIComponent(id)}?after=${after}`);
    if (state.run?.id !== id) return;
    const steps = state.run.steps.concat(data.steps);
    state.run = {...data,steps};
    updateRun(after);
    if (data.state === 'running') state.poll = setTimeout(() => pollRun(id), 700);
    else { busy(false); await history(); }
  } catch (e) {
    message(`${e.message} Reconnecting to the run…`, true);
    state.poll = setTimeout(() => pollRun(id), 1800);
  }
}
async function openRun(id) {
  if (state.busy) { message('Stop the active run before opening another.'); return; }
  const version = ++state.version; clearRun(); message('Loading saved trace…');
  try {
    const data = await api(`/api/runs/${encodeURIComponent(id)}`);
    if (version !== state.version) return;
    state.run = data; state.task = data.task; $('title').textContent = niceName(data.name);
    $('width').value = data.task.target_canvas.width; $('height').value = data.task.target_canvas.height;
    updateTarget(); showSource(data.task,data.images.source); updateRun(0);
    // Make saved imported tasks selectable again without requiring another upload.
    const imported = await api('/api/import', {name:data.name,task:data.task});
    if (![...$('task').options].some(o=>o.value===imported.id)) $('task').add(new Option(niceName(data.name),imported.id));
    $('task').value = imported.id;
    if (data.state === 'running') { busy(true); pollRun(id); }
  } catch(e) { message(e.message,true); }
}
function details(label, value, raw = false) {
  if (!value) return '';
  const text = typeof value === 'string' ? value : JSON.stringify(value,null,2);
  return `<details${raw ? ' class="raw"' : ''}><summary>${escapeHTML(label)}</summary><pre>${escapeHTML(text)}</pre></details>`;
}
const label = t => String(t || '').replaceAll('_',' ');
function analysisHTML(a) {
  if (!a) return '';
  const order = Object.fromEntries((a.technical || []).map((t,i)=>[t.id,i]));
  const swatches = (a.palette || []).map(p=>`<li><span class="swatch" style="background:${/^#[0-9a-f]{3,8}$/i.test(p.hex || '') ? p.hex : 'transparent'}"></span><strong>${escapeHTML(p.hex)}</strong> ${escapeHTML(p.name || '')}<small>${escapeHTML(p.usage || '')}</small></li>`).join('');
  const measured = a.extracted_colours ? Object.entries(a.extracted_colours.image_assets || {}).flatMap(([,cs])=>cs).concat((a.extracted_colours.text_colours || []).concat(a.extracted_colours.shape_fills || []).map(hex=>({hex}))) : [];
  const chips = !swatches && measured.length ? `<p class="analysis-label">Measured colours</p><ul class="palette">${measured.slice(0,12).map(c=>`<li><span class="swatch" style="background:${escapeHTML(c.hex)}"></span>${escapeHTML(c.hex)}</li>`).join('')}</ul>` : '';
  const reasons = Object.entries(a.priority_reasons || {}).filter(([,w])=>w).sort(([x],[y])=>(order[x] ?? 99)-(order[y] ?? 99));
  const rel = (a.relationships || []).map(r=>`${[].concat(r.a).join(', ')} ${label(r.relation)} ${[].concat(r.b).join(', ')}${r.note ? ' · '+r.note : ''}`);
  return `<div class="analysis">${a.description ? `<p class="analysis-description">${escapeHTML(a.description)}</p>` : ''}`
    + (a.pattern ? `<p><span class="analysis-label">Pattern</span> ${escapeHTML(label(a.pattern))}${a.pattern_notes ? ' — '+escapeHTML(a.pattern_notes) : ''}</p>` : '')
    + (a.target_pattern ? `<p><span class="analysis-label">Target pattern</span> ${escapeHTML(label(a.target_pattern))}${a.target_pattern_notes ? ' — '+escapeHTML(a.target_pattern_notes) : ''}</p>` : '')
    + (a.color_scheme ? `<p><span class="analysis-label">Colour scheme</span> ${escapeHTML(label(a.color_scheme))}${a.color_notes ? ' — '+escapeHTML(a.color_notes) : ''}</p>` : '')
    + (swatches ? `<ul class="palette">${swatches}</ul>` : chips)
    + (reasons.length ? details(`Why each element matters (${reasons.length}, most important first)`, reasons.map(([id,w])=>`${id}: ${w}`).join('\n')) : '')
    + (rel.length ? details(`Relationships to keep (${rel.length})`, rel.join('\n')) : '')
    + details('Technical facts (position, size, layer)', a.technical) + '</div>';
}
function rationaleHTML(r) {
  if (!r || (!r.summary && !Object.keys(r.justifications || {}).length)) return '';
  const lines = Object.entries(r.justifications || {}).map(([id,w])=>`${id}: ${w}`);
  if (r.missing?.length) lines.push(`No justification given for: ${r.missing.join(', ')}`);
  return `<div class="analysis">${r.pattern ? `<p><span class="analysis-label">Pattern</span> ${escapeHTML(label(r.pattern))}</p>` : ''}${r.summary ? `<p class="analysis-description">${escapeHTML(r.summary)}</p>` : ''}${lines.length ? details(`Why each placement (${Object.keys(r.justifications || {}).length})`, lines.join('\n')) : ''}</div>`;
}
// Declining the image is an expected fallback (to extracted colours), not a failure.
const isIssue = s => s.outcome !== 'usable' && s.outcome !== 'images_unsupported';
const visualNames = {image:'rendered source image', palette:'colours extracted in code', text:'element facts only', none:'none (code heuristics)'};
function stepHTML(s, i) {
  let title = s.kind, body = '', failed = false;
  if (s.kind === 'llm_start') { title = `Model working · ${s.purpose}`; body = 'Request sent. Waiting for the provider to return a complete reply.'; }
  if (s.kind === 'llm_call') {
    const refused = s.outcome === 'images_unsupported'; failed = isIssue(s);
    title = `${refused ? 'Model declined the image' : failed ? 'Model request failed' : 'Model replied'} · ${s.purpose}`;
    const u=usageOf(s);
    body = `${seconds(s.seconds)} · ${count(u.total)} total tokens (${count(u.input)} in · ${count(u.output)} out · ${count(u.reasoning)} reasoning)${s.images ? ' · with source image' : ''}${s.finish_reason ? ' · finish: ' + s.finish_reason : ''}`;
    if (s.error) body += ' · ' + s.error;
  }
  if (s.kind === 'understand') { title = `Design analysed · ${s.difficulty}`; body = `${s.decided_by === 'llm' ? 'Model design analysis' : 'Code-assigned roles; no model analysis'} · visual input: ${visualNames[s.analysis?.visual_input] || 'none'}${s.pressure_override ? ' · high space pressure forced the hard path' : ''}`; }
  if (s.kind === 'visual_input') { title = `Visual input · ${visualNames[s.mode] || s.mode}`; body = s.used ? 'Sent with the design analysis request.' : s.reason; }
  if (s.kind === 'preset') { title = 'Preset layout checked'; body = s.decision; }
  if (s.kind === 'attempt') { title = `${s.name} checked`; body = s.new_best ? 'New best layout.' : 'The earlier layout is still preferred.'; }
  if (s.kind === 'tie_break') { title = 'Compared equal rule costs'; body = `${s.a} vs ${s.b}: ${s.winner} wins · ${s.decided_by}`; }
  if (s.kind === 'loop_end') { title = 'Design loop finished'; body = s.reason; }
  if (s.kind === 'model_recovery') { title = s.recovered ? 'Adjusted model settings for one retry' : 'Stopped truncated requests'; body = s.reason; }
  if (s.kind === 'result') { title = `Returned ${s.winner}`; body = s.reason; }
  if (s.kind === 'error') { title = 'Engine error · safety layout returned'; body = s.error; failed = true; }
  const report=s.report;
  const badge = report ? `<span class="mini${report.passes ? '' : ' bad'}">${report.passes ? 'Rules pass' : `${report.rules.length} rule violations`}</span>` : '';
  const reportHTML = report ? `<p>Rule cost ${report.rule_cost} · quality signal cost ${report.signal_cost}</p>` + details(`Rule violations (${report.rules.length})`,report.rules.length ? report.rules.map(v=>`${v.element_id || 'Canvas'}: ${v.message}`).join('\n') : 'None.') + details(`Quality observations (${report.signals.length})`,report.signals.length ? report.signals.map(v=>`${v.element_id || 'Canvas'}: ${v.message}`).join('\n') : 'None detected. This is not a visual quality guarantee.') : '';
  return `<article class="step${failed ? ' failure' : ''}" data-step="${i}"><div class="step-header"><h3>${escapeHTML(title)}${badge}</h3><time>+${seconds(s.at_seconds)}</time></div><p>${escapeHTML(body)}</p>${s.kind === 'understand' ? analysisHTML(s.analysis) : ''}${s.kind === 'attempt' || s.kind === 'result' ? rationaleHTML(s.rationale) : ''}${reportHTML}${details('Provider reasoning / summary',s.reasoning)}${details('Roles and priorities',s.roles ? {roles:s.roles,priorities:s.priorities} : null)}${details('Prompt',s.prompt,true)}${details('Raw reply',s.reply,true)}${details('Layout JSON',s.layout,true)}${details('Traceback',s.traceback,true)}</article>`;
}
function updateRun(after) {
  const r = state.run;
  if (!after) $('timeline').replaceChildren();
  r.steps.slice(after).forEach((s,i) => $('timeline').insertAdjacentHTML('beforeend',stepHTML(s,after+i)));
  showWarnings([...new Set(r.warnings || [])]);
  const failures=r.steps.filter(s=>s.kind==='llm_call' && isIssue(s));
  const usable=r.steps.some(s=>s.kind==='llm_call' && s.outcome==='usable');
  $('state').textContent = r.state==='running' ? 'Running' : r.state==='done' ? (failures.length && !usable ? 'Complete · model unavailable' : 'Complete') : r.state;
  $('state').classList.toggle('bad',r.state==='failed' || failures.length>0);
  if (r.images.source && $('source-image').getAttribute('src')!==r.images.source) showSource(r.task,r.images.source);
  const candidates = r.steps.map((s,i)=>({s,i})).filter(({s})=>s.layout);
  $('candidates').innerHTML = candidates.map(({s,i})=>`<button type="button" class="candidate${state.selected===i ? ' active' : ''}" data-candidate="${i}">${escapeHTML(s.kind==='result' ? 'Returned layout' : s.name || 'Preset layout')}<small>${s.report ? s.report.passes ? 'Rules pass' : s.report.rules.length+' rule violations' : 'Safety layout'}</small></button>`).join('');
  if (state.selected===null || (r.state==='running' && after<r.steps.length)) state.selected=candidates.at(-1)?.i ?? null;
  showCandidate(state.selected); updateMetrics();
  const last=r.steps.at(-1);
  if (r.error) message(r.error,true);
  else if (r.state==='running') message(last?.kind==='llm_start' ? `Waiting for ${last.model} · ${last.purpose}…` : 'Processing design…');
  else if (r.state==='cancelled') message('Run stopped. Completed steps remain available.');
  else message(failures.length ? `Run complete with ${failures.length} model request issue(s). Open the trace for details.` : 'Run complete. Select a candidate to compare layouts.', !!failures.length);
  $('downloads').innerHTML = r.id.startsWith('legacy-') ? `<a href="/media/traces/${encodeURIComponent(r.name)}.json" download>Trace JSON ↓</a>` : `<a href="/media/workbench/${r.id}/trace.json" download>Trace JSON ↓</a>${r.state==='done' ? `<a href="/media/workbench/${r.id}/output.json" download>Design JSON ↓</a>` : ''}`;
}
function showCandidate(index) {
  if (index===null || !state.run) return;
  state.selected=index;
  const r=state.run,s=r.steps[index],src=r.images[`step${index}`];
  $('result-title').textContent=s.kind==='result' ? `Returned · ${s.winner}` : s.name || 'Preset layout';
  if (src) { $('result-image').src=src; $('result-image').hidden=false; $('result-empty').hidden=true; }
  else { $('result-image').hidden=true; $('result-empty').hidden=false; $('result-empty').textContent=r.state==='running' ? 'Rendering preview…' : 'Preview unavailable. Check run warnings.'; }
  const summary=s.rationale?.summary || '';
  $('result-summary').hidden=!summary; $('result-summary').textContent=summary;
  $('result-foot').textContent=s.report ? `${s.report.passes ? 'Rules pass' : s.report.rules.length+' rule violations'} · ${s.report.signals.length} quality observations · rule cost ${s.report.rule_cost}` : 'Safety layout · inspect the error in the trace.';
  document.querySelectorAll('[data-candidate]').forEach(b=>b.classList.toggle('active',Number(b.dataset.candidate)===index));
}
function usageOf(s) {
  const u=s.usage || {}, input=u.prompt_tokens || 0, output=u.completion_tokens || 0;
  // Ollama reports no total; Gemini's total already includes thought tokens.
  return {input, output, reasoning:u.completion_tokens_details?.reasoning_tokens || 0, total:u.total_tokens || input+output};
}
function updateUsage(r,calls,elapsed) {
  $('usage').hidden=!r;
  if (!r) return;
  const rows=calls.map(s=>({s,...usageOf(s)})), sum=k=>rows.reduce((n,x)=>n+x[k],0);
  const modelTime=calls.reduce((n,s)=>n+(s.seconds || 0),0);
  const pending=r.state==='running' && r.steps.at(-1)?.kind==='llm_start';
  const stat=(label,value)=>`<div><strong>${value}</strong><span>${label}</span></div>`;
  const table=rows.length ? `<div class="usage-table"><table><thead><tr><th>#</th><th>Request</th><th>Outcome</th><th>Time</th><th>Input</th><th>Output</th><th>Reasoning</th><th>Total</th></tr></thead><tbody>${rows.map((x,i)=>`<tr${isIssue(x.s) ? ' class="bad"' : ''}><td>${i+1}</td><td>${escapeHTML(x.s.purpose)}</td><td>${escapeHTML(x.s.outcome)}</td><td>${seconds(x.s.seconds)}</td><td>${count(x.input)}</td><td>${count(x.output)}</td><td>${count(x.reasoning)}</td><td>${count(x.total)}</td></tr>`).join('')}</tbody><tfoot><tr><td></td><td colspan="2">All requests</td><td>${seconds(modelTime)}</td><td>${count(sum('input'))}</td><td>${count(sum('output'))}</td><td>${count(sum('reasoning'))}</td><td>${count(sum('total'))}</td></tr></tfoot></table></div>` : '';
  $('usage').innerHTML=`<div class="usage-head"><div><span>Total token usage</span><strong>${count(sum('total'))}</strong></div><p>${rows.length ? `${rows.length} model request${rows.length===1 ? '' : 's'}` : 'No model requests · preset only or model skipped'}${pending ? ' · one request in progress' : ''}</p></div>`
    + `<div class="usage-stats">${stat('input tokens',count(sum('input')))}${stat('output tokens',count(sum('output')))}${stat('reasoning tokens',count(sum('reasoning')))}${stat('time in model requests',seconds(modelTime))}${stat('time in code',seconds(Math.max(0,elapsed-modelTime)))}${stat('total elapsed',seconds(elapsed))}</div>${table}`;
}
function updateMetrics() {
  const r=state.run,calls=(r?.steps || []).filter(s=>s.kind==='llm_call');
  const reasoning=calls.reduce((n,s)=>n+(s.usage?.completion_tokens_details?.reasoning_tokens || 0),0);
  const completion=calls.reduce((n,s)=>n+(s.usage?.completion_tokens || 0),0);
  const final=(r?.steps || []).findLast(s=>s.kind==='result');
  const elapsed=r ? r.state==='running' ? (Date.now()/1000-r.created) : r.seconds || Math.max(0,...r.steps.map(s=>s.at_seconds || 0)) : 0;
  const values = [r ? seconds(elapsed) : '—',r ? String(calls.length) : '—',r ? count(reasoning) : '—',final?.report ? String(final.report.rules.length) : '—'];
  [...$('metrics').children].forEach((el,i)=>el.querySelector('strong').textContent=values[i]);
  updateUsage(r,calls,elapsed);
  if (calls.length) {
    const failed=calls.filter(isIssue).reduce((n,s)=>n+(s.seconds || 0),0);
    const total=calls.reduce((n,s)=>n+(s.seconds || 0),0);
    // Gemini completion_tokens excludes thought tokens; OpenAI includes them.
    const denom=r.model?.startsWith('gemini') ? completion+reasoning : completion;
    $('insight').hidden=false;
    $('insight').textContent=`${seconds(total)} in model requests. ${failed ? seconds(failed)+' spent on failed requests. ' : ''}${reasoning && denom ? `${Math.round(100*reasoning/denom)}% of generated tokens were reasoning. Try low/off and compare the returned layout, rule violations and time.` : 'No reasoning tokens reported. Compare image quality as well as speed.'}`;
  }
}
$('run').addEventListener('click',startRun);
$('task').addEventListener('change',selectTask);
$('width').addEventListener('input',updateTarget); $('height').addEventListener('input',updateTarget);
$('provider').addEventListener('change',providerChanged);
$('show-details').addEventListener('change',()=>document.body.classList.toggle('show-raw',$('show-details').checked));
$('refresh-history').addEventListener('click',()=>history().catch(e=>message(e.message,true)));
$('history').addEventListener('click',e=>{const b=e.target.closest('[data-run]');if(b)openRun(b.dataset.run);});
$('candidates').addEventListener('click',e=>{const b=e.target.closest('[data-candidate]');if(b)showCandidate(Number(b.dataset.candidate));});
for (const id of ['source-image','result-image']) $(id).addEventListener('click',()=>window.open($(id).src,'_blank','noopener'));
$('cancel').addEventListener('click',async()=>{try{const data=await api('/api/cancel/'+state.run.id,{});message(data.message);}catch(e){message(e.message,true);}});
$('refresh-models').addEventListener('click',async()=>{
  $('refresh-models').disabled=true;message('Fetching models available to your key…');
  try{const data=await api('/api/models',{api_key:$('api-key').value});$('model-list').replaceChildren(...data.models.map(name=>new Option(name,name)));message(`${data.models.length} models fetched. Type in the model field to choose.`);}catch(e){message(e.message,true);}finally{$('refresh-models').disabled=false;}
});
$('upload').addEventListener('change',async()=>{
  const file=$('upload').files[0];if(!file)return;
  try{if(file.size>2_000_000)throw new Error('Task must be below 2 MB.');const task=JSON.parse(await file.text());const data=await api('/api/import',{name:file.name.replace(/\.json$/i,''),task});if(![...$('task').options].some(o=>o.value===data.id))$('task').add(new Option(file.name,data.id));$('task').value=data.id;await selectTask();}catch(e){message(e.message,true);}finally{$('upload').value='';}
});
setInterval(()=>{if(state.busy)updateMetrics();},1000);
(async()=>{try{state.config=await api('/api/config');$('task').replaceChildren(...state.config.tasks.map(t=>new Option(niceName(t.name),t.id)));providerChanged();await history();await selectTask();if(!state.config.renderer)message('Renderer missing. Install requirements.txt before running previews.',true);}catch(e){message(e.message,true);}})();
