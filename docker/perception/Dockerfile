# GPU perception container (ROBOAI-60): SAM 2 and GroundingDINO behind the
# perception/segment ROS service. Torch stays out of the ROS container.
# Build and run with docker/perception/run.sh.
FROM ros:humble-ros-base

RUN apt-get update && apt-get install -y --no-install-recommends python3-pip git \
    && rm -rf /var/lib/apt/lists/*

# CUDA 12.6 wheels bundle their own CUDA libraries: the host needs only the
# NVIDIA driver and the container toolkit.
RUN pip3 install --no-cache-dir torch==2.8.0 torchvision==0.23.0 \
        --index-url https://download.pytorch.org/whl/cu126

# numpy stays below 2: ROS Humble's message bindings are built against 1.x.
# SAM2_BUILD_CUDA=0 skips the optional CUDA extension (only post processing).
# The SAM 2 commit is the one the ROBOAI-56 spike ran.
RUN SAM2_BUILD_CUDA=0 pip3 install --no-cache-dir "numpy>=1.24,<2" \
        "git+https://github.com/facebookresearch/sam2.git@2b90b9f5ceec907a1c18123530e92e794ad901a4" \
        "transformers>=4.51,<6" pydantic-settings pyyaml pillow

COPY fastdds_udp.xml /etc/roboreason/fastdds_udp.xml
COPY entrypoint.sh /entrypoint.sh
ENV FASTRTPS_DEFAULT_PROFILES_FILE=/etc/roboreason/fastdds_udp.xml \
    HF_HOME=/models/hf \
    HF_HUB_OFFLINE=1 \
    PYTHONUNBUFFERED=1
ENTRYPOINT ["/entrypoint.sh"]
