// ---- elements ----
const robotLed = document.getElementById('robot-led');
const robotText = document.getElementById('robot-text');
const probeTraj = document.getElementById('probe-traj');
const probeJoints = document.getElementById('probe-joints');
const probeIo = document.getElementById('probe-io');

const bridgeNode = document.getElementById('bridge-node');
const nodeCount = document.getElementById('node-count');
const nodeList = document.getElementById('node-list');

const selMode = document.getElementById('sel-mode');
const selReasoning = document.getElementById('sel-reasoning');
const selProvider = document.getElementById('sel-provider');
const selModel = document.getElementById('sel-model');
const inpTemp = document.getElementById('inp-temp');
const chkMockLlm = document.getElementById('chk-mock-llm');
const optionsError = document.getElementById('options-error');

const cfgApply = document.getElementById('cfg-apply');
const cfgResult = document.getElementById('cfg-result');

const driverState = document.getElementById('driver-state');
const driverInfo = document.getElementById('driver-info');
const driverLogs = document.getElementById('driver-logs');
const driverStart = document.getElementById('driver-start');
const driverReconnect = document.getElementById('driver-reconnect');
const driverStop = document.getElementById('driver-stop');
const driverHistoryWrap = document.getElementById('driver-history-wrap');
const driverHistory = document.getElementById('driver-history');
const inpRobotIp = document.getElementById('inp-robot-ip');
const inpReverseIp = document.getElementById('inp-reverse-ip');
const headerReconnect = document.getElementById('header-reconnect');

const chkMockRobot = document.getElementById('chk-mock-robot');
const chkMockCamera = document.getElementById('chk-mock-camera');
const stackState = document.getElementById('stack-state');
const stackInfo = document.getElementById('stack-info');
const stackLogs = document.getElementById('stack-logs');
const stackStart = document.getElementById('stack-start');
const stackRestart = document.getElementById('stack-restart');
const stackStop = document.getElementById('stack-stop');

let providerModels = {};

// ---- helpers ----
function fillSelect(sel, values) {
  sel.innerHTML = '';
  values.forEach((v) => {
    const opt = document.createElement('option');
    opt.value = v;
    opt.textContent = v;
    sel.appendChild(opt);
  });
}

function setDot(el, ok) {
  el.className = 'dot ' + (ok ? 'dot-green' : 'dot-red');
}

// ---- options (fetched once) ----
async function loadOptions() {
  try {
    const res = await fetch('/api/options');
    const data = await res.json();

    if (data.error) {
      optionsError.hidden = false;
      optionsError.textContent = 'Could not load model registry: ' + data.error;
    }

    fillSelect(selMode, data.modes || []);
    fillSelect(selReasoning, data.reasoning_methods || []);

    providerModels = data.providers || {};
    const providers = Object.keys(providerModels);
    fillSelect(selProvider, providers);
    if (providers.length) fillSelect(selModel, providerModels[providers[0]]);

    if (data.temperature_default != null) inpTemp.value = data.temperature_default;
    syncCameraToggle();
  } catch (err) {
    optionsError.hidden = false;
    optionsError.textContent = 'Failed to fetch /api/options: ' + err;
  }
}

// Keep the model list consistent with the chosen provider.
selProvider.addEventListener('change', () => {
  fillSelect(selModel, providerModels[selProvider.value] || []);
});

// ---- live config (B2) ----
function currentConfig() {
  // The backend expects model_name as "provider/model" (see ModelRegistry);
  // the dropdown keys are bare, so prefix them with the selected provider.
  const model = selModel.value ? `${selProvider.value}/${selModel.value}` : '';
  return {
    mode: selMode.value || 'LLM',
    reasoning_method: selReasoning.value,
    model_name: model,
    temperature: parseFloat(inpTemp.value),
    use_mock_llm: chkMockLlm.checked,
  };
}

