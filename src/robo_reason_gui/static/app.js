// ---- elements ----
const robotLed = document.getElementById('robot-led');
const ledToggle = document.getElementById('led-toggle');
const robotPopover = document.getElementById('robot-popover');
const popoverDriver = document.getElementById('popover-driver');
const probeTraj = document.getElementById('probe-traj');
const probeJoints = document.getElementById('probe-joints');
const probeIo = document.getElementById('probe-io');
const probePendant = document.getElementById('probe-pendant');
const calibLed = document.getElementById('calib-led');

const cameraImg = document.getElementById('camera-img');
const cameraPlaceholder = document.getElementById('camera-placeholder');
const cameraState = document.getElementById('camera-state');

const planContent = document.getElementById('plan-content');
const planState = document.getElementById('plan-state');

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

const camSvcState = document.getElementById('cam-svc-state');
const camSvcInfo = document.getElementById('cam-svc-info');
const camSvcLogs = document.getElementById('cam-svc-logs');
const camSvcStart = document.getElementById('cam-svc-start');
const camSvcStop = document.getElementById('cam-svc-stop');

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

// Provider→model maps, split by mode. Populated once in loadOptions().
let llmProviderModels = {};
let vlmProviderModels = {};

function currentProviderModels() {
  return (selMode.value || 'LLM').toUpperCase() === 'VLM'
    ? vlmProviderModels
    : llmProviderModels;
}

// ---- toasts ----
// Lightweight transient notifications so action outcomes (config applied, stack
// errors, driver state) are visible without hunting for inline text.
const toasts = document.getElementById('toasts');

function toast(message, kind = 'info', timeout = 4000) {
  const node = el('div', 'toast toast-' + kind, message);
  toasts.appendChild(node);
  requestAnimationFrame(() => node.classList.add('show'));
  setTimeout(() => {
    node.classList.remove('show');
    setTimeout(() => node.remove(), 200);
  }, timeout);
}

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

// ---- LED popover (robot-connection detail) ----
ledToggle.addEventListener('click', (e) => {
  e.stopPropagation();
  const show = robotPopover.hidden;
  robotPopover.hidden = !show;
  ledToggle.setAttribute('aria-expanded', String(show));
});
// Close when clicking anywhere outside the popover.
document.addEventListener('click', (e) => {
  if (robotPopover.hidden) return;
  if (!robotPopover.contains(e.target) && e.target !== ledToggle) {
    robotPopover.hidden = true;
    ledToggle.setAttribute('aria-expanded', 'false');
  }
});

// ---- copy buttons (chat + log terminals) ----
async function copyText(text, label) {
  if (!text || !text.trim()) {
    toast('Nothing to copy', 'info');
    return;
  }
  // Try the modern async clipboard API first (requires HTTPS or localhost).
  // Fall back to the legacy execCommand approach which works on plain HTTP
  // (e.g. accessing the GUI by IP address from the host machine).
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      toast(`${label} copied`, 'success', 2000);
      return;
    } catch (_e) { /* fall through */ }
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
    document.body.appendChild(ta);
    ta.focus();
    ta.select();
    document.execCommand('copy');
    document.body.removeChild(ta);
    toast(`${label} copied`, 'success', 2000);
  } catch (_e) {
    toast('Copy failed', 'error');
  }
}

document.getElementById('chat-copy').addEventListener('click', () =>
  copyText(chatHistory.innerText, 'Chat'));
document.getElementById('stack-copy').addEventListener('click', () =>
  copyText(stackLogs.textContent, 'Stack logs'));
document.getElementById('driver-copy').addEventListener('click', () =>
  copyText(driverLogs.textContent, 'Driver logs'));
document.getElementById('cam-svc-copy').addEventListener('click', () =>
  copyText(camSvcLogs.textContent, 'Camera logs'));

// ---- camera feed (polled JPEG frames) ----
// Poll /api/camera/frame; on a 503 the camera isn't up yet, so show the
// placeholder. Object URLs are revoked as we swap frames to avoid leaking blobs.
let cameraObjectUrl = null;

