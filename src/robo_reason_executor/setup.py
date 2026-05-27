from setuptools import setup, find_packages

package_name = 'robo_reason_executor'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='Matteo Nini',
    maintainer_email='matteo.nini@example.com',
    description='Skill executor nodes: fake (dry-run) and real UR5cb.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'fake_skill_executor_node = robo_reason_executor.fake_skill_executor_node:main',
            'ur5_skill_executor_node = robo_reason_executor.ur5_skill_executor_node:main',
        ],
    },
)
