#!/usr/bin/env bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONNOUSERSITE=1

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
else
  echo "ERROR: /opt/ros/humble/setup.bash not found." >&2
  return 1 2>/dev/null || exit 1
fi

if [[ -f "${PROJECT_ROOT}/ros2_ws/install/setup.bash" ]]; then
  set +u
  source "${PROJECT_ROOT}/ros2_ws/install/setup.bash"
  set -u
else
  echo "Note: workspace not built yet, skipping ros2_ws/install/setup.bash"
fi

echo "ROS env ready for ${PROJECT_ROOT}"
