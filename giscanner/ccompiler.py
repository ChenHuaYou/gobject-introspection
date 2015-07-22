# -*- Mode: Python -*-
# GObject-Introspection - a framework for introspecting GObject libraries
# Copyright (C) 2014  Chun-wei Fan
#
# This library is free software; you can redistribute it and/or
# modify it under the terms of the GNU Lesser General Public
# License as published by the Free Software Foundation; either
# version 2 of the License, or (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the
# Free Software Foundation, Inc., 59 Temple Place - Suite 330,
# Boston, MA 02111-1307, USA.
#

import os
import subprocess

import sys
import distutils

from distutils.msvccompiler import MSVCCompiler
from distutils.cygwinccompiler import Mingw32CCompiler
from distutils.sysconfig import customize_compiler

from . import utils


class CCompiler(object):

    compiler_cmd = ''
    compiler = None
    _cflags_no_deprecation_warnings = ''

    def __init__(self,
                 environ=os.environ,
                 osname=os.name,
                 compiler_name=None):

        if osname == 'nt':
            # The compiler used here on Windows may well not be
            # the same compiler that was used to build Python,
            # as the official Python binaries are built with
            # Visual Studio
            if compiler_name is None:
                if environ.get('MSYSTEM') == 'MINGW32' or environ.get('MSYSTEM') == 'MINGW64':
                    compiler_name = 'mingw32'
                else:
                    compiler_name = distutils.ccompiler.get_default_compiler()
            if compiler_name != 'msvc' and \
               compiler_name != 'mingw32':
                raise SystemExit('Specified Compiler \'%s\' is unsupported.' % compiler_name)
        else:
            # XXX: Is it common practice to use a non-Unix compiler
            #      class instance on non-Windows on platforms g-i supports?
            compiler_name = distutils.ccompiler.get_default_compiler()

        # Now, create the distutils ccompiler instance based on the info we have.
        self.compiler = distutils.ccompiler.new_compiler(compiler=compiler_name)
        customize_compiler(self.compiler)

        # customize_compiler from distutils only does customization
        # for 'unix' compiler type.  Also, avoid linking to msvcrxx.dll
        # for MinGW builds as the dumper binary does not link to the
        # Python DLL, but link to msvcrt.dll if necessary.
        if isinstance(self.compiler, Mingw32CCompiler):
            if self.compiler.dll_libraries != ['msvcrt']:
                self.compiler.dll_libraries = []
            if self.compiler.preprocessor is None:
                self.compiler.preprocessor = self.compiler.compiler + ['-E']

        if self.check_is_msvc():
            # We trick distutils to believe that we are (always) using a
            # compiler supplied by a Windows SDK, so that we avoid launching
            # a new build environment to detect the compiler that is used to
            # build Python itself, which is not desirable, so that we use the
            # compiler commands (and env) as-is.
            os.environ['DISTUTILS_USE_SDK'] = '1'
            if 'MSSdk' not in os.environ:
                if 'WindowsSDKDir' in os.environ:
                    os.environ['MSSdk'] = os.environ.get('WindowsSDKDir')
                elif os.environ.get('VCInstallDir'):
                    os.environ['MSSdk'] = os.environ.get('VCInstallDir')

            self.compiler_cmd = 'cl.exe'

            self._cflags_no_deprecation_warnings = "-wd4996"
        else:
            if (isinstance(self.compiler, Mingw32CCompiler)):
                self.compiler_cmd = self.compiler.compiler[0]
            else:
                self.compiler_cmd = ''.join(self.compiler.executables['compiler'])

            self._cflags_no_deprecation_warnings = "-Wno-deprecated-declarations"

    def get_internal_link_flags(self, args, libtool, libraries, libpaths):
        # An "internal" link is where the library to be introspected
        # is being built in the current directory.

        # Search the current directory first
        # (This flag is not supported nor needed for Visual C++)
        if not self.check_is_msvc():
            args.append('-L.')

            # https://bugzilla.gnome.org/show_bug.cgi?id=625195
            if not libtool:
                args.append('-Wl,-rpath=.')
                args.append('-Wl,--no-as-needed')

        for library in libraries:
            if self.check_is_msvc():
                args.append(library + '.lib')
            else:
                if library.endswith(".la"):  # explicitly specified libtool library
                    args.append(library)
                else:
                    args.append('-l' + library)

        for library_path in libpaths:
            # Not used/needed on Visual C++, and -Wl,-rpath options
            # will cause grief
            if not self.check_is_msvc():
                args.append('-L' + library_path)
                if os.path.isabs(library_path):
                    if libtool:
                        args.append('-rpath')
                        args.append(library_path)
                    else:
                        args.append('-Wl,-rpath=' + library_path)

    def get_external_link_flags(self, args, libraries):
        # An "external" link is where the library to be introspected
        # is installed on the system; this case is used for the scanning
        # of GLib in gobject-introspection itself.

        for library in libraries:
            if self.check_is_msvc():
                args.append(library + '.lib')
            else:
                if library.endswith(".la"):  # explicitly specified libtool library
                    args.append(library)
                else:
                    args.append('-l' + library)

    def resolve_windows_libs(self, libraries, options):
        args = []
        libsearch = []

        # When we are using Visual C++...
        if self.check_is_msvc():
            # The search path of the .lib's on Visual C++
            # is dependent on the LIB environmental variable,
            # so just query for that
            libpath = os.environ.get('LIB')
            libsearch = libpath.split(';')

            # Use the dumpbin utility that's included in
            # every Visual C++ installation to find out which
            # DLL the library gets linked to
            args.append('dumpbin.exe')
            args.append('-symbols')

        # When we are not using Visual C++ (i.e. we are using GCC)...
        else:
            libtool = utils.get_libtool_command(options)
            if libtool:
                args.append(utils.which(os.environ.get('SHELL', 'sh.exe')))
                args.extend(libtool)
                args.append('--mode=execute')
            # FIXME: it could have prefix (i686-w64-mingw32-dlltool.exe)
            args.extend(['dlltool.exe', '--identify'])
            proc = subprocess.Popen([self.compiler_cmd, '-print-search-dirs'],
                                    stdout=subprocess.PIPE)
            o, e = proc.communicate()
            for line in o.splitlines():
                if line.startswith('libraries: '):
                    libsearch = line[len('libraries: '):].split(';')

        shlibs = []
        not_resolved = []
        for lib in libraries:
            found = False
            candidates = [
                'lib%s.dll.a' % lib,
                'lib%s.a' % lib,
                '%s.dll.a' % lib,
                '%s.a' % lib,
                '%s.lib' % lib,
            ]
            for l in libsearch:
                if found:
                    break
                if l.startswith('='):
                    l = l[1:]
                for c in candidates:
                    if found:
                        break
                    implib = os.path.join(l, c)
                    if os.path.exists(implib):
                        proc = subprocess.Popen(args + [implib],
                                                stdout=subprocess.PIPE)
                        o, e = proc.communicate()
                        for line in o.splitlines():
                            if self.check_is_msvc():
                                # On Visual Studio, dumpbin -symbols something.lib gives the
                                # filename of DLL without the '.dll' extension that something.lib
                                # links to, in the line that contains
                                # __IMPORT_DESCRIPTOR_<dll_filename_that_something.lib_links_to>

                                if '__IMPORT_DESCRIPTOR_' in line:
                                    line_tokens = line.split()
                                    for item in line_tokens:
                                        if item.startswith('__IMPORT_DESCRIPTOR_'):
                                            shlibs.append(item[20:] + '.dll')
                                            found = True
                                            break
                                if found:
                                    break
                            else:
                                shlibs.append(line)
                                found = True
                                break
            if not found:
                not_resolved.append(lib)
        if len(not_resolved) > 0:
            raise SystemExit(
                "ERROR: can't resolve libraries to shared libraries: " +
                ", ".join(not_resolved))
        return shlibs

    def check_is_msvc(self):
        if isinstance(self.compiler, MSVCCompiler):
            return True
        else:
            return False
