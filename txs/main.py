import os
import time
import argparse
import sys
from collections import abc
from . import utils
from . import ffmpeg
from . import __name__, __version__

class MyHelpFormatter(argparse.HelpFormatter):
    def _get_help_string(self, action):
        def as_str(thing):
            if isinstance(thing, abc.Iterable) and not isinstance(thing, str):
                string = ' '.join(as_str(x) for x in thing)
            elif not isinstance(thing, bool):
                string = str(thing)
            else:
                string = None
            if string:
                return string

        help = action.help
        if action.default is not argparse.SUPPRESS:
            defaulting_nargs = [argparse.OPTIONAL, argparse.ZERO_OR_MORE]
            if action.option_strings or action.nargs in defaulting_nargs:
                string = as_str(action.default)
                if string is not None:
                    help += f' (Default: {string})'
        return help

    def _fill_text(self, text, width, indent):
        return ''.join(indent + line for line in text.splitlines(keepends=True))


TUTORIAL = f'''
This is a tutorial that should get you started. Run `{__name__} -h` and
`{__name__} SUBCOMMAND -h` for more information.

    $ {__name__} -s source.mkv -r 25:00 10 -x crf=19:me=umh samples \\
      -xs subme=9/10:deblock=-2,-1/-3,-3:no-fast-pskip

The above command creates test encodes or samples with all possible combinations
of the given values for "subme", "deblock" and "no-fast-pskip" in the directory
"samples.orig@25:00-1.subme:deblock:no-fast-pskip". The samples are all encoded
with "crf=19:me=umh", they are all 10 seconds long and start at 25 minutes in
source.mkv:

    source.sample@25:00-10.crf=19:me=umh:subme=10:deblock=-2,-1.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=10:deblock=-2,-1:no-fast-pskip.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=10:deblock=-3,-3.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=10:deblock=-3,-3:no-fast-pskip.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=9:deblock=-2,-1.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=9:deblock=-2,-1:no-fast-pskip.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=9:deblock=-3,-3.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=9:deblock=-3,-3:no-fast-pskip.mkv

By providing multiple sets of sample settings to -xs you can limit the number of
combinations:

    $ {__name__} -s source.mkv -r 25:00 10 -x crf=19:me=umh samples \\
      -xs subme=10/11:no-deblock deblock=-2,-1/-3,-3

This creates the following samples:

    source.sample@25:00-10.crf=19:me=umh:deblock=-2,-1.mkv
    source.sample@25:00-10.crf=19:me=umh:deblock=-3,-3.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=10.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=10:no-deblock.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=11.mkv
    source.sample@25:00-10.crf=19:me=umh:subme=11:no-deblock.mkv

To compare encodes, use the "compare" subcommand:

    $ {__name__} -s source.mkv compare samples.orig@25:00-1.subme:deblock:no-fast-pskip

This opens mpv in fullscreen mode with a playlist of two samples. Switch between
samples with "j" and "k" or toggle the original source with "o". Pick the better
sample with "b", the worse with "w" or mark them as equal with "e". New samples
are loaded automatically every time you make a decision. After you've rated all
samples once, any samples that were marked as equal are loaded again. This
process will leave you with the best sample and its settings in the end.

You can adjust the playlist size with the -p (--playlist-size) option if you
want to compare more than two samples.

"Shift+w" does the same thing as "w", but it also removes the sample and its log
file from the file system.

Show and hide the playlist overlay with "`".

Some final notes you might find useful:

- Choose your sample range carefully. It should be representative of the full
  video. 10 seconds or less is fine to check something quickly, but use 60
  seconds or more for fine tuning.

- Increasing gamma with "6" to around 10 to 15 makes differences more obvious.
  (Decrease with "5".) Don't go too high or you'll watch pixels dance that
  nobody else will ever see.

- Try switching between samples while playing. txs should preserve playback
  time, but there is a small delay when mpv seeks.

- You can seek forward and backward by single frames with "." and ",".
'''.strip()


