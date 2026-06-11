#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONNOUSERSITE=1

if [[ ! -f /opt/ros/humble/setup.bash ]]; then
  echo "ERROR: /opt/ros/humble/setup.bash not found." >&2
  exit 1
fi

set +u
source /opt/ros/humble/setup.bash
set -u

cd "${PROJECT_ROOT}/ros2_ws"
colcon build --symlink-install