cfgApply.addEventListener('click', async () => {
  cfgApply.disabled = true;
  cfgResult.className = 'muted';
  cfgResult.textContent = 'applying…';
  try {
    const data = await postJSON('/api/config', currentConfig());
    if (data.error) {
      cfgResult.className = 'error';
      cfgResult.textContent = data.error;
    } else if (data.applied) {
      cfgResult.className = 'ok';
      cfgResult.textContent = `applied to ${data.target}`;
    } else {
      cfgResult.className = 'error';
      const failed = Object.entries(data.results || {})
        .filter(([, r]) => !r.successful)
        .map(([k, r]) => `${k}: ${r.reason || 'rejected'}`)
        .join('; ');
      cfgResult.textContent = failed || 'some parameters were rejected';
    }
  } catch (err) {
    cfgResult.className = 'error';
    cfgResult.textContent = 'request failed: ' + err;
  } finally {
    cfgApply.disabled = false;
  }
});

// ---- stack orchestration (B1) ----
function stackPayload() {
  return {
    mock_robot: chkMockRobot.checked,
    mock_camera: chkMockCamera.checked,
    ...currentConfig(),
  };
}

// The camera only exists in VLM mode — disable its toggle in LLM mode.
function syncCameraToggle() {
  const vlm = (selMode.value || 'LLM').toUpperCase() === 'VLM';
  chkMockCamera.disabled = !vlm;
  chkMockCamera.parentElement.style.opacity = vlm ? '1' : '0.5';
}
selMode.addEventListener('change', syncCameraToggle);

function renderStack(status) {
  const running = !!status.running;
  stackState.textContent = running ? 'running' : 'stopped';
  stackState.className = 'badge ' + (running ? 'badge-on' : 'badge-off');
  stackStart.disabled = running;
  stackStop.disabled = !running;

  if (status.target) {
    const t = status.target;
    const parts = [
      t.mode,
      `robot:${t.mock_robot ? 'mock' : 'real'}`,
      t.mode === 'VLM' ? `cam:${t.mock_camera ? 'mock' : 'real'}` : null,
      t.mock_llm ? 'llm:mock' : 'llm:real',
    ].filter(Boolean);
    stackInfo.textContent =
      parts.join(' · ') +
      (status.pid ? ` · pid ${status.pid}` : '') +
      (status.returncode != null ? ` · exit ${status.returncode}` : '');
  } else {
    stackInfo.textContent = '';
  }

  const logs = status.logs || [];
  stackLogs.textContent = logs.length
    ? logs.map((l) => `${l.t}  ${l.line}`).join('\n')
    : '(no output yet)';
  stackLogs.scrollTop = stackLogs.scrollHeight;
}

async function pollStack() {
  try {
    renderStack(await (await fetch('/api/stack')).json());
  } catch (_e) { /* backend unreachable — health poll already flags it */ }
}

async function stackAction(url, withPayload) {
  [stackStart, stackRestart, stackStop].forEach((b) => (b.disabled = true));
  try {
    const data = await postJSON(url, withPayload ? stackPayload() : {});
    if (data.error) {
      stackInfo.textContent = data.error;
    } else if (data.status) {
      renderStack(data.status);
    }
  } catch (err) {
    stackInfo.textContent = 'request failed: ' + err;
  } finally {
    pollStack();
  }
}

stackStart.addEventListener('click', () => stackAction('/api/stack/start', true));
stackRestart.addEventListener('click', () => stackAction('/api/stack/restart', true));
stackStop.addEventListener('click', () => stackAction('/api/stack/stop', false));

// ---- UR driver supervisor (B5 / Phase 5) ----
// The stock UR driver fails intermittently on startup, so the supervisor
// retries until the robot is reachable. The UI reflects its state machine
// (stopped/connecting/connected/failed), per-attempt history and log tail.
const DRIVER_BADGE = {
  connected: 'badge-on',
  connecting: 'badge-amber',
  failed: 'badge-off',
  stopped: 'badge-off',
};

function driverPayload() {
  // Send only non-empty IPs; the backend falls back to its defaults.
  const payload = {};
  if (inpRobotIp.value.trim()) payload.robot_ip = inpRobotIp.value.trim();
  if (inpReverseIp.value.trim()) payload.reverse_ip = inpReverseIp.value.trim();
  return payload;
}

