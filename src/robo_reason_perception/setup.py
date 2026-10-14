from setuptools import find_packages, setup

package_name = 'robo_reason_perception'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', [f'resource/{package_name}']),
        (f'share/{package_name}', ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='matteo nini',
    maintainer_email='m.nini@unimore.it',
    description='SAM 2 and GroundingDINO segmentation as a ROS service (ROBOAI-60).',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'segment_node = robo_reason_perception.segment_node:main',
        ],
    },
)
