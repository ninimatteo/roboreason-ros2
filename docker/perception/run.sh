#!/bin/bash
# GPU perception container (ROBOAI-60): SAM 2 and GroundingDINO behind the
# perception/segment ROS service, with torch kept out of the ROS container.
#
#   docker/perception/run.sh --setup   once: install the python packages (~8 GB)
#   docker/perception/run.sh           start (or restart) the service
#
# No image of its own: it runs on the ROS image (roboreason:ur, ROS Humble and
# python 3.10) and keeps everything heavy under PERCEPTION_HOME, on /home:
#   pylib/   torch, SAM 2, transformers (pip --target, first on PYTHONPATH)
#   ws/      the colcon build of the three packages it needs
#   models/  sam2/sam2.1_hiera_base_plus.pt and hf/ (grounding-dino-tiny)
#
# Environment, matching the ROS stack it serves:
#   ROS_DOMAIN_ID       (default 42, the ROS container's)
#   (ROS_LOCALHOST_ONLY is not supported: with the UDP profile the service
#    advertises LAN locators a localhost-only client cannot reach.)
#   ROBOREASON_PROFILE  real|sim (default real): sim serves /sim/perception/segment
#   PERCEPTION_HOME     (default ~/docker_ws/perception)
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HERE/../.." && pwd)
IMAGE=${PERCEPTION_IMAGE:-roboreason:ur}
HOME_DIR=${PERCEPTION_HOME:-$HOME/docker_ws/perception}
NAME=roboreason_perception${ROBOREASON_PROFILE:+_$ROBOREASON_PROFILE}
mkdir -p "$HOME_DIR/pylib" "$HOME_DIR/ws" "$HOME_DIR/tmp"
MOUNTS=(-v "$HOME_DIR":/perception -v "$REPO/src":/src:ro -v "$HERE":/conf:ro)

if [ "$1" = "--setup" ]; then
  # TMPDIR on /home too: pip unpacks the CUDA wheels there, several GB
  docker run --rm --user "$(id -u):$(id -g)" -e TMPDIR=/perception/tmp -e HOME=/perception/tmp \
    "${MOUNTS[@]}" $IMAGE bash -c \
    "python3 -m pip install --no-cache-dir --target /perception/pylib -r /conf/requirements.txt"
  exit
fi
docker rm -f $NAME >/dev/null 2>&1 || true
docker run -d --name $NAME --gpus all --network host \
  -e ROS_DOMAIN_ID=${ROS_DOMAIN_ID:-42} \
  -e ROS_LOCALHOST_ONLY=0 \
  -e ROBOREASON_PROFILE=${ROBOREASON_PROFILE:-real} \
  -e FASTRTPS_DEFAULT_PROFILES_FILE=/conf/fastdds_udp.xml \
  -e SAM2_BUILD_CUDA=0 -e HF_HOME=/perception/models/hf -e HF_HUB_OFFLINE=1 \
  -e PYTHONUNBUFFERED=1 -e TMPDIR=/perception/tmp \
  "${MOUNTS[@]}" --entrypoint /conf/entrypoint.sh $IMAGE
echo "started $NAME; logs: docker logs -f $NAME"
