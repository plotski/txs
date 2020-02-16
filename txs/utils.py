import sys
import os
import site
import re
import itertools
from collections import abc, defaultdict
import shlex
import subprocess
import textwrap
import termios, tty
import contextlib

from . import utils
from . import __name__

def error(msg):
    print(msg, file=sys.stderr)

def croak(msg=None):
    if msg:
        error(msg)
    sys.exit(1)

@contextlib.contextmanager
def raw_mode_posix():
    attrs = termios.tcgetattr(sys.stdin.fileno())
    tty.setraw(sys.stdin.fileno())
    try:
        yield
    finally:
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSADRAIN, attrs)

def dialog_yesno(question):
    answer = ''
    try:
        if os.name == 'posix':
            print(f'{question} [y/n] ', end='', flush=True)
            with raw_mode_posix():
                while answer.lower() not in ('y', 'n'):
                    answer = sys.stdin.read(1)
            print(answer)
        else:
            while answer.lower() not in ('y', 'n'):
                answer = input(f'{question} [y/n] ')
    except KeyboardInterrupt:
        pass
    return answer.lower() == 'y'

def wrap(string, width=80):
    return textwrap.fill(string, width=width)

def indent(amount, string):
    return textwrap.indent(string, ' '*amount)

def cmd2str(cmd):
    return ' '.join(shlex.quote(arg) for arg in cmd)

def duration2str(seconds):
    hours = int(seconds / 3600)
    mins = int((seconds - (hours * 3600)) / 60)
    return f'{hours:02d}:{mins:02d}'

def bytes2str(bytes):
    for size,unit in ((2**30, 'Gi'), (2**20, 'Mi'), (2**10, 'Ki')):
        if bytes >= size:
            return f'{round(bytes / size, 2):5.2f} {unit}B'
    return f'{bytes} B'

def combine_dicts(*dcts):
    # {**a, **b} doesn't cut it because the order of the keys in later dicts
    # trumps the order of the same keys in earlier dicts.
    # >>> a = {'me': 'umh', 'bframes': 5}
    # >>> b = {'crf': 20, 'bframes': 5}
    # >>> {**a, **b}
    # {'me': 'umh', 'bframes': 5, 'crf': 20}
    # 'crf' and 'bframes' were flipped, bu we need to maintain key order of b or
    # the Lua script won't find the estimates.
    combined = {}
    for d in dcts:
        for k in d:
            combined.pop(k, None)
        combined.update(d)
    return combined

def logfile(filepath):
    return os.path.splitext(filepath)[0] + '.log'

def title(filepath):
    return os.path.splitext(os.path.basename(filepath))[0]

def mkdir(path):
    if not os.path.exists(path):
        try:
            os.mkdir(path)
        except OSError as e:
            croak(f'Unable to create {path}: {os.strerror(e.errno)}')
    if not os.path.isdir(path):
        croak(f'Not a directory: {path}')

def cleanup(*filepaths):
    for filepath in filepaths:
        for f in (filepath, logfile(filepath)):
            if os.path.exists(f):
                print(f'Deleting {f}')
                os.remove(f)


def parse_settings(*strings, default_value=None):
    # Split settings at unescaped ":"
    strings = [s.replace('\\:', ':') for string in strings
               for s in re.split(r'(?<!\\):', string)]
    # Convert list of "k=v" strings into dict
    settings = {}
    for string in strings:
        if '=' in string:
            k, v = string.split('=', maxsplit=1)
            settings[k.strip()] = v.strip()
        else:
            key = string.strip()
            if key:
                settings[key] = default_value
    return settings

def generate_sample_settings(*strings):
    # Parse each string as a list of settings
    combinations = []
    for string in strings:
        # Each setting can have multiple values separated by unescaped "/"
        settings = parse_settings(string)
        for k,v in settings.items():
            if v is not None:
                settings[k] = [v.replace('\\/', '/')
                               for v in re.split(r'(?<!\\)/', v)]
            else:
                # Flags (e.g. no-deblock) have no value
                settings[k] = [None]

        # Create all combinations of all settings
        # https://stackoverflow.com/a/5228294
        keys = settings.keys()
        value_lists = settings.values()
        for values in itertools.product(*value_lists):
            d = dict(zip(keys, values))
            # For each encountered flag (boolean setting), add another sample
            # with that flag removed.
            for k,v in tuple(d.items()):
                if v is None:
                    d2 = d.copy()
                    d2.pop(k)
                    combinations.append(d2)
            combinations.append(d)
    return combinations

def sample_keys(sample_settings):
    # Same thing as set().union(), but preserve order.
    keys = []
    for settings in sample_settings:
        for key in settings.keys():
            if key not in keys:
                keys.append(key)
    return keys

