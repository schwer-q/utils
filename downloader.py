#!/usr/bin/python3
#
# Copyright (c) 2020, Quentin Schwerkolt
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#    1. Redistributions of source code must retain the above copyright
#       notice, this list of conditions and the following disclaimer.
#    2. Redistributions in binary form must reproduce the above copyright
#       notice, this list of conditions and the following disclaimer in the
#       documentation and/or other materials provided with the distribution.
#    3. Neither the name of the <organization> nor the
#       names of its contributors may be used to endorse or promote products
#       derived from this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
# ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL <COPYRIGHT HOLDER> BE LIABLE FOR ANY
# DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
# (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
# LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
# ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
# (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
# SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
#

import datetime
import fcntl
import getopt
import hashlib
import os
import os.path
import sys
import time
import traceback
import urllib
import urllib.request
import xml.etree.ElementTree as ET

rootdir = None
quiet = False
verbose = False

class Entry(object):
    def __init__(self, name: str):
        self.name = name
        self.files = []

    def download(self):
        progress = Progress()
        print("* {}:".format(self.name))
        for file in self.files:
            print("  + {}:".format(file.name))
            if not os.path.exists(file.destdir):
                os.makedirs(file.destdir)

            filename = os.path.join(file.destdir, file.name)
            if file.exists():
                progress.start('    - checksuming')
                status = file.validate(progress)
                progress.end()
                if status:
                    print("    - {}: already downloaded".format(file.name))
                    continue

                print("    - {}: checksum mismatch. retrying...".format(file.name))
                os.unlink(filename)

            progress.start('    - downloading')
            file.download(progress)
            progress.end()
            progress.start('    - checksuming')
            status = file.validate(progress)
            progress.end()
            if not status:
                print("    - {}: checksum mismatch".format(file.name))
                os.unlink(filename)

class File(object):
    def __init__(self, name: str, destdir: str, url: str):
        self.name = name
        self.size = 0
        self.destdir = os.path.normpath(os.path.join(rootdir, destdir))
        self.url = url.replace('$(name)', self.name)
        self.checksums = {}

    def download(self, progress):
        up = urllib.request.urlopen(self.url)
        self.size = int(up.getheader('Content-Length', -1))
        filename = os.path.join(self.destdir, self.name)
        nread = 0
        with open(filename, 'wb') as fp:
            while True:
                buf = up.read(512)
                if not buf:
                    break

                fp.write(buf)
                nread += len(buf)
                if progress:
                    progress.update(nread, self.size)

    def exists(self):
        filename = os.path.join(self.destdir, self.name)
        if os.path.exists(filename):
            sb = os.lstat(filename)
            self.size = sb.st_size
            return True

        return False

    def validate(self, progress):
        engines = {}
        for algo in self.checksums.keys():
            engines[algo] = hashlib.new(algo)
        filename = os.path.join(self.destdir, self.name)
        nread = 0
        with open(filename, 'rb') as fp:
            while True:
                buf = fp.read(512)
                if not buf:
                    break

                for algo in engines.keys():
                    engines[algo].update(buf)
                nread += len(buf)
                if progress:
                    progress.update(nread, self.size)

        success = True
        for algo, digest in self.checksums.items():
            if engines[algo].hexdigest() != digest:
                success = False
        return success

class Progress(object):
    def __init__(self):
        self.prefix = ''
        self.start_ts = 0
        self.last = -1

    def start(self, prefix: str):
        self.prefix = prefix
        self.start_ts = time.time_ns()
        if not sys.stdout.isatty() and not quiet:
            print("{}:".format(self.prefix), end=' ')
            sys.stdout.flush()

    def update(self, current: int, total: int):
        if current == 0 or total == 0 or quiet:
            return

        now = time.time_ns()
        elapsed = now - self.start_ts
        remaining = int((elapsed * (total / current)) - elapsed)
        prog = int((100 * current) / total)
        eta = (int(remaining / (1000000000 * 3600)),
               int(remaining / (1000000000 * 60)) % 60,
               int(remaining / 1000000000) % 60)

        if sys.stdout.isatty():
            print("\r{}: {:>3}% {:0>2}:{:0>2}:{:0>2}".format(
                self.prefix, prog, eta[0], eta[1], eta[2]), end=' ')
        else:
            if prog == self.last:
                return

            if (prog % 10) == 0:
                print("{}%".format(prog), end='')
            elif (prog % 2) == 0:
                print('.', end='')
            sys.stdout.flush()
            self.last = prog

    def end(self):
        print()

def parse_xml_node(root: ET.Element):
    if root.tag == 'entry':
        entry = Entry(**root.attrib)
        for child in root:
            file = parse_xml_node(child)
            if not file:
                continue

            entry.files.append(file)
        return entry
    elif root.tag == 'file':
        file = File(**root.attrib)
        for child in root:
            checksum = parse_xml_node(child)
            file.checksums[checksum[0]] = checksum[1]
        return file
    elif root.tag == 'checksum':
        return root.attrib['algo'], root.attrib['digest']
    else:
        print("unknown tag: {}".format(root.tag))

def main():
    global rootdir
    global quiet
    global verbose

    rootdir = os.getcwd()
    try:
        opts, args = getopt.getopt(sys.argv[1:], 'qvur:')
    except getopt.GetoptError as e:
        print("illegal option - {}".format(e.opt), file=sys.stderr)
        return 2

    fp = open('/tmp/download.lock', 'w')
    for opt, arg in opts:
        if opt == '-q':
            quiet = True
        elif opt == '-r':
            rootdir = os.path.normpath(arg)
        elif opt == '-u':
            try:
                fcntl.lockf(fp, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except IOError:
                print('already running...')
                return 0
        elif opt == '-v':
            verbose = True

    if quiet and verbose:
        print('-q and -v cannot be specified at the same time', file=sys.stderr)
        return 2

    entries = []
    for arg in args:
        tree = ET.parse(arg)
        root = tree.getroot()
        assert(root.tag == 'entries')
        for child in root:
            entry = parse_xml_node(child)
            if not entry:
                continue

            entries.append(entry)

    for entry in entries:
        entry.download()
    fp.close()
    os.unlink('/tmp/downloader.lock')

if __name__ == '__main__':
    try:
        exitcode = main()
    except KeyboardInterrupt:
        exitcode = 1
    except SystemExit as e:
        exitcode = e
    except Exception:
        traceback.print_exc()
        exitcode = 99

    sys.exit(exitcode)