async function pollCamera() {
  try {
    const res = await fetch('/api/camera/frame', { cache: 'no-store' });
    if (!res.ok) throw new Error('no frame');
    const blob = await res.blob();
    const url = URL.createObjectURL(blob);
    cameraImg.src = url;
    cameraImg.style.display = 'block';
    cameraPlaceholder.style.display = 'none';
    cameraState.textContent = 'live';
    cameraState.className = 'badge badge-on';
    if (cameraObjectUrl) URL.revokeObjectURL(cameraObjectUrl);
    cameraObjectUrl = url;
  } catch (_e) {
    cameraImg.style.display = 'none';
    cameraPlaceholder.style.display = 'block';
    cameraState.textContent = 'no feed';
    cameraState.className = 'badge badge-off';
  }
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

    llmProviderModels = data.providers || {};
    vlmProviderModels = data.vlm_providers || {};

    if (data.temperature_default != null) inpTemp.value = data.temperature_default;
    syncModelsByMode();  // populates providers + models for the initial mode
  } catch (err) {
    optionsError.hidden = false;
    optionsError.textContent = 'Failed to fetch /api/options: ' + err;
  }
}

// Keep the model list consistent with the chosen provider.
selProvider.addEventListener('change', () => {
  fillSelect(selModel, currentProviderModels()[selProvider.value] || []);
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
      toast('Config: ' + data.error, 'error');
    } else if (data.applied) {
      cfgResult.className = 'ok';
      cfgResult.textContent = `applied to ${data.target}`;
      toast(`Config applied to ${data.target}`, 'success');
    } else {
      cfgResult.className = 'error';
      const failed = Object.entries(data.results || {})
        .filter(([, r]) => !r.successful)
        .map(([k, r]) => `${k}: ${r.reason || 'rejected'}`)
        .join('; ');
      cfgResult.textContent = failed || 'some parameters were rejected';
      toast('Config: ' + (failed || 'some parameters were rejected'), 'error');
    }
  } catch (err) {
    cfgResult.className = 'error';
    cfgResult.textContent = 'request failed: ' + err;
    toast('Config request failed: ' + err, 'error');
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

// The camera toggle and model lists both depend on the current mode.
function syncCameraToggle() {
  const vlm = (selMode.value || 'LLM').toUpperCase() === 'VLM';
  chkMockCamera.disabled = !vlm;
  chkMockCamera.parentElement.style.opacity = vlm ? '1' : '0.5';
}

// Switch provider→model dropdowns when mode changes (LLM ↔ VLM show
// different model subsets), then also sync the camera toggle.
function syncModelsByMode() {
  const map = currentProviderModels();
  const providers = Object.keys(map);
  const prevProvider = selProvider.value;
  fillSelect(selProvider, providers);
  // Preserve the selected provider if it still exists in the new list.
  if (providers.includes(prevProvider)) selProvider.value = prevProvider;
  fillSelect(selModel, map[selProvider.value] || []);
  syncCameraToggle();
}
selMode.addEventListener('change', syncModelsByMode);

// Gate stack Start/Restart on real-robot readiness: in real-robot mode (mock
// robot unchecked) the stack talks to controllers that only exist once the
// GUI-owned UR driver is up AND the teach pendant has accepted the reverse
// interface — starting the stack earlier just produces confusing planner/
// executor timeouts. Mock-robot mode has no such dependency.
let stackRunning = false;
let robotReady = false;

function updateStackStartGate() {
  const needsRealRobot = !chkMockRobot.checked;
  const blockedByDriver = needsRealRobot && !robotReady;
  stackStart.disabled = stackRunning || blockedByDriver;
  stackRestart.disabled = blockedByDriver;
  stackStart.title = blockedByDriver
    ? 'Start the UR driver and wait for it to connect (green LED) before starting the stack in real-robot mode.'
    : '';
}
chkMockRobot.addEventListener('change', updateStackStartGate);

function renderStack(status) {
  const running = !!status.running;
  stackState.textContent = running ? 'running' : 'stopped';
  stackState.className = 'badge ' + (running ? 'badge-on' : 'badge-off');
  stackRunning = running;
  updateStackStartGate();
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
      toast('Stack: ' + data.error, 'error');
    } else if (data.status) {
      renderStack(data.status);
      toast(data.status.running ? 'Stack running' : 'Stack stopped', 'success');
      // The supervisor reaps orphaned stack processes before launching.
      if (data.warning) toast('Stack: ' + data.warning, 'info', 6000);
    }
  } catch (err) {
    stackInfo.textContent = 'request failed: ' + err;
    toast('Stack request failed: ' + err, 'error');
  } finally {
    pollStack();
    checkPreflight();
  }
}

