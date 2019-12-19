from setuptools import setup, find_packages

setup(
    name='txs',
    version='0.0.1',
    description='Create x264 test encodes and compare them',
    long_description=open('README.md').read(),
    # long_description_content_type="text/markdown",
    url='http://github.com/plotski/txs',
    license='GPLv3',
    packages=find_packages(),
    install_requires=[],
    data_files=[('share/txs/lua', ['txs-compare.lua'])],
    entry_points='''
        [console_scripts]
        txs=txs.main:run
    ''',
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
    ],
    python_requires='>=3.6',
)
