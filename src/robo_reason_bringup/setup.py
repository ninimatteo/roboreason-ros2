from setuptools import setup, find_packages
import os
from glob import glob

package_name = 'robo_reason_bringup'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        (os.path.join('share', package_name, 'launch'), glob('launch/*.py')),
        # Python package (config.py) is picked up automatically by find_packages()
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Matteo Nini',
    maintainer_email='matteo.nini@example.com',
    description='Launch files for the full RoboReason system.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={'console_scripts': []},
)
