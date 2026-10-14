#!/bin/bash
# Build the three packages the service needs from the mounted repo, then run it.
set -e
source /opt/ros/humble/setup.bash
mkdir -p /ws && cd /ws
colcon build --base-paths /src \
    --packages-select robo_reason_interfaces robo_reason_bringup robo_reason_perception \
    --event-handlers console_direct- > /ws/build.log 2>&1 || { tail -30 /ws/build.log; exit 1; }
source /ws/install/setup.bash
exec ros2 run robo_reason_perception segment_node "$@"
