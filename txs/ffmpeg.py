import subprocess
import json
import os
import re
import pprint
from . import utils

def _run(*args, stdout_callback=None, stderr_callback=None, **kwargs):
    kwargs.update(stdout=subprocess.PIPE,
                  stderr=subprocess.PIPE,
                  bufsize=64,
                  encoding='utf-8')
    try:
        proc = subprocess.Popen(args, **kwargs)
    except OSError as e:
        utils.croak(f'{args[0]}: {os.strerror(e.errno)}')
    stderr = []
    while proc.returncode is None:
        proc.poll()
        while True:
            stderr_line = proc.stderr.readline().rstrip('\n')
            if stderr_line:
                stderr.append(stderr_line)
                if stderr_callback is not None:
                    stderr_callback(stderr_line)

            stdout_line = ''
            if stdout_callback is not None:
                stdout_line = proc.stdout.readline().rstrip('\n')
                if stdout_line:
                    stdout_callback(stdout_line)

            if not stderr_line and not stdout_line:
                break
    if proc.returncode:
        utils.error(f'Command failed: {utils.cmd2str(proc.args)}')
        for line in stderr:
            utils.error(f'{proc.args[0]}: {line}')
        utils.croak()
    else:
        return proc

def _as_json(string):
    try:
        return json.loads(string)
    except ValueError as e:
        utils.error('Unable to parse JSON:')
        utils.error(pprint.pformat(string))
        utils.croak(e)

def _get_video_info(filepath):
    proc = _run('ffprobe', '-hide_banner',
                '-show_format', '-show_streams',
                '-of', 'json', filepath)
    return _as_json(proc.stdout.read())

def duration(filepath):
    info = _get_video_info(filepath)
    return float(info['format']['duration'])

def encode(source, dest, settings, start, stop):
    logfile = utils.logfile(dest)
    x264opts = utils.settings2str(settings, escape=True)
    cmd = ['ffmpeg', '-hide_banner', '-nostdin', '-report',
           '-y', '-ss', start, '-i', f'file:{source}', '-to', stop,
           '-c:v', 'libx264', '-x264opts', x264opts,
           '-c:a', 'copy',
           f'file:{dest}']
    env = os.environ.copy()
    env['FFREPORT'] = 'file=%s:level=40' % (logfile.replace(':', '\\:'),)

    # Example ffmpeg output:
    # frame=   49 fps= 12 q=24.0 size=     482kB time=00:00:02.08 bitrate=1895.5kbits/s speed=0.527x
    regex = re.compile(r'fps\s*=\s*([\d.]+).*?time\s*=\s*([\d:\.]+).*?speed=([\d\.]+)')
    status_length = 39
    def handle_stderr(line):
        match = regex.search(line)
        if match:
            fps, time, speed = match.group(1, 2, 3)
            print(f'fps={float(fps):5.1f} time={time} speed={float(speed):1.3f}x', end='', flush=True)
            print('\b'*status_length, end='')
    _run(*cmd, env=env, stderr_callback=handle_stderr)

def bframes(logfile):
    values = []
    regex = re.compile(r'consecutive B-frames:\s*((?:\d+\.\d+\s*%\s*)+)')
    with open(logfile, 'r') as f:
        for line in f.readlines():
            match = regex.search(line)
            if match:
                for perc in re.split(r'\s+', match.group(1)):
                    if perc:
                        values.append(float(perc[:-1]))
    if not values:
        utils.croak(f'Unable to find consecutive B-frames in {logfile}')
    return values
