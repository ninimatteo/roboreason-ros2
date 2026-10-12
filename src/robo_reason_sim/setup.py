import os
from glob import glob

from setuptools import find_packages, setup

package_name = 'robo_reason_sim'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        (os.path.join('share', package_name, 'config'), glob('config/*.yaml')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Matteo Nini',
    maintainer_email='matteo.nini@example.com',
    description='Gazebo virtual test environment for RoboReason.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'gripper_shim_node = robo_reason_sim.gripper_shim_node:main',
        ],
    },
)
