from setuptools import find_packages, setup

package_name = "vlm_camera_service"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (f"share/{package_name}", ["package.xml"]),
        (
            f"share/{package_name}/launch",
            ["launch/camera_services.launch.py"],
        ),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="nemo",
    maintainer_email="nemo@example.com",
    description="Camera services for VLM planner integration.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "mock_camera_services_node = vlm_camera_service_mock.mock_camera_services_node:main",
            "pixel_overlay_viewer_node = vlm_camera_service_mock.pixel_overlay_viewer_node:main",
        ],
    },
)
