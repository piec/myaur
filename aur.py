#!/usr/bin/env python3
import bottle
from bottle import route, run, template, request
from bottle import Bottle
from bottle import static_file

import os
import re
import subprocess
from subprocess import PIPE, DEVNULL, STDOUT
import shlex
import json
import sh

app = Bottle()
ROOT = os.getcwd()
OVERLAYS_DIR = os.path.join(ROOT, 'overlays')
MAKEPKG = os.path.join(ROOT, 'makepkg', 'makepkg')

"""
search
  http://aur-url/rpc.php?type=search&arg=foobar
msearch
  http://aur-url/rpc.php?type=msearch&arg=cactus
info
  http://aur-url/rpc.php?type=info&arg=1123
  http://aur-url/rpc.php?type=info&arg=foobar
multiinfo
  http://aur-url/rpc.php?type=multiinfo&arg[]=cups-xerox&arg[]=cups-mc2430dl&arg[]=10673

http://aur-url/packages/pa/package-query-git/package-query-git.tar.gz
"""

log_level = 2
if log_level > 0:
    import inspect
    from colorama import Fore, Back, Style

level_attributes = { 
    1: ("ERR", Fore.RED),
    2: ("INFO", Fore.GREEN),
    3: ("DBG", Fore.BLUE),
}

def log(level, *args, **kwargs):
    global log_level
    if level <= log_level:
        if level in level_attributes:
            (name, color) = level_attributes[level]
        else:
            (name, color) = ("UNK", Fore.RESET)

        line, func = inspect.stack()[2][2:4]
        prefix = "%s %s:%s" % (name, func, line)
        prefix = prefix.ljust(25)
        prefix = color + prefix + Fore.RESET + "│ "
        print(prefix, end='')
        print(*args, **kwargs)

def debug(*args, **kwargs):
    log(3, *args, **kwargs)

def info(*args, **kwargs):
    log(2, *args, **kwargs)

def error(*args, **kwargs):
    log(1, *args, **kwargs)

class Package(object):
    def __init__(self, name, overlay_name,
                 url=None, description=None, version=None, license=None, id_=None,
                 last_modified=None, maintainer=None, category_id=None):
        self.name = name
        self.overlay_name = overlay_name

        self.url = url
        self.description = description
        self.version = version
        self.license = license
        self.id_ = id_
        self.last_modified = last_modified
        self.maintainer = maintainer
        self.category_id = category_id

    def to_json(self, url_path):
        return {
            'Name'           : self.name,
            'URLPath'        : url_path,

            'URL'            : self.url or 'none',
            'Description'    : self.description or '@desc',
            'Version'        : self.version or '@version',
            'FirstSubmitted' : '@FirstSubmitted',
            'License'        : self.license or '@license',
            'ID'             : self.id_ or '@id',
            'OutOfDate'      : 0,
            'LastModified'   : self.last_modified or '@lastmodified',
            'Maintainer'     : self.maintainer or '@maintainer',
            'CategoryID'     : self.category_id or 0,
            'NumVotes'       : 42
        }

    def apply_fields(self, fields):
        def get(name):
            if name in fields:
                return fields[name]
            return None

        pkgdesc = get('pkgdesc')
        self.description = pkgdesc[1:][:-1] if pkgdesc else None
        
        epoch = get('epoch')
        pkgver = get('pkgver')
        pkgrel = get('pkgrel')

        if all([epoch, pkgver, pkgrel]):
            self.version = "%s:%s-%s" % (epoch, pkgver, pkgrel)
        elif all([pkgver, pkgrel]):
            self.version = "%s-%s" % (pkgver, pkgrel)
        elif pkgver:
            self.version = pkgver


        return self

    def matches(self, string):
        low_str = string.lower()
        return (low_str in self.name.lower() or 
                (self.description and low_str in self.description.lower())
                )

    def __repr__(self):
        return '<P: "%s", path="%s">' % (self.name, self.url_path)


