import subprocess
import json
import os
import re
import pprint
from . import utils

if os.name == 'posix':
    FFPROBE = 'ffprobe'
    FFMPEG = 'ffmpeg'
elif os.name == 'nt':
    FFPROBE = 'ffprobe.exe'
    FFMPEG = 'ffmpeg.exe'
else:
    raise RuntimeError('Unsupported os: {os.name!r}')

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

def _get_source(path):
    if os.path.isfile(path):
        return f'file:{path}'
    elif os.path.isdir(path):
        if os.path.exists(os.path.join(path, 'BDMV')):
            return f'bluray:{path}'
    return path

def _get_video_info(filepath):
    proc = _run(FFPROBE, '-hide_banner',
                '-show_format', '-show_streams',
                '-of', 'json', _get_source(filepath))
    return _as_json(proc.stdout.read())

def duration(filepath):
    info = _get_video_info(filepath)
    return float(info['format']['duration'])

def encode(source, dest, settings=None, start=None, stop=None, topic=None, create_logfile=True):
    env = os.environ.copy()
    cmd = [FFMPEG, '-hide_banner', '-nostdin', '-sn', '-y']
    if create_logfile:
        cmd.extend(('-report',))
        env['FFREPORT'] = 'file=%s:level=40' % (utils.logfile(dest).replace(':', '\\:'),)
    if start is not None:
        cmd.extend(('-ss', start))
    cmd.extend(('-i', _get_source(source)))
    if stop is not None:
        cmd.extend(('-t', stop))
    if settings is not None:
        cmd.extend(('-c:v', 'libx264',
                    '-x264opts', utils.settings2str(settings, escape=True)))
    else:
        cmd.extend(('-c:v', 'copy'))
    cmd.extend(('-c:a', 'copy'))
    cmd.extend((
        # Encoding excerpts often results in "Too many packets buffered for
        # output stream" errors and increasing the muxing queue prevents
        # them.
        '-max_muxing_queue_size', '1024',
        f'file:{dest}'))

    # Example ffmpeg output:
    # frame=   49 fps= 12 q=24.0 size=     482kB time=00:00:02.08 bitrate=1895.5kbits/s speed=0.527x
    regex = re.compile(r'fps\s*=\s*([\d.]+).*?time\s*=\s*([\d:\.]+).*?speed=([\d\.]+)')
    def handle_stderr(line):
        match = regex.search(line)
        if match:
            fps, time, speed = match.group(1, 2, 3)
            parts = (f'fps={float(fps):.1f}'.ljust(10),
                     f'time={time}'.ljust(16),
                     f'speed={float(speed):.3f}x'.ljust(13))
            status = ' '.join(parts)
            print(' '.join(parts), end='', flush=True)
            print('\b'*len(status), end='')

    if topic is not None:
        print(f'{topic}: ', end='')
    _run(*cmd, env=env, stderr_callback=handle_stderr)
    print()

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