def settings2str(settings, delimiter=':', escape=False, replace_in_values={':':','}):
    # Values that use ":" as a separator (e.g. deblock) can also use ",", which
    # makes the whole string easier to parse.
    def apply_riv(value):
        for this,that in replace_in_values.items():
            value = str(value).replace(this, that)
        return value

    def normalize_value(value):
        if value is None:
            return None
        value = apply_riv(value)
        if escape:
            value = value.replace(delimiter, '\\'+delimiter)
            value = value.replace('=', '\\=')
        return value

    if isinstance(settings, abc.Sequence):
        # Group together settings with identical key sets and map each group to
        # a tuple of the shared keys.
        # Example: {('crf', 'bframes'): [{'crf': '22', 'bframes': '8'},
        #                                {'crf': '22', 'bframes': '16'},
        #                                {'crf': '21', 'bframes': '8'},
        #                                {'crf': '21', 'bframes': '16'}],
        #           ('b-adapt', 'bframes'): [{'b-adapt': '1', 'bframes': '3'},
        #                                    {'b-adapt': '1', 'bframes': '6'},
        #                                    {'b-adapt': '2', 'bframes': '3'},
        #                                    {'b-adapt': '2', 'bframes': '6'}]}
        groups = defaultdict(lambda: [])
        for settings in settings:
            groups[tuple(settings.keys())].append(settings)
        # For each group, map each key to a list of values.
        strings = []
        for keys,list_of_settings in groups.items():
            value_lists = defaultdict(lambda: [])
            for key in keys:
                value_list = value_lists[key]
                for settings in list_of_settings:
                    value = settings[key]
                    if value not in value_list:
                        value = normalize_value(value)
                        if value:
                            value_list.append(value)
            # Separate value with "/"
            parts = []
            for key,values in value_lists.items():
                if len(values) > 0:
                    parts.append(f'{key}=' + '/'.join(values))
                else:
                    # key is a flag/boolean
                    parts.append(key)
            # Separate settings with ":"
            strings.append(delimiter.join(parts))
        # Separate groups of settings with " "
        return ' '.join(strings)

    else:
        parts = []
        for k,v in settings.items():
            value = normalize_value(v)
            if value:
                parts.append(f'{k}={normalize_value(v)}')
            else:
                parts.append(f'{k}')
        return delimiter.join(parts)

def read_estimates(estimates_file):
    est = {}
    if os.path.exists(estimates_file):
        with open(estimates_file, 'r') as f:
            for line in f.readlines():
                parts = [part.strip() for part in line.split('/')]
                est[parts[0]] = {'settings'     : parts[0],
                                 'time_str'     : parts[1],
                                 'time'         : parts[2],
                                 'size_str'     : parts[3],
                                 'size'         : parts[4],
                                 'all_settings' : parts[5]}
    return est

def update_estimates(estimates_file, diff_settings, est_time, est_size, settings):
    est = read_estimates(estimates_file)
    diff_settings_str = utils.settings2str(diff_settings, escape=False)
    est[diff_settings_str] = {'settings'     : diff_settings_str,
                              'time_str'     : utils.duration2str(est_time),
                              'time'         : int(est_time),
                              'size_str'     : utils.bytes2str(est_size),
                              'size'         : int(est_size),
                              'all_settings' : utils.settings2str(settings, escape=True)}
    max_key_width = max(len(k) for k in est)
    with open(estimates_file, 'w') as f:
        for key,values in est.items():
            values = ' / '.join(str(v) for v in tuple(values.values())[1:])
            f.write(f'{key.ljust(max_key_width)} / {values}\n')

if os.name == 'posix':
    MPV = 'mpv'
elif os.name == 'nt':
    MPV = 'mpv.exe'
else:
    raise RuntimeError('Unsupported os: {os.name!r}')

def compare_samples(sample_dir, debug=None, playlist_size=None, font_size=None, estimates_file=None):
    script_path_user = os.path.join(site.USER_BASE, f'share/{__name__}/lua/{__name__}-compare.lua')
    script_path_system = os.path.join(sys.prefix, f'share/{__name__}/lua/{__name__}-compare.lua')
    if os.path.exists(script_path_user):
        script_path = script_path_user
    elif os.path.exists(script_path_system):
        script_path = script_path_system
    else:
        error(f'No such file: {script_path_user}')
        error(f'No such file: {script_path_system}')
        croak(f'Cannot find {__name__}-compare.lua')

    cmd = [MPV, '--idle']
    cmd.append(f'--script={script_path}')
    scriptopts = []
    if debug:
        scriptopts.append(f'{__name__}-debug={"yes" if debug else "no"}')
    if playlist_size:
        scriptopts.append(f'{__name__}-playlist_size={playlist_size}')
    if font_size:
        scriptopts.append(f'{__name__}-font_size={font_size}')
    if estimates_file:
        scriptopts.append(f'{__name__}-estimates_file={estimates_file}')
    if scriptopts:
        cmd.append(f'--script-opts={",".join(scriptopts)}')
    if debug:
        print(cmd2str(cmd))
    subprocess.run(cmd, cwd=sample_dir)