pkgbuild_fields = {
    'pkgver': re.compile(r'^pkgver=(?P<value>[a-zA-Z0-9\.]+)'),
    'pkgrel': re.compile(r'^pkgrel=(?P<value>[a-zA-Z0-9\.]+)'),
    'epoch': re.compile(r'^epoch=(?P<value>[0-9]+)'),
    'pkgdesc': re.compile(r'^pkgdesc=(?P<value>.+)'),
}

"""
extract details about the package from the PKGBUILD
same way as /usr/bin/makepkg
see get_full_version()
  $epoch:$pkgver-$pkgrel

makepkg updates $pkgver and $pkgrel inside the PKGBUILD on full bulid
for -git packages for example (where pkgver() is defined)
aur uses the "pkgver=..." and not the pkgver() function so we do the same

from https://wiki.archlinux.org/index.php/Arch_Packaging_Standards
"Version tags may not include hyphens! Letters, numbers, and periods only"
"""
def parse_pkgbuild(f, dirname, lines_to_parse=20):
    debug("dirname=%s, f=%s" % (dirname, str(f)))

    fields = {}

    for line in f.readlines()[:lines_to_parse]:
        for name, regex in pkgbuild_fields.items():
            m = regex.match(line)
            if m:
                if name in fields:
                    debug("seen %s again in %s" % (name, dirname))
                else:
                    fields[name] = m.group('value')
    debug("fields=", fields)
    return fields

def package_url(package):
    return "/packages/%s/%s" % (package.name, 'source.tar.gz')

"""read directory that has a PKGBUILD"""
def read_package_dir(package_dir, overlay_name):
    debug("%s" % package_dir)
    packages = {}
    pkgbuild_path = os.path.join(package_dir, 'PKGBUILD')
    try:
        for encoding in ['utf-8', 'latin9']:
            try:
                with open(pkgbuild_path, 'r', encoding=encoding) as f:
                    dirname = os.path.basename(package_dir)
                    package = Package(dirname, overlay_name)
                    fields = parse_pkgbuild(f, dirname)
                    package.apply_fields(fields)
                    return package
            except UnicodeDecodeError as e:
                error("'%s' in '%s'" % (e, dirname))
        error("can't read '%s'" % (pkgbuild_path))
    except IOError:
        debug("IOError")
        return None


"""read directory that has package directories"""
def read_overlay(overlay_path):
    info("overlay_path=%s" % overlay_path)
    if not os.path.isdir(overlay_path):
        return

    overlay_name = os.path.basename(overlay_path)
    for name in os.listdir(overlay_path):
        path = os.path.join(overlay_path, name)
        if os.path.isdir(path):
            package = read_package_dir(path, overlay_name)
            if package:
                yield package

overlays = {}

@app.route('/<overlay_name>/rpc.php')
def rpc_php(overlay_name):
    type = request.query.get('type')
    arg = request.query.get('arg')
    args = request.query.getall('arg[]')

    if overlay_name not in overlays:
        overlay = {}
        for package in read_overlay(os.path.join(OVERLAYS_DIR, overlay_name)):
            overlay[package.name] = package
        overlays[overlay_name] = overlay
        info("%d packages in '%s'" % (len(overlay), overlay_name))

    overlay = overlays[overlay_name]

    if type in ('info', 'multiinfo', 'search'):
        results = []

        if type == 'info' and arg in overlay:
            p = overlay[arg]
            results = [p.to_json(package_url(p))]
        elif type == 'search':
            if arg is not None:
                for name, p in overlay.items():
                    if p.matches(arg):
                        results.append(p.to_json(package_url(p)))
        elif type == 'multiinfo':
            for arg in args:
                if arg in overlay:
                    p = overlay[arg]
                    results.append(p.to_json(package_url(p)))

        #format restults
        len_results = len(results)

        if type == 'info':
            # mimic aur's impl: limit results to 1, and no list (except for 0 results :/)
            if len(results) > 0:
                results = results[0]
                len_results = 1

        return {
            'type': type,
            'resultcount': len_results,
            'results': results
        }

    return {'type': 'error', 'resultcount': 0, 'results': 'incorrect'}


