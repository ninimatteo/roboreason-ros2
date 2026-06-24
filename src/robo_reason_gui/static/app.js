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

loadOptions();
pollHealth();
setInterval(pollHealth, 2000);
