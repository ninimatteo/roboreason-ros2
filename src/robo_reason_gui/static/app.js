const led = document.getElementById('ros-led');
const text = document.getElementById('ros-text');
const bridgeNode = document.getElementById('bridge-node');
const nodeCount = document.getElementById('node-count');
const nodeList = document.getElementById('node-list');

function setLed(state) {
  led.className = 'led led-' + state;
}

async function pollHealth() {
  try {
    const res = await fetch('/api/health');
    if (!res.ok) throw new Error('HTTP ' + res.status);
    const data = await res.json();

    setLed(data.ros_ok ? 'green' : 'amber');
    text.textContent = data.ros_ok ? 'ROS connected' : 'ROS degraded';
    bridgeNode.textContent = data.bridge_node || '—';
    nodeCount.textContent = data.node_count;

    nodeList.innerHTML = '';
    (data.discovered_nodes || []).forEach((n) => {
      const li = document.createElement('li');
      li.textContent = n;
      nodeList.appendChild(li);
    });
  } catch (err) {
    setLed('red');
    text.textContent = 'backend unreachable';
  }
}

pollHealth();
setInterval(pollHealth, 2000);
