cmake_minimum_required(VERSION 3.8)
project(robo_reason_interfaces)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)
find_package(action_msgs REQUIRED)
find_package(sensor_msgs REQUIRED)
find_package(geometry_msgs REQUIRED)
find_package(std_msgs REQUIRED)

rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/PixelArray.msg"
  "srv/PlanTask.srv"
  "srv/ExecutePlan.srv"
  "srv/CancelExecution.srv"
  "srv/GetImage.srv"
  "srv/Deproject.srv"
  "action/ExecuteSkill.action"
  DEPENDENCIES action_msgs sensor_msgs geometry_msgs
)

ament_export_dependencies(rosidl_default_runtime)
ament_package()