function renderDriver(status) {
  const state = status.state || 'stopped';
  driverState.textContent = state;
  driverState.className = 'badge ' + (DRIVER_BADGE[state] || 'badge-off');

  const busy = state === 'connecting';
  driverStart.disabled = busy || state === 'connected';
  driverStop.disabled = state === 'stopped';

  // Reflect resolved IPs (defaults included) without clobbering an active edit.
  const p = status.params || {};
  if (p.robot_ip && document.activeElement !== inpRobotIp) inpRobotIp.value = p.robot_ip;
  if (p.reverse_ip && document.activeElement !== inpReverseIp) inpReverseIp.value = p.reverse_ip;

  const bits = [];
  if (state === 'connecting' && status.attempt) {
    bits.push(`attempt ${status.attempt}/${status.max_attempts}`);
  }
  if (status.robot_ready) bits.push('robot ready');
  if (status.pid) bits.push(`pid ${status.pid}`);
  driverInfo.textContent = bits.join(' · ');

  const history = status.history || [];
  driverHistoryWrap.hidden = history.length === 0;
  driverHistory.innerHTML = '';
  history.forEach((h) => {
    const li = el('li', null,
      `#${h.attempt} ${h.started} · ${h.outcome} · ${h.duration_s}s` +
      (h.error ? ` — ${h.error}` : ''));
    driverHistory.appendChild(li);
  });

  const logs = status.logs || [];
  driverLogs.textContent = logs.length
    ? logs.map((l) => `${l.t}  ${l.line}`).join('\n')
    : '(driver not started)';
  driverLogs.scrollTop = driverLogs.scrollHeight;
}

async function pollDriver() {
  try {
    renderDriver(await (await fetch('/api/driver')).json());
  } catch (_e) { /* backend unreachable — health poll already flags it */ }
}

async function driverAction(url, withPayload) {
  [driverStart, driverReconnect, driverStop, headerReconnect].forEach((b) => (b.disabled = true));
  try {
    const data = await postJSON(url, withPayload ? driverPayload() : {});
    if (data.error) {
      driverInfo.textContent = data.error;
    } else if (data.status) {
      renderDriver(data.status);
    }
  } catch (err) {
    driverInfo.textContent = 'request failed: ' + err;
  } finally {
    pollDriver();
  }
}

driverStart.addEventListener('click', () => driverAction('/api/driver/start', true));
driverReconnect.addEventListener('click', () => driverAction('/api/driver/reconnect', true));
driverStop.addEventListener('click', () => driverAction('/api/driver/stop', false));
headerReconnect.addEventListener('click', () => driverAction('/api/driver/reconnect', true));

// ---- health (polled) ----
async function pollHealth() {
  try {
    const res = await fetch('/api/health');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    const robot = data.robot || { level: 'red', probes: {} };
    robotLed.className = 'led led-' + robot.level;
    robotText.textContent = 'robot: ' + robot.level;
    setDot(probeTraj, robot.probes.trajectory_server);
    setDot(probeJoints, robot.probes.joint_states);
    setDot(probeIo, robot.probes.gripper_io);

    bridgeNode.textContent = data.bridge_node || '—';
    nodeCount.textContent = data.node_count;
    nodeList.innerHTML = '';
    (data.discovered_nodes || []).forEach((n) => {
      const li = document.createElement('li');
      li.textContent = n;
      nodeList.appendChild(li);
    });
  } catch (err) {
    robotLed.className = 'led led-red';
    robotText.textContent = 'backend unreachable';
  }
}

// ---- chat ----
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const chatSend = document.getElementById('chat-send');
const chatHistory = document.getElementById('chat-history');

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = text;
  return node;
}

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  return res.json();
}

function scrollIntoView(node) {
  node.scrollIntoView({ behavior: 'smooth', block: 'end' });
}

// Render a single plan step as <li> with status dot, action name and arg chips.
function renderStep(step) {
  const li = el('li', 'plan-step pending');
  li.appendChild(el('span', 'step-status'));

  const body = el('div', 'step-body');
  const head = el('div', 'step-head');
  head.appendChild(el('span', 'step-idx', String(step.step ?? '?')));
  head.appendChild(el('span', 'step-action', step.action_name ?? '?'));
  body.appendChild(head);

  const args = Object.entries(step).filter(
    ([k]) => k !== 'step' && k !== 'action_name'
  );
  if (args.length) {
    const chips = el('div', 'step-args');
    args.forEach(([k, v]) => {
      const chip = el('span', 'arg-chip');
      chip.appendChild(el('span', 'arg-key', k));
      chip.appendChild(el('span', 'arg-val', typeof v === 'object' ? JSON.stringify(v) : String(v)));
      chips.appendChild(chip);
    });
    body.appendChild(chips);
  }

  li.appendChild(body);
  return li;
}