// ---- preflight: warn loudly if a duplicate /execute_skill server appears ----
// Only toast on the transition into the duplicate state so we don't spam the
// 2s poll. A duplicate executor runs every skill twice and corrupts robot state.
let lastDuplicate = false;

async function checkPreflight() {
  try {
    const pf = await (await fetch('/api/preflight')).json();
    if (pf.duplicate && !lastDuplicate) {
      toast(pf.message, 'error', 10000);
    }
    lastDuplicate = !!pf.duplicate;
  } catch (_e) { /* backend unreachable — health poll flags it */ }
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

let lastDriverState = null;

function renderDriver(status) {
  const state = status.state || 'stopped';
  // Toast only on meaningful transitions so the operator notices connect/fail
  // without watching the card. Skip the very first render (page load).
  if (lastDriverState !== null && state !== lastDriverState) {
    if (state === 'connected') toast('UR driver connected', 'success');
    else if (state === 'failed') toast('UR driver failed to connect', 'error');
  }
  lastDriverState = state;

  driverState.textContent = state;
  driverState.className = 'badge ' + (DRIVER_BADGE[state] || 'badge-off');

  // Mirror the driver state into the LED popover, noting whether the teach
  // pendant has connected on the reverse interface.
  let driverLine = 'driver: ' + state;
  if (state === 'connected') {
    driverLine += status.robot_connected ? ' · pendant connected' : ' · pendant not connected';
  }
  popoverDriver.textContent = driverLine;

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
      toast('UR driver: ' + data.error, 'error');
    } else if (data.status) {
      renderDriver(data.status);
    }
  } catch (err) {
    driverInfo.textContent = 'request failed: ' + err;
    toast('UR driver request failed: ' + err, 'error');
  } finally {
    pollDriver();
  }
}

driverStart.addEventListener('click', () => driverAction('/api/driver/start', true));
driverReconnect.addEventListener('click', () => driverAction('/api/driver/reconnect', true));
driverStop.addEventListener('click', () => driverAction('/api/driver/stop', false));
headerReconnect.addEventListener('click', () => driverAction('/api/driver/reconnect', true));

// ---- camera service supervisor ----
function renderCameraService(status) {
  const running = !!status.running;
  const ready = !!status.ready;
  if (ready) {
    camSvcState.textContent = 'ready';
    camSvcState.className = 'badge badge-on';
  } else if (running) {
    camSvcState.textContent = 'starting…';
    camSvcState.className = 'badge badge-amber';
  } else {
    camSvcState.textContent = 'stopped';
    camSvcState.className = 'badge badge-off';
  }
  camSvcStart.disabled = running;
  camSvcStop.disabled = !running;

  const bits = [];
  if (status.pid) bits.push(`pid ${status.pid}`);
  if (status.returncode != null) bits.push(`exit ${status.returncode}`);
  camSvcInfo.textContent = bits.join(' · ');

  const logs = status.logs || [];
  camSvcLogs.textContent = logs.length
    ? logs.map((l) => `${l.t}  ${l.line}`).join('\n')
    : '(camera not started)';
  camSvcLogs.scrollTop = camSvcLogs.scrollHeight;
}

