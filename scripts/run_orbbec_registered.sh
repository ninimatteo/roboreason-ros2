#!/usr/bin/env bash
set -euo pipefail

export PYTHONNOUSERSITE=1

if [[ ! -f /opt/ros/humble/setup.bash ]]; then
  echo "ERROR: /opt/ros/humble/setup.bash not found." >&2
  exit 1
fi

set +u
source /opt/ros/humble/setup.bash
set -u

ros2 launch orbbec_camera gemini_330_series.launch.py depth_registration:=true
