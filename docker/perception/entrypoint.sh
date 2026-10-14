#!/bin/bash
# Inside the perception container: build the interfaces and the service from the
# mounted repo (bringup is imported from source: building it would pull in the
# whole planning stack it depends on), then run it with the packages in /perception/pylib (ROBOAI-60).
set -e
source /opt/ros/humble/setup.bash
cd /perception/ws
colcon build --base-paths /src \
    --packages-select robo_reason_interfaces robo_reason_perception \
    --event-handlers console_direct- > build.log 2>&1 || { tail -30 build.log; exit 1; }
source install/setup.bash
# only now: pylib's setuptools is too new for colcon, its numpy must win at runtime
export PYTHONPATH=/perception/pylib:/src/robo_reason_bringup:$PYTHONPATH
exec python3 install/robo_reason_perception/lib/robo_reason_perception/segment_node "$@"