async function pollCameraService() {
  try {
    renderCameraService(await (await fetch('/api/camera/service')).json());
  } catch (_e) {}
}

async function cameraServiceAction(url) {
  [camSvcStart, camSvcStop].forEach((b) => (b.disabled = true));
  try {
    const data = await postJSON(url, {});
    if (data.error) {
      camSvcInfo.textContent = data.error;
      toast('Camera: ' + data.error, 'error');
    } else if (data.status) {
      renderCameraService(data.status);
    }
  } catch (err) {
    camSvcInfo.textContent = 'request failed: ' + err;
    toast('Camera request failed: ' + err, 'error');
  } finally {
    pollCameraService();
  }
}

camSvcStart.addEventListener('click', () => cameraServiceAction('/api/camera/service/start'));
camSvcStop.addEventListener('click', () => cameraServiceAction('/api/camera/service/stop'));

document.getElementById('cam-recalibrate').addEventListener('click', async function () {
  this.disabled = true;
  try {
    const data = await fetch('/api/camera/recalibrate', { method: 'POST' }).then(r => r.json());
    toast(data.message || (data.ok ? 'Recalibration started' : (data.error || 'failed')),
          data.ok ? 'info' : 'error');
  } catch (err) {
    toast('Recalibrate request failed: ' + err, 'error');
  } finally {
    this.disabled = false;
  }
});

// ---- health (polled) ----
async function pollHealth() {
  try {
    const res = await fetch('/api/health');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    const robot = data.robot || { level: 'red', probes: {}, pendant: {} };
    robotLed.className = 'led led-' + robot.level;
    // 'green' already means controllers up AND (not GUI-supervised OR pendant
    // connected) — see bridge_node.py::_robot_status(). Reuse it to gate the
    // stack Start button instead of re-deriving driver/pendant state here.
    robotReady = robot.level === 'green';
    updateStackStartGate();
    setDot(probeTraj, robot.probes.trajectory_server);
    setDot(probeJoints, robot.probes.joint_states);
    setDot(probeIo, robot.probes.gripper_io);
    // Pendant is only meaningful for a GUI-supervised real driver; show grey
    // (red dot) until it connects, green once the reverse interface is up.
    const pendant = robot.pendant || {};
    setDot(probePendant, pendant.connected);

    // Calibration LED: amber until the first status message arrives (camera
    // node not up yet / never calibrated), green once locked, red if a
    // recalibration reset it back to unlocked.
    const calib = data.calibration || { calibrated: false, seen: false };
    calibLed.className = 'led led-' + (!calib.seen ? 'amber' : (calib.calibrated ? 'green' : 'red'));

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
    robotLed.className = 'led led-red';
  }
}

// ---- chat ----
const chatForm = document.getElementById('chat-form');
const chatInput = document.getElementById('chat-input');
const chatSend = document.getElementById('chat-send');
const chatHistory = document.getElementById('chat-history');
const chatClear = document.getElementById('chat-clear');
const chatEstop = document.getElementById('chat-estop');

chatClear.addEventListener('click', () => {
  chatHistory.innerHTML = '';
});

chatEstop.addEventListener('click', async () => {
  chatEstop.disabled = true;
  try {
    const data = await postJSON('/api/execute/cancel', {});
    if (data.error || data.ok === false) {
      toast('Emergency stop: ' + (data.error || data.message || 'failed'), 'error');
    } else {
      toast(data.message || 'Robot stopped and returned home', 'info');
      setPlanState('cancelled', 'badge-off');
    }
  } catch (err) {
    toast('Emergency stop request failed: ' + err, 'error');
  }
  // Left enabled=false here — sendCommand() re-enables it only while a new
  // plan/execute cycle is actually in flight.
});

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

