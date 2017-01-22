# code for profile walking with visitors

import abc
import errno
import functools
import os.path
import re
import shlex


class ProfileVisitor(abc.ABC):
    @abc.abstractmethod
    def handle_pkg(self, fn, p):
        pass

    @abc.abstractmethod
    def handle_use(self, fn, f):
        pass

    @abc.abstractmethod
    def handle_pkg_use(self, fn, pkg, f):
        pass

    @abc.abstractmethod
    def handle_make_conf(self, fn, data):
        pass

    @abc.abstractmethod
    def make_conf_dict(self, fn):
        pass


def parse_line_file(f, cb):
    for l in f:
        if l.startswith('#') or not l.strip():
            continue
        cb(l.strip())


def parse_package_use_file(f, cb):
    for l in f:
        if l.startswith('#') or not l.strip():
            continue
        pkg, *flags = l.strip().split()
        for fl in flags:
            cb(pkg, fl)


var_regex = re.compile(r'[$](?P<brace>[{])(?P<var>[A-Z0-9]*)(?(brace)[}])')


def parse_make_conf(f, cb, prev_vals):
    def wrap_shlex(it):
        while True:
            it = iter(it)
            try:
                k = next(it)
            except StopIteration:
                return
            eq = next(it)
            v = next(it)
            assert eq == '='
            yield (k, v)

    data = {}
    lex = shlex.shlex(f, posix=True)
    for k, v in wrap_shlex(lex):
        while True:
            m = var_regex.search(v)
            if not m:
                break
            # substitute var ref
            if m.group('var') in data:
                rv = data[m.group('var')]
            else:
                rv = prev_vals.get(m.group('var'), '')
            v = v[0:m.start()] + rv + v[m.end():]
        data[k] = v
    cb(data)


parsers = {}
parsers['make.defaults'] = parse_make_conf
for fn in ('packages', 'packages.build', 'package.mask', 'package.provided'):
    parsers[fn] = parse_line_file
for fn in ('use.force', 'use.mask', 'use.stable.force', 'use.stable.mask'):
    parsers[fn] = parse_line_file
for fn in ('package.use', 'package.use.force', 'package.use.mask',
        'package.use.stable.force', 'package.use.stable.mask'):
    parsers[fn] = parse_package_use_file


def process_profile(profile_path, visitor, verbose=True):
    # start by recurring into parent profiles
    try:
        with open(os.path.join(profile_path, 'parent'), 'r') as f:
            for l in f:
                l = l.strip()
                process_profile(os.path.join(profile_path, l), visitor, verbose)
    except OSError as e:
        if e.errno != errno.ENOENT:
            raise

    if verbose:
        print(os.path.abspath(profile_path))

    for fn, pf in parsers.items():
        if pf == parse_line_file:
            if fn.startswith('use.'):
                cb = functools.partial(visitor.handle_use, fn)
            else:
                cb = functools.partial(visitor.handle_pkg, fn)
        elif pf == parse_package_use_file:
            cb = functools.partial(visitor.handle_pkg_use, fn)
        elif pf == parse_make_conf:
            cb = functools.partial(visitor.handle_make_conf, fn)
        else:
            raise NotImplementedError(pf)

        try:
            with open(os.path.join(profile_path, fn), 'r', encoding='utf8') as f:
                if pf == parse_make_conf:
                    pf(f, cb, visitor.make_conf_dict(fn))
                else:
                    pf(f, cb)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise


class CombinedProfile(ProfileVisitor):
    incr_vars = ('USE', 'USE_EXPAND', 'USE_EXPAND_HIDDEN',
            'CONFIG_PROTECT', 'CONFIG_PROTECT_MASK', 'IUSE_IMPLICIT',
            'USE_EXPAND_IMPLICIT', 'USE_EXPAND_UNPREFIXED')

    def __init__(self):
        self.db_ = {}

    @staticmethod
    def handle_entry(s, x):
        if x.startswith('-'):
            s.discard(x[1:])
        else:
            s.add(x)

    def handle_pkg(self, fn, p):
        if fn not in self.db_:
            self.db_[fn] = set()
        self.handle_entry(self.db_[fn], p)

    def handle_use(self, fn, f):
        if fn not in self.db_:
            self.db_[fn] = set()
        self.handle_entry(self.db_[fn], f)

    def handle_pkg_use(self, fn, pkg, f):
        if fn not in self.db_:
            self.db_[fn] = {}
        if pkg not in self.db_[fn]:
            self.db_[fn][pkg] = set()
        self.handle_entry(self.db_[fn][pkg], f)

    def handle_make_conf(self, fn, data):
        if fn not in self.db_:
            self.db_[fn] = {}
        for k, v in data.items():
            if k in self.incr_vars:
                newv = set(self.db_[fn].get(k, '').split())
                # handle +/- logic
                for f in v.split():
                    self.handle_entry(newv, f)
                self.db_[fn][k] = ' '.join(sorted(newv))
            else:
                self.db_[fn][k] = v

    def make_conf_dict(self, fn):
        return self.db_.get(fn, {})

    def dump_all(self, d):
        for fn, data in self.db_.items():
            if data:
                with open(os.path.join(d, fn), 'w', encoding='utf8') as f:
                    if isinstance(data, set):
                        for l in sorted(data):
                            f.write('%s\n' % l)
                    elif isinstance(data, dict):
                        for key, values in sorted(data.items()):
                            if isinstance(values, set): # package.use*
                                if not values:
                                    continue
                                f.write('%s %s\n' % (key, ' '.join(sorted(values))))
                            else: # make.defaults
                                assert '"' not in values
                                f.write('%s="%s"\n' % (key, values))
                    else:
                        raise NotImplementedError(pf)
