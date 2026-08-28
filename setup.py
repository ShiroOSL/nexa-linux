from setuptools import setup, find_packages

setup(
    name='nexa-assistant',
    version='0.1.0',
    packages=find_packages(where='src'),
    package_dir={'': 'src'},
    include_package_data=True,
    install_requires=[
        'python-dbus-next',
        'PyGObject',
        'GStreamer',
        'soundfile',
        'numpy'
    ],
    entry_points={
        'console_scripts': [
            'nexa-assistant=main:main',
        ],
    },
)
