# robo_reason_gui

A web control panel for the RoboReason stack. It runs a FastAPI server with an
embedded `rclpy` bridge node and serves a plain HTML/JS frontend, so the whole
system can be driven from a browser: send natural-language commands, watch the
plan execute step-by-step, retune the planner live, and start/stop both the
ROS2 stack and the UR driver.

## Architecture

```
browser  <--HTTP/WebSocket-->  FastAPI (server_node.py)
                                 |-- GuiBridgeNode (rclpy)      # plan/execute, probes, live params
                                 |-- StackSupervisor            # owns `ros2 launch gui_stack...`
                                 \-- UrDriverSupervisor         # owns the flaky UR driver, auto-retry
```

- **GuiBridgeNode** (`bridge_node.py`) — bridges the GUI to ROS: `/plan_task`
  and `/execute_plan` service calls, robot-connectivity probes, `/execution_log`
  fan-out over a WebSocket, and live planner retuning via `SetParameters`.
- **StackSupervisor** (`stack_supervisor.py`) — launches the RoboReason stack as
  a child `ros2 launch robo_reason_bringup gui_stack.launch.py` process so the
  operator can start/stop/restart it and mix real/mock robot, camera and LLM.
- **UrDriverSupervisor** (`ur_driver_supervisor.py`) — owns the UR driver
  process, retrying startup with backoff until the robot is reachable through
  the bridge probes, and auto-reconnecting on unexpected exits.

## Build

`server_node.py`, `app.py` etc. are plain Python modules picked up by an
incremental build. **New files and changed static assets need a clean rebuild**
because `setup.py` collects them with `glob()`:

```bash
cd ~/ws
rm -rf build/robo_reason_gui install/robo_reason_gui
colcon build --symlink-install --packages-select robo_reason_gui
source install/setup.bash
```

With `--symlink-install` the served static files are symlinks into the build
tree; the app serves them through an explicit `/static/{path}` route (not
Starlette's `StaticFiles`, which refuses to follow those symlinks).

## Run

```bash
ros2 run robo_reason_gui gui_node       # or: ros2 launch robo_reason_gui gui.launch.py
```

The server binds `0.0.0.0:8080`. The container uses `--network host`, so open
**http://localhost:8080** in the host browser.

## Using the panel

- **Command** — type a natural-language instruction; the polished plan renders
  immediately and each step flips to done/failed live as `/execution_log`
  streams in. *Clear* empties the chat history.
- **Robot connection** — three probes drive the header LED: trajectory
  controller readiness, `/joint_states` freshness, gripper I/O service.
- **Configuration** — mode, reasoning method, provider, model, temperature and
  *Mock LLM*. *Apply to running planner* pushes these onto the live planner via
  ROS parameters (no relaunch). `model_name` is sent as `provider/model`.
- **UR driver** — start/reconnect/stop the real UR driver with robot/reverse IP;
  shows the state machine (stopped/connecting/connected/failed), attempt history
  and log tail. A header *Reconnect* button mirrors the reconnect action.
- **Stack** — start/restart/stop the ROS2 stack with independent mock toggles
  for robot and camera (camera is VLM-only).

Action outcomes surface as toast notifications in the top-right.

## HTTP API

| Method | Path | Purpose |
| ------ | ---- | ------- |
| GET  | `/api/health` | ROS + robot connectivity snapshot |
| GET  | `/api/options` | selector options from the model registry |
| POST | `/api/plan` | plan a command (no execution) |
| POST | `/api/execute` | execute a previously-planned plan |
| POST | `/api/config` | live-retune the running planner |
| GET/POST | `/api/stack[/start\|/stop\|/restart]` | stack supervisor |
| GET/POST | `/api/driver[/start\|/stop\|/reconnect]` | UR driver supervisor |
| WS   | `/ws/execution` | live `/execution_log` stream |
