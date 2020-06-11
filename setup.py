from setuptools import setup, find_packages
import re

def get_var(name):
    with open('txs/__init__.py') as f:
        content = f.read()
        match = re.search(rf'''^{name}\s*=\s*['"]([^'"]*)['"]''',
                          content, re.MULTILINE)
        if match:
            return match.group(1)
        else:
            raise RuntimeError('Unable to find __{name}__')

setup(
    name='txs',
    version=get_var('__version__'),
    author=get_var('__author__'),
    author_email=get_var('__author_email__'),
    description='Wrapper around ffmpeg and mpv that generates and compares x264 test encodes',
    long_description=open('README.md').read(),
    long_description_content_type='text/markdown',
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
        'Programming Language :: Python :: 3',
        'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)',
    ],
    python_requires='>=3.6',
)
