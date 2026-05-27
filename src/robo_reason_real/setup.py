from setuptools import setup, find_packages
import os
from glob import glob

package_name = 'robo_reason_real'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'config'), glob('config/*.json')),
        (os.path.join('share', package_name, 'prompts'), glob('prompts/*.txt')),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Matteo Nini',
    maintainer_email='matteo.nini@example.com',
    description='RoboReason ROS2 real robot integration',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'task_interface_node = robo_reason_real.task_interface_node:main',
            'llm_planner_node = robo_reason_real.llm_planner_node:main',
            'plan_manager_node = robo_reason_real.plan_manager_node:main',
            'fake_skill_executor_node = robo_reason_real.fake_skill_executor_node:main',
            'ur5_skill_executor_node = robo_reason_real.ur5_skill_executor_node:main',
        ],
    },
)