def package_dir(overlay, package_name):
    path_dir = os.path.join(ROOT, 'overlays', overlay, package_name)
    if not os.path.isdir(path_dir): return None

    path_pkgbuild = os.path.join(path_dir, 'PKGBUILD')
    if not os.path.isfile(path_pkgbuild): return None

    return path_dir


def makepkg(overlay, package_name):
    debug("makepkg A")
    path_dir = package_dir(overlay, package_name)
    if not path_dir:
        return None
    debug("makepkg B")
    stdout = None
    stdout = PIPE
    process = subprocess.Popen(shlex.split('%s -S' % MAKEPKG), cwd=path_dir, close_fds=True, stdin=DEVNULL, stderr=STDOUT, stdout=stdout, env={})
    out, err = process.communicate()
    
    out_str = out.decode()
    marker = 'JsonDone\n'
    pos = out_str.find(marker)
    if pos < 0:
        error("can't find marker '%s'" % marker)
        error("output='%s'" % out_str)
        return None
    debug("output='%s'" % out_str)

    json_str = out_str[pos + len(marker):]
    debug("json_str='%s'" % json_str)
    makepkg_ret = json.loads(json_str)
    return makepkg_ret['file']


@app.route('/<overlay>/packages/<package_name>/<filename>')
def package(overlay, package_name, filename):
    source_package_path = makepkg(overlay, package_name)
    if source_package_path:
        if not source_package_path.startswith(OVERLAYS_DIR):
            error("source package, wrong prefix '%s'" % source_package_path)
            return 'error'
        rel_path = source_package_path[len(OVERLAYS_DIR):]
        debug("rel_path='%s'" % rel_path)
        return static_file(rel_path, root=OVERLAYS_DIR)
    error("source_package_path undef")
    return 'error'

@app.route('/env')
def index():
    import pprint
    return template("<pre>env={{env}}", env=pprint.pformat(request.environ))


@app.route('/config')
def index():
    import pprint
    return template("<pre>config={{config}}", config=pprint.pformat(request.app.config))

@app.route('/static/<path:path>')
def index(path):
    return static_file(path, root='static/')

def update_overlay(name):
    info("name='%s'" % name)
    overlay_path = os.path.join(OVERLAYS_DIR, name)
    git_path = os.path.join(overlay_path, '.git')

    if not os.path.isdir(git_path):
        error("'%s' is not a directory" % git_path)
        return False

    try:
        git = sh.git.bake(_cwd=os.path.join(OVERLAYS_DIR, name), _err_to_out=True, _out=info)
        git.reset('--hard').wait()
        git.clean('-fdx').wait()
        git.fetch('--all').wait()
        git.merge('--ff-only', 'origin').wait()
    except sh.ErrorReturnCode as e:
        error("git '%s'" % e)
        return False

    return True

@app.post('/github-hook')
def github_hook():
    #with open("github.json") as f:
        #payload = f.read()

    try:
        payload = request.POST["payload"]
    except KeyError as e:
        error("no 'payload' field")
        return

    try:
        j = json.loads(payload)
    except ValueError as e:
        error("json '%s'" % e)
        return

    try:
        name = j["repository"]["name"]
        url = j["repository"]["url"]
    except KeyError as e:
        error("keyerror '%s'" % e)
        return

    update_overlay(name)
    if name in overlays:
        info("reset '%s' index" % name)
        del overlays[name]

    #info(j)
    return template("<pre>name={{name}}<br>url={{url}}", name=name, url=url)


root_app = Bottle()
root_app.mount('/aur', app)

application = root_app

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("-v", "--verbose", action="count", help="increase output verbosity", default=2)
    args = parser.parse_args()
    log_level = args.verbose
    info("log_level=%d" % log_level)

    bottle.debug(True)
    #run(app=root_app, host='0.0.0.0', port=8080, reloader=True)
    run(app=(root_app), host='localhost', port=8080, reloader=True)

# vim:set ts=4:
