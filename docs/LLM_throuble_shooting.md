## Build and source the project

### Venv, build, source the overlay

1. Virtual Env - Use a python managed venv (not conda because substitutes its C++ to machine C++ which ros2 refers thus makes a mess when building the packages - not recognizes some python packages and stuff like that). 
Thus - Create a new python virtual environment:

```
python3 -m venv <module_name>
source <module_name>/bin/activate
pip install -r requirements.txt
```

2. Build packages - Use colcon build with specific arguments to avoid using the python basic installer but the activate venv instead:

```
touch venv/COLCON_IGNORE    # Avoid colcon building venv folder, thus throwing errors

python3 $(which colcon) build --symlink-install --cmake-args -DPython3_EXECUTABLE=$(which python3)  # specific command to use the venv 

source install/setup.bash
```

3. To ``unbuild'' some previous build

```
rm -rf /build /log /install
```


## Validating LLM planning 

1. Dry run with real LLM client in the loop  -

On one terminal - Export API-keys, otherwise does not  work, then launch the services and nodes

```
export GROQ_API_KEY="<paste_key_here>"
echo $GROQ_API_KEY
```

```
ros2 launch robo_reason_bringup dry_run_services.launch.py use_mock_llm:=false reasoning_method:=fhp
```

On another teminal -  Spin interface node 

```
ros2 run robo_reason_task_interface task_interface_node
```









