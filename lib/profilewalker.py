# code for profile walking with visitors

import errno
import functools
import os.path
import re
import shlex


class ProfileVisitor(object):
    #def handle_pkg(self, fn, p, path):

    #def handle_use(self, fn, f, path):

    #def handle_pkg_use(self, fn, pkg, f, path):

    #def handle_make_conf(self, fn, data, path):

    def make_conf_dict(self, fn):
        return {}


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


var_regex = re.compile(r'[$](?P<brace>[{])(?P<var>[A-Za-z0-9_]*)(?(brace)[}])')


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


def process_profile(profile_path, visitor, verbose=True, recursive=True):
    if recursive:
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
                cb = 'handle_use'
            else:
                cb = 'handle_pkg'
        elif pf == parse_package_use_file:
            cb = 'handle_pkg_use'
        elif pf == parse_make_conf:
            cb = 'handle_make_conf'
        else:
            raise NotImplementedError(pf)
        # skip files visitor does not care about
        if not hasattr(visitor, cb):
            continue
        path = os.path.join(profile_path, fn)
        cb = functools.partial(getattr(visitor, cb), fn, path=path)

        try:
            with open(path, 'r', encoding='utf8') as f:
                if pf == parse_make_conf:
                    pf(f, cb, visitor.make_conf_dict(fn))
                else:
                    pf(f, cb)
        except OSError as e:
            if e.errno != errno.ENOENT:
                raise


def flag_to_str(k, v):
    return '%s%s' % ('' if v else '-', k)


class MakeConfDictWrapper(object):
    def __init__(self, d):
        self._d = d

    def get(self, key, default=None):
        v = self._d.get(key, default)
        if isinstance(v, dict):
            v = ' '.join(
                flag_to_str(k, v) for k, v in sorted(v.items()))
        return v


class CombinedProfile(ProfileVisitor):
    incr_vars = ('USE', 'USE_EXPAND', 'USE_EXPAND_HIDDEN',
            'CONFIG_PROTECT', 'CONFIG_PROTECT_MASK', 'IUSE_IMPLICIT',
            'USE_EXPAND_IMPLICIT', 'USE_EXPAND_UNPREFIXED')

    def __init__(self):
        self.db_ = {}
        self.db_dumps_ = {}

    @staticmethod
    def handle_entry(s, x):
        if x.startswith('-'):
            s[x[1:]] = False
        else:
            s[x] = True

    def handle_pkg(self, fn, p, path):
        if fn not in self.db_:
            self.db_[fn] = {}
            self.db_dumps_[fn] = self.dump_pkg_or_use
        self.handle_entry(self.db_[fn], p)

    def handle_use(self, fn, f, path):
        if fn not in self.db_:
            self.db_[fn] = {}
            self.db_dumps_[fn] = self.dump_pkg_or_use
        self.handle_entry(self.db_[fn], f)

    def handle_pkg_use(self, fn, pkg, f, path):
        if fn not in self.db_:
            self.db_[fn] = {}
            self.db_dumps_[fn] = self.dump_pkg_use
        if pkg not in self.db_[fn]:
            self.db_[fn][pkg] = {}
        self.handle_entry(self.db_[fn][pkg], f)

    def handle_make_conf(self, fn, data, path):
        if fn not in self.db_:
            self.db_[fn] = {}
            self.db_dumps_[fn] = self.dump_make_conf
        for k, v in data.items():
            if k in self.incr_vars:
                if k not in self.db_[fn]:
                    self.db_[fn][k] = {}
                # handle +/- logic
                for f in v.split():
                    self.handle_entry(self.db_[fn][k], f)
            else:
                self.db_[fn][k] = v

    def make_conf_dict(self, fn):
        return MakeConfDictWrapper(self.db_.get(fn, {}))

    @staticmethod
    def dump_pkg_or_use(f, data):
        for k, v in sorted(data.items()):
            f.write('%s\n' % flag_to_str(k, v))

    @staticmethod
    def dump_pkg_use(f, data):
        for key, flags in sorted(data.items()):
            f.write('%s %s\n' % (key, ' '.join(
                flag_to_str(k, v) for k, v in sorted(flags.items()))))

    @staticmethod
    def dump_make_conf(f, data):
        for key, value in sorted(data.items()):
            if isinstance(value, dict):
                f.write('%s="%s"\n' % (key, ' '.join(
                    flag_to_str(k, v) for k, v in sorted(value.items()))))
            else:
                assert '"' not in value
                f.write('%s="%s"\n' % (key, value))

    def dump_all(self, d):
        for fn, data in self.db_.items():
            if data:
                with open(os.path.join(d, fn), 'w', encoding='utf8') as f:
                    # call dump method
                    self.db_dumps_[fn](f, data)