// Render the polished, animated plan into the dedicated Plan panel (request #5
// keeps the chat to requests + reports). Returns a map of step-index -> <li> so
// the live execution log can flip each step's status as it completes.
function renderPlanPanel(plan) {
  planContent.innerHTML = '';
  const stepEls = {};
  if (plan.task_summary) {
    planContent.appendChild(el('div', 'plan-summary', plan.task_summary));
  }
  const list = el('ol', 'plan-steps');
  (plan.plan || []).forEach((step) => {
    const li = renderStep(step);
    list.appendChild(li);
    stepEls[String(step.step ?? '?')] = li;
  });
  planContent.appendChild(list);

  const details = el('details', 'raw-plan');
  details.appendChild(el('summary', null, 'raw JSON'));
  details.appendChild(el('pre', null, JSON.stringify(plan, null, 2)));
  planContent.appendChild(details);

  return stepEls;
}

function setPlanState(label, kind) {
  planState.textContent = label;
  planState.className = 'badge ' + (kind || 'badge-off');
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
  // The chat holds only the request + the execution report (request #5); the
  // animated plan lives in its own panel.
  const block = el('div', 'msg pending');
  block.appendChild(el('div', 'cmd', command));
  const status = el('div', 'label', 'planning…');
  block.appendChild(status);
  chatHistory.appendChild(block);
  scrollIntoView(block);
  setPlanState('planning…', 'badge-amber');

  // 1. Plan.
  let planData;
  try {
    planData = await postJSON('/api/plan', { command });
  } catch (err) {
    block.className = 'msg failed';
    block.appendChild(el('div', 'error', 'Request failed: ' + err));
    setPlanState('idle', 'badge-off');
    return;
  }
  if (planData.error) {
    block.className = 'msg failed';
    status.remove();
    block.appendChild(el('div', 'error', 'Planning failed: ' + planData.error));
    toast('Planning failed: ' + planData.error, 'error');
    setPlanState('planning failed', 'badge-off');
    scrollIntoView(block);
    return;
  }

  // 2. Show the polished plan in the Plan panel, before execution starts.
  const stepEls = renderPlanPanel(planData.plan);
  status.textContent = 'executing…';
  setPlanState('executing…', 'badge-amber');
  scrollIntoView(block);

  // 3. Stream /execution_log over a WebSocket while the robot runs.
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/execution`);
  ws.onmessage = (ev) => {
    try {
      applyLogLine(stepEls, JSON.parse(ev.data).log);
    } catch (_e) { /* ignore malformed frame */ }
  };
  await new Promise((resolve) => {
    ws.onopen = resolve;
    ws.onerror = resolve; // proceed even if the log stream is unavailable
  });

  // 4. Execute. Enabled only for this window, since /cancel_execution is
  // meaningless before a skill is in flight and the plan is done by the time
  // execute() resolves either way.
  chatEstop.disabled = false;
  let execData;
  try {
    execData = await postJSON('/api/execute', { plan_json: planData.plan_json });
  } catch (err) {
    block.className = 'msg failed';
    status.remove();
    block.appendChild(el('div', 'error', 'Request failed: ' + err));
    setPlanState('error', 'badge-off');
    ws.close();
    return;
  } finally {
    ws.close();
    chatEstop.disabled = true;
  }

  status.remove();
  if (execData.error) {
    block.className = 'msg failed';
    // Mark the first not-yet-done step as failed for a clear visual cue.
    const pending = planContent.querySelector('.plan-step.pending');
    setStepState(pending, 'failed');
    block.appendChild(el('div', 'error', 'Execution failed: ' + execData.error));
    toast('Execution failed: ' + execData.error, 'error');
    setPlanState('failed', 'badge-off');
  } else {
    block.className = 'msg';
    Object.values(stepEls).forEach((li) => setStepState(li, 'done'));
    block.appendChild(el('div', 'label', 'execution report'));
    block.appendChild(el('pre', 'report', execData.report || '(no report)'));
    setPlanState('done', 'badge-on');
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
pollCameraService();
pollCamera();
checkPreflight();
setInterval(pollHealth, 2000);
setInterval(pollStack, 2000);
setInterval(pollDriver, 2000);
setInterval(pollCameraService, 2000);
setInterval(pollCamera, 1000);
setInterval(checkPreflight, 2000);