def run():
    argparser = argparse.ArgumentParser(
        prog=__name__,
        formatter_class=MyHelpFormatter,
        description='Generate and compare x264 test encodings with different settings')
    argparser.add_argument('-s', '--source',
                           help='Path to original video')
    argparser.add_argument('-r', '--range', nargs=2, default=['5:00', '10'], metavar=('START', 'DURATION'),
                           help=('Time range in original video; '
                                 'e.g. "10:00 60" means "from 10 minutes to 11 minutes"'))
    argparser.add_argument('-x', '--x264-settings', default='',
                           help=('Colon-separated x264 settings (colons in values must be escaped);'
                                 'subcommands may override these'))
    argparser.add_argument('-d', '--dry-run', action='store_true',
                           help='Only show what would be done with these arguments')
    argparser.add_argument('-o', '--overwrite', action='store_true',
                           help='Overwrite existing files')
    argparser.add_argument('-e', '--estimates-file', default='./estimates', metavar='PATH',
                           help=('Where to store estimates of final size and encoding time;'
                                 'this path is relative to the samples directory'))
    argparser.add_argument('--version', action='version', version=f'{__name__} {__version__}')

    subparsers = argparser.add_subparsers()

    argparser_tutorial = subparsers.add_parser(
        'tutorial',
        help='Show tutorial that explains the basic workflow',
        description='Show tutorial that explains the basic workflow')
    argparser_tutorial.set_defaults(func=lambda args: print(TUTORIAL))

    argparser_samples = subparsers.add_parser(
        'samples',
        help='Generate samples with different settings',
        description='Generate samples with different settings')
    argparser_samples.add_argument('-xs', '--sample-settings', nargs='+', default=[], metavar='SETTINGS',
                                   help='x264 settings to test; values are separated with "/"')
    argparser_samples.set_defaults(func=_samples)

    argparser_compare = subparsers.add_parser(
        'compare',
        formatter_class=MyHelpFormatter,
        help='Compare previously generated samples',
        description='Compare previously generated samples',
        epilog=('key bindings:\n'
                '  j        Play next sample\n'
                '  k        Play previous sample\n'
                '  b        Remove all other samples from playlist\n'
                '           and load more samples\n'
                '  w        Remove current sample from playlist;\n'
                '           load more samples if there is only one left\n'
                '  e        Samples in current playlist are equal; revisit \n'
                '           them after seeing all other samples at least once\n'
                '  shift+w  Delete sample from file system and estimates file\n'
                '  o        Show/Hide original source\n'
                '  `        Show/Hide current playlist\n'
                '\n'
                '  You can change them by putting these lines in ~/.config/mpv/input.conf:\n'
                '    j       script-binding txs/playlist-next\n'
                '    k       script-binding txs/playlist-prev\n'
                '    b       script-binding txs/sample-is-better\n'
                '    w       script-binding txs/sample-is-worse\n'
                '    e       script-binding txs/samples-are-equal\n'
                '    shift+w script-binding txs/sample-is-garbage\n'
                '    o       script-binding txs/toggle-original\n'
                '    `       script-binding txs/toggle-info\n'
                '\n\n'
                'configuration:\n'
                '  The file "script-opts/txs.conf" in mpv\'s user folder (e.g.\n'
                '   ~/.config/mpv/script-opts/txs.conf) can configure these options:\n'
                '     debug=no\n'
                '     playlist_size=5\n'
                '     font_size=8\n'
                '     font_color=FFFFFF\n'
                '     border_size=1.0\n'
                '     border_color=101010\n'
                '     estimates_file=./estimates\n'
        ))

    argparser_compare.add_argument('samples',
                                   help='Directory that contains the samples')
    argparser_compare.add_argument('-p', '--playlist-size', default=None,
                                   help='Maximum number of sample samples to compare')
    argparser_compare.add_argument('-f', '--font-size', default=None,
                                   help='Font size for playlist')
    argparser_compare.add_argument('--debug', action='store_true',
                                   help='Print debugging messages in Lua print')
    argparser_compare.set_defaults(func=_compare)

    argparser_bframes = subparsers.add_parser(
        'bframes',
        help='Generate test encode and show consecutive B-frames percentages',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=('Generate test encode and show consecutive B-frames percentages\n\n'
                     'The settings given by --x264-settings are used but are optimized\n'
                     'for speed, e.g. crf=51.'))
    argparser_bframes.add_argument('-b', '--bframes', default='16',
                                   help='Maximum number of consecutive B-frames in test encode')
    argparser_bframes.add_argument('-a', '--b-adapt', default='2',
                                   help='Adaptive B-frame decision method')
    argparser_bframes.set_defaults(func=_bframes)

    args = argparser.parse_args()
    if hasattr(args, 'func'):
        args.func(args)
    else:
        argparser.print_help()

