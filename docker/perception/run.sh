#!/bin/bash
# Build the perception image (first time) and start the container (ROBOAI-60).
#
#   docker/perception/run.sh [--build]
#
# Environment, matching the ROS stack it serves:
#   ROS_DOMAIN_ID       (default 42, the ROS container's)
#   ROS_LOCALHOST_ONLY  (default 0)
#   ROBOREASON_PROFILE  real|sim (default real): sim serves /sim/perception/segment
#   PERCEPTION_MODELS   host folder with sam2/sam2.1_hiera_base_plus.pt and the
#                       hf/ cache of IDEA-Research/grounding-dino-tiny
#                       (default ~/docker_ws/perception_models)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
IMAGE=roboreason:perception
NAME=roboreason_perception${ROBOREASON_PROFILE:+_$ROBOREASON_PROFILE}
MODELS=${PERCEPTION_MODELS:-$HOME/docker_ws/perception_models}

if [ "$1" = "--build" ] || ! docker image inspect $IMAGE >/dev/null 2>&1; then
  docker build -t $IMAGE "$HERE"
fi
docker rm -f $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME --gpus all --network host \
  -e ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-42} \
  -e ROS_LOCALHOST_ONLY=${ROS_LOCALHOST_ONLY:-0} \
  -e ROBOREASON_PROFILE=${ROBOREASON_PROFILE:-real} \
  -v "$REPO/src":/src:ro \
  -v "$MODELS":/models:ro \
  $IMAGE
echo "started $NAME; logs: docker logs -f $NAME"
