# Docker Setup Guide

This guide explains how to decompress, load, and run the RoboReason Docker image shared as a `.tar.gz` file.

---

## 1. Decompress the image

Once you have received the `.tar.gz` file, decompress it:

```bash
gunzip roboreason_ros2.tar.gz
```

This will produce a `roboreason_ros2.tar` file in the same directory.

---

## 2. Load the image into Docker

```bash
docker load -i roboreason_ros2.tar
```

Verify the image was loaded correctly:

```bash
docker images
```

You should see `roboreason-ros2` (or similar) in the list.

---

## 3. Set up shell aliases

Add the following two aliases to your `~/.bashrc` so you can start and attach to the container easily:

```bash
alias roboreason-ur="docker run -it --rm --name roboreason_ur_gui --network host --gpus all --privileged -v /dev:/dev -e DISPLAY=$DISPLAY -e QT_X11_NO_MITSHM=1 -v /tmp/.X11-unix:/tmp/.X11-unix:rw -v ~/docker_ws/ros2_humble:/root/ws -w /root/ws roboreason:ur"
alias roboreason-shell="docker exec -it roboreason_ur_gui bash --rcfile /root/ws/.bashrc_docker"
```

Open your `.bashrc`:

```bash
nano ~/.bashrc
```

Paste both aliases at the end of the file, then save and reload it:

```bash
source ~/.bashrc
```

---

## 4. Prepare the workspace directory

The container mounts `~/docker_ws/ros2_humble` as `/root/ws` inside the container.
Make sure that directory exists on your machine:

```bash
mkdir -p ~/docker_ws/ros2_humble
```

Clone or copy the RoboReason source code into it so it is available inside the container:

```bash
# Example — clone the repository
git clone <repository_url> ~/docker_ws/ros2_humble
```

---

## 5. Running the container

### Start the container

```bash
roboreason-ur
```

This starts the Docker container with:
- Full GPU access (`--gpus all`)
- Host networking (`--network host`) for ROS2 topic discovery
- Hardware access (`--privileged`, `-v /dev:/dev`) for robot and camera devices
- GUI support via X11 forwarding (`DISPLAY`, `QT_X11_NO_MITSHM`, `.X11-unix`)
- Your local workspace mounted at `/root/ws`

> **Note:** If you get an X11 permission error, run `xhost +local:docker` on the host before starting the container.

### Open an additional terminal inside the running container

While the container is running, open as many extra terminals as needed with:

```bash
roboreason-shell
```

Each call opens a new `bash` session inside the same container, sourcing the Docker-specific `.bashrc_docker` file for the correct ROS2 environment.

---

## 6. Typical workflow

```
Terminal 1                          Terminal 2 (or more)
──────────────────────────────      ──────────────────────────────
$ roboreason-ur                     $ roboreason-shell
# starts the container,             # attaches to the running
# drops you into a shell            # container in a new session
```

Inside the container you can build the workspace, launch nodes, run scripts, etc. All changes made inside `~/docker_ws/ros2_humble` on the host are immediately reflected at `/root/ws` inside the container, and vice versa.

---

## 7. The `roboreason` venv and numpy

`.bashrc_docker` activates the venv at `/root/ws/roboreason`. It is created with
`include-system-site-packages = true`, so it sees everything the image installs
from `requirements.txt`, including `roboticstoolbox`, `spatialmath` and
`cv_bridge`, which are compiled against **numpy 1.x**.

Never `pip install` numpy or OpenCV inside the venv: a copy there shadows the
image's, and `opencv-python` 4.12 or newer drags in numpy 2, which breaks every
import of `roboticstoolbox` with `numpy.dtype size changed, may indicate binary
incompatibility`. This happened in October 2026 and was fixed by removing both
from the venv:

```bash
/root/ws/roboreason/bin/pip uninstall -y numpy opencv-python
```

Python dependencies belong in `requirements.txt` (which pins `numpy<2`), not in
the venv.