// Build the polished plan card; returns a map of step-index -> <li> so the
// live execution log can flip each step's status as it completes.
function renderPlan(block, plan) {
  const stepEls = {};
  if (plan.task_summary) {
    block.appendChild(el('div', 'plan-summary', plan.task_summary));
  }
  const list = el('ol', 'plan-steps');
  (plan.plan || []).forEach((step) => {
    const li = renderStep(step);
    list.appendChild(li);
    stepEls[String(step.step ?? '?')] = li;
  });
  block.appendChild(list);

  const details = el('details', 'raw-plan');
  details.appendChild(el('summary', null, 'raw JSON'));
  details.appendChild(el('pre', null, JSON.stringify(plan, null, 2)));
  block.appendChild(details);

  return stepEls;
}

function setStepState(li, state) {
  if (!li) return;
  li.className = 'plan-step ' + state;
}

// A log line looks like "[Step 2] pick -> OK" — mark that step done.
function applyLogLine(stepEls, line) {
  const m = line.match(/\[Step\s+(\S+?)\]/);
  if (m) setStepState(stepEls[m[1]], 'done');
}

async function sendCommand(command) {
  const block = el('div', 'msg pending');
  block.appendChild(el('div', 'cmd', command));
  const status = el('div', 'label', 'planning…');
  block.appendChild(status);
  chatHistory.appendChild(block);
  scrollIntoView(block);

  // 1. Plan.
  let planData;
  try {
    planData = await postJSON('/api/plan', { command });
  } catch (err) {
    block.className = 'msg failed';
    block.appendChild(el('div', 'error', 'Request failed: ' + err));
    return;
  }
  if (planData.error) {
    block.className = 'msg failed';
    status.remove();
    block.appendChild(el('div', 'error', 'Planning failed: ' + planData.error));
    scrollIntoView(block);
    return;
  }

  // 2. Show the polished plan immediately, before execution starts.
  const stepEls = renderPlan(block, planData.plan);
  status.textContent = 'executing…';
  scrollIntoView(block);

  // 3. Stream /execution_log over a WebSocket while the robot runs.
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/execution`);
  ws.onmessage = (ev) => {
    try {
      applyLogLine(stepEls, JSON.parse(ev.data).log);
      scrollIntoView(block);
    } catch (_e) { /* ignore malformed frame */ }
  };
  await new Promise((resolve) => {
    ws.onopen = resolve;
    ws.onerror = resolve; // proceed even if the log stream is unavailable
  });

  // 4. Execute.
  let execData;
  try {
    execData = await postJSON('/api/execute', { plan_json: planData.plan_json });
  } catch (err) {
    block.className = 'msg failed';
    status.remove();
    block.appendChild(el('div', 'error', 'Request failed: ' + err));
    ws.close();
    return;
  } finally {
    ws.close();
  }

  status.remove();
  if (execData.error) {
    block.className = 'msg failed';
    // Mark the first not-yet-done step as failed for a clear visual cue.
    const pending = block.querySelector('.plan-step.pending');
    setStepState(pending, 'failed');
    block.appendChild(el('div', 'error', 'Execution failed: ' + execData.error));
  } else {
    block.className = 'msg';
    Object.values(stepEls).forEach((li) => setStepState(li, 'done'));
    block.appendChild(el('div', 'label', 'execution report'));
    block.appendChild(el('pre', 'report', execData.report || '(no report)'));
  }
  scrollIntoView(block);
}

chatForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  const command = chatInput.value.trim();
  if (!command) return;
  chatInput.value = '';
  chatSend.disabled = true;
  chatInput.disabled = true;
  try {
    await sendCommand(command);
  } finally {
    chatSend.disabled = false;
    chatInput.disabled = false;
    chatInput.focus();
  }
});

loadOptions();
pollHealth();
pollStack();
pollDriver();
setInterval(pollHealth, 2000);
setInterval(pollStack, 2000);
setInterval(pollDriver, 2000);
