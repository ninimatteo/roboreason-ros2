from setuptools import setup, find_packages

package_name = 'robo_reason_manager'

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
    description='Plan manager node: validates and executes plans step-by-step.',
    license='MIT',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
            'plan_manager_node = robo_reason_manager.plan_manager_node:main',
        ],
    },
)
