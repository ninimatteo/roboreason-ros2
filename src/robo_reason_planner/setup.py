from setuptools import setup, find_packages

package_name = 'robo_reason_planner'

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
    description='LLM planner node exposing /plan_task service.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'llm_planner_node = robo_reason_planner.llm_planner_node:main',
            'vlm_planner_node = robo_reason_planner.vlm_planner_node:main',
            'vlm_llm_planner_node = robo_reason_planner.vlm_llm_planner_node:main',
        ],
    },
)
