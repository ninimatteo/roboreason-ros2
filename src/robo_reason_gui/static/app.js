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
const optionsError = document.getElementById('options-error');

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
  } catch (err) {
    optionsError.hidden = false;
    optionsError.textContent = 'Failed to fetch /api/options: ' + err;
  }
}

// Keep the model list consistent with the chosen provider (works even while
// disabled, ready for Phase 2 when the selectors become interactive).
selProvider.addEventListener('change', () => {
  fillSelect(selModel, providerModels[selProvider.value] || []);
});

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
setInterval(pollHealth, 2000);