def _samples(args):
    base_settings = utils.parse_settings(args.x264_settings)
    sample_settings = utils.generate_sample_settings(*args.sample_settings)
    title = utils.title(args.source)
    samples_dir = os.path.join('.', (f'samples.{title}@{"-".join(args.range)}.' +
                                     ':'.join(utils.sample_keys(sample_settings))))
    if not sample_settings:
        utils.croak('Missing argument: --sample-settings')

    print(f'    Base settings: {utils.settings2str(base_settings, escape=False)}')
    print(f'{len(sample_settings):9d} samples: '
          f'{utils.settings2str(sample_settings, escape=False)}')
    print(f'Samples directory: {samples_dir}')
    if not args.dry_run:
        utils.mkdir(samples_dir)
        # Extract range from original into separate file
        excerpt_path = os.path.join(samples_dir, f'{title}.original@{"-".join(args.range)}.mkv')
        if not os.path.exists(excerpt_path):
            try:
                ffmpeg.encode(args.source, dest=excerpt_path,
                              start=args.range[0], stop=args.range[1],
                              topic=f'  Extracting range {args.range[0]} - {args.range[1]}',
                              create_logfile=False)
            except KeyboardInterrupt:
                print('\n')
                utils.cleanup(excerpt_path)
                utils.croak('Aborted')

    total_secs = ffmpeg.duration(args.source)
    estimates_file = os.path.join(samples_dir, args.estimates_file)
    try:
        for i,diff_settings in enumerate(sample_settings, start=1):
            settings = utils.combine_dicts(base_settings, diff_settings)
            dest = os.path.join(samples_dir,
                                (f'{title}.sample@'
                                 f'{"-".join(args.range)}.'
                                 f'{utils.settings2str(settings, escape=False)}'
                                 f'.mkv'))
            print(f'Sample {i}/{len(sample_settings)}: '
                  f'{utils.settings2str(diff_settings, escape=False)}')
            if not args.dry_run and (args.overwrite or not os.path.exists(dest)):
                start_time = time.monotonic()
                ffmpeg.encode(excerpt_path, dest, settings, topic='  Encoding')
                enc_time = time.monotonic() - start_time
                sample_secs = ffmpeg.duration(dest)
                est_time = enc_time * total_secs / sample_secs
                est_size = os.path.getsize(dest) * total_secs / sample_secs
                utils.update_estimates(estimates_file, diff_settings,
                                       est_time, est_size, settings)
                est = utils.read_estimates(estimates_file)
                key = utils.settings2str(diff_settings, escape=False)
                print(f'  Estimated encoding time: {est[key]["time_str"]}')
                print(f'     Estimated final size: {est[key]["size_str"]}')
            elif os.path.exists(dest):
                print(f'  Already encoded')
                est = utils.read_estimates(estimates_file)
                key = utils.settings2str(diff_settings, escape=False)
                if key in est:
                    print(f'  Estimated encoding time: {est[key]["time_str"]}')
                    print(f'     Estimated final size: {est[key]["size_str"]}')
    except KeyboardInterrupt:
        print('\n')
        utils.cleanup(dest)
        utils.croak('Aborted')
    else:
        if not args.dry_run:
            cmd = [__name__, 'compare', samples_dir]
            print(f'To compare settings visually run:\n{utils.cmd2str(cmd)}')
            if utils.dialog_yesno('Do you want to compare samples now?'):
                utils.compare_samples(samples_dir)


def _compare(args):
    utils.compare_samples(args.samples,
                          debug=args.debug,
                          playlist_size=args.playlist_size,
                          font_size=args.font_size,
                          estimates_file=args.estimates_file)


def _bframes(args):
    title = utils.title(args.source)
    bframes_dir = os.path.join('.', f'bframes:{title}@{"-".join(args.range)}')
    settings = {**utils.parse_settings(args.x264_settings),
                **{# These settings shouldn't change the consecutive bframes
                   # percentages, but they make the test encode faster.
                    'crf': '51',
                    'trellis': '0',
                    'ref': '1',
                    'aq-mode': '0',
                    'partitions': 'none',
                    'weightp': '0',
                    'no-mixed-refs': None,
                    'no-deblock': None,
                    'no-cabac': None,
                    'no-8x8dct': None,
                    'no-scenecut': None},
                **{'bframes': args.bframes, 'b-adapt': args.b_adapt}}
    dest = os.path.join(bframes_dir,
                        (f'{title}.bframes@{"-".join(args.range)}.'
                         f'{utils.settings2str(settings, escape=False)}'
                         f'.mkv'))
    print(f'Finding consecutive B-frames with these settings:')
    print(utils.settings2str(settings, escape=False))
    try:
        if not args.dry_run:
            utils.mkdir(bframes_dir)
            if args.overwrite or not os.path.exists(utils.logfile(dest)):
                ffmpeg.encode(args.source, dest, settings,
                              start=args.range[0], stop=args.range[1])
    except KeyboardInterrupt:
        print('\n')
        utils.cleanup(dest)
        utils.croak('Aborted')
    else:
        if not args.dry_run:
            bframes = ffmpeg.bframes(utils.logfile(dest))
            lines = [' '.join(f' {i:2d}  ' for i in range(17))]
            values = []
            for perc in bframes:
                values.append(f'{perc:4.1f}%')
            lines.append(' '.join(values))
            lines.append(utils.wrap('For each possible number of consecutive B-frames, '
                                    'show how many frames (in percent) are in such a '
                                    'sequence of B-frames.', width=100))
            for line in lines:
                print(line)
            with open(os.path.join(bframes_dir, 'bframes'), 'w') as f:
                for line in lines:
                    print(line, file=f)
