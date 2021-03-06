# Universal Subtitles, universalsubtitles.org
# 
# Copyright (C) 2010 Participatory Culture Foundation
# 
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
# 
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
# 
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see 
# http://www.gnu.org/licenses/agpl-3.0.html.

import sys, os, shutil, subprocess, logging, time
import re

from django.conf import settings
from django.core.management.base import BaseCommand
from django.contrib.sites.models import Site
from django.template.loader import render_to_string

import optparse

from deploy.git_helpers import get_current_commit_hash

from apps import widget

LAST_COMMIT_GUID = get_current_commit_hash()

def _make_version_debug_string():
    """
    See Command._append_verion_for_debug

    We have this as an external function because we need this on compilation and testing deployment
    """
    return '/*unisubs.static_version="%s"*/' % LAST_COMMIT_GUID
    



logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s %(levelname)s %(message)s')

def to_static_root(*paths):
    return os.path.join(settings.STATIC_ROOT, *paths)
JS_LIB = os.path.join(settings.PROJECT_ROOT, "media")
CLOSURE_LIB = os.path.join(JS_LIB, "js", "closure-library")
FLOWPLAYER_JS = os.path.join(
    settings.PROJECT_ROOT, "media/flowplayer/flowplayer-3.2.6.min.js")
COMPILER_PATH = os.path.join(settings.PROJECT_ROOT,  "closure", "compiler.jar")


DIRS_TO_COMPILE = []
SKIP_COPING_ON = DIRS_TO_COMPILE + [
    "videos",
    "*closure-lib*" ,
    settings.COMPRESS_OUTPUT_DIRNAME,
    "teams",
     ]

NO_UNIQUE_URL = (
# TODO: Figure out if you can uncomment this, then possibly remove
# special case for it in send_to_s3
#    {
#        "name": "embed.js",
#        "no-cache": True 
#    },
    {
        "name": "js/unisubs-streamer.js",
        "no-cache": True
    }, 
    {
        "name": "js/unisubs-widgetizer.js",
        "no-cache": True
    }, {
        "name": "js/unisubs-widgetizer-debug.js",
        "no-cache": True,
    }, {
        "name": "js/unisubs-widgetizer-sumo.js",
        "no-cache": True,
    }, {
        "name": "js/unisubs-api.js",
        "no-cache": True
    }, {
        "name": "js/unisubs-statwidget.js",
        "no-cache": False,
    }, {
        "name": "js/widgetizer/widgetizerprimer.js",
        "no-cache": True
    }
)

def call_command(command):
    process = subprocess.Popen(command.split(' '),
                               stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE)
    return process.communicate()

def get_cache_base_url():
    return "%s%s/%s" % (settings.STATIC_URL_BASE, settings.COMPRESS_OUTPUT_DIRNAME, LAST_COMMIT_GUID)

def get_cache_dir():
    return os.path.join(settings.STATIC_ROOT, settings.COMPRESS_OUTPUT_DIRNAME, LAST_COMMIT_GUID)

def sorted_ls(path):
    """
    Returns contents of dir from older to newer
    """
    mtime = lambda f: os.stat(os.path.join(path, f)).st_mtime
    return list(sorted(os.listdir(path), key=mtime))

class Command(BaseCommand):
    """
    
    """


    help = 'Compiles all bundles in settings.py (css and js).'
    args = 'media_bundles'

    option_list = BaseCommand.option_list + (

        optparse.make_option('--checks-version',
            action='store_true', dest='test_str_version', default=True,
            help="Check that we outputed the version string for comopiled files."),


        optparse.make_option('--keeps-previous',
            action='store_true', dest='keeps_previous', default=False,
            help="Will remove older static media builds."),
        )
    
    def _append_version_for_debug(self, descriptor, file_type):
        """
        We append the /*unisubs.static_version="{{commit guid}"*/ to the end of the
        file so we can debug, be sure we have the correct version of media.

        Arguments:
        `descriptor` : the fd to append to
        `file_type` : if it's a js or html or css file - we currently only support js and css
            """
        descriptor.write(_make_version_debug_string())
        
    def compile_css_bundle(self, bundle_name, bundle_type, files):
        file_list = [os.path.join(settings.STATIC_ROOT, x) for x in files]
        for f in file_list:
            open(f).read()
        buffer = [open(f).read() for f in file_list]
        dir_path = os.path.join(self.temp_dir, "css-compressed")
        if os.path.exists(dir_path) is False:
            os.mkdir(dir_path)
        concatenated_path =  os.path.join(dir_path, "%s.%s" % (bundle_name, bundle_type))
        out = open(concatenated_path, 'w')
        out.write("".join(buffer))        
        out.close()
        if bundle_type == "css":
            filename = "%s.css" % ( bundle_name)
            cmd_str = "%s --type=%s %s" % (settings.COMPRESS_YUI_BINARY, bundle_type, concatenated_path)
        if self.verbosity > 1:
            logging.info( "calling %s" % cmd_str)
        output, err_data  = call_command(cmd_str)

            
        out = open(concatenated_path, 'w')
        out.write(output)
        self._append_version_for_debug(out, "css")
        out.close()
        #os.remove(concatenated_path)
        return  filename

    def compile_js_bundle(self, bundle_name, bundle_type, files):
        bundle_settings = settings.MEDIA_BUNDLES[bundle_name]
        if 'bootloader' in bundle_settings:
            output_file_name = "{0}-inner.js".format(bundle_name)
        else:
            output_file_name = "{0}.js".format(bundle_name)

        debug = bundle_settings.get("debug", False)
        extra_defines = bundle_settings.get("extra_defines", None)
        include_flash_deps = bundle_settings.get("include_flash_deps", True)
        closure_dep_file = bundle_settings.get("closure_deps",'js/closure-dependencies.js' )
        optimization_type = bundle_settings.get("optimizations", "ADVANCED_OPTIMIZATIONS")

        logging.info("Starting {0}".format(output_file_name))

        deps = [" --js %s " % os.path.join(JS_LIB, file) for file in files]
        calcdeps_js = os.path.join(JS_LIB, 'js', 'unisubs-calcdeps.js')
        compiled_js = os.path.join(self.temp_dir, "js" , output_file_name)
        if not os.path.exists(os.path.dirname(compiled_js)):
            os.makedirs(os.path.dirname(compiled_js))
        compiler_jar = COMPILER_PATH

        logging.info("Calculating closure dependencies")

        js_debug_dep_file = ''
        if debug:
            js_debug_dep_file = '-i {0}/{1}'.format(JS_LIB, 'js/closure-debug-dependencies.js')

        cmd_str = "%s/closure/bin/calcdeps.py -i %s/%s %s -p %s/ -o script"  % (
            CLOSURE_LIB,
            JS_LIB,
            closure_dep_file, 
            js_debug_dep_file,
            CLOSURE_LIB)
        if self.verbosity > 1:
            logging.info( "calling %s" % cmd_str)    
        output,_ = call_command(cmd_str)

        # This is to reduce the number of warnings in the code.
        # The unisubs-calcdeps.js file is a concatenation of a bunch of Google Closure
        # JavaScript files, each of which has a @fileoverview tag to describe it.
        # When put all in one file, the compiler complains, so remove them all.
        output_lines = filter(lambda s: s.find("@fileoverview") == -1,
                              output.split("\n"))

        calcdeps_file = open(calcdeps_js, "w")
        calcdeps_file.write("\n".join(output_lines))
        calcdeps_file.close()

        logging.info("Compiling {0}".format(output_file_name))

        debug_arg = ''
        if not debug:
            debug_arg = '--define goog.DEBUG=false'
        extra_defines_arg = ''
        if extra_defines is not None:
            for k, v in extra_defines.items():
                extra_defines_arg += ' --define {0}={1} '.format(k, v)
        cmd_str =  ("java -jar %s --js %s %s --js_output_file %s %s %s "
                    "--define goog.NATIVE_ARRAY_PROTOTYPES=false "
                    "--output_wrapper (function(){%%output%%})(); "
                    "--compilation_level %s") % \
                    (compiler_jar, calcdeps_js, deps, compiled_js, 
                     debug_arg, extra_defines_arg, optimization_type)

        if self.verbosity > 1:
            logging.info( "calling %s" % cmd_str)    
        output,err = call_command(cmd_str)

        with open(compiled_js, 'r') as compiled_js_file:
            compiled_js_text = compiled_js_file.read()

        with open(compiled_js, 'w') as compiled_js_file:
            if include_flash_deps:
                with open(os.path.join(JS_LIB, 'js', 'swfobject.js'), 'r') as swfobject_file:
                    compiled_js_file.write(swfobject_file.read())
                with open(FLOWPLAYER_JS, 'r') as flowplayerjs_file:
                    compiled_js_file.write(flowplayerjs_file.read())
            compiled_js_file.write(compiled_js_text)
            self._append_version_for_debug(compiled_js_file, "js")
        if len(output) > 0:
            logging.info("compiler.jar output: %s" % output)

        if 'bootloader' in bundle_settings:
            self._compile_js_bootloader(
                bundle_name, bundle_settings['bootloader'])

        if len(err) > 0:
            logging.info("stderr: %s" % err)
        else:
            logging.info("Successfully compiled {0}".format(output_file_name))

    def _compile_js_bootloader(self, bundle_name, bootloader_settings):
        logging.info("_compile_js_bootloader called with cache_base_url {0}".format(
                get_cache_base_url()))
        context = { 'gatekeeper' : bootloader_settings['gatekeeper'],
                    'script_src': "{0}/js/{1}-inner.js".format(
                get_cache_base_url(), bundle_name) }
        template_name = "widget/bootloader.js"
        if "template" in bootloader_settings:
            template_name = bootloader_settings["template"]
        rendered = render_to_string(template_name, context)
        file_name = os.path.join(
            self.temp_dir, "js", "{0}.js".format(bundle_name))
        uncompiled_file_name = os.path.join(
            self.temp_dir, "js", "{0}-uncompiled.js".format(bundle_name))
        with open(uncompiled_file_name, 'w') as f:
            f.write(rendered)
        cmd_str = ("java -jar {0} --js {1} --js_output_file {2} "
                   "--compilation_level ADVANCED_OPTIMIZATIONS").format(
            COMPILER_PATH, uncompiled_file_name, file_name)
        call_command(cmd_str)
        os.remove(uncompiled_file_name)

    def compile_media_bundle(self, bundle_name, bundle_type, files):
        getattr(self, "compile_%s_bundle" % bundle_type)(bundle_name, bundle_type, files)

    def _create_temp_dir(self):
        commit_hash = LAST_COMMIT_GUID
        temp = os.path.join("/tmp", "static-%s-%s" % (commit_hash, time.time()))
        os.makedirs(temp)
        return temp

    def _copy_static_root_to_temp_dir(self):
        mr = settings.STATIC_ROOT
        for dirname in os.listdir(mr):
            original_path = os.path.join(mr, dirname)
            if os.path.isdir(original_path) and dirname not in SKIP_COPING_ON :
                dest =  os.path.join(self.temp_dir, dirname)
                if os.path.exists(dest):
                    shutil.rmtree(dest)
                shutil.copytree(original_path,
                         dest,
                         ignore=shutil.ignore_patterns(*SKIP_COPING_ON))
         # we need to copy all js, ideally this can be refactored in other libs

    def _output_embed_to_dir(self, output_dir, version=''):
        file_name = 'embed{0}.js'.format(version)
        context = widget.add_offsite_js_files(
            {'current_site': Site.objects.get_current(),
             'STATIC_URL': get_cache_base_url() +"/",
             'COMPRESS_MEDIA': settings.COMPRESS_MEDIA,
             "js_file": get_cache_base_url() +"/js/unisubs-offsite-compiled.js" })
        rendered = render_to_string(
            'widget/{0}'.format(file_name), context)
        with open(os.path.join(output_dir, file_name), 'w') as f:
            f.write(rendered)
            
    def _compile_conf_and_embed_js(self):
        """
        Compiles config.js, statwidgetconfig.js, and embed.js. These 
        are used to provide build-specific info (like media url and site url)
        to compiled js.
        """
        logging.info(("_compile_conf_and_embed_js with cache_base_url {0}").format(
                get_cache_base_url()))

        file_name = os.path.join(JS_LIB, 'js/config.js')

        context = {'current_site': Site.objects.get_current(),
                   'STATIC_URL': get_cache_base_url()+ "/",
                   'COMPRESS_MEDIA': settings.COMPRESS_MEDIA }
        rendered = render_to_string(
            'widget/config.js', context)
        with open(file_name, 'w') as f:
            f.write(rendered)
        logging.info("Compiled config to %s" % (file_name))
        self._output_embed_to_dir(settings.STATIC_ROOT)
        self._output_embed_to_dir(
            settings.STATIC_ROOT, settings.EMBED_JS_VERSION)
        for version in settings.PREVIOUS_EMBED_JS_VERSIONS:
            self._output_embed_to_dir(settings.STATIC_ROOT, version)

        file_name = os.path.join(JS_LIB, 'js/statwidget/statwidgetconfig.js')
        rendered = render_to_string(
            'widget/statwidgetconfig.js', context)
        with open(file_name, 'w') as f:
            f.write(rendered)    

    def _compile_media_bundles(self, restrict_bundles, args):
        bundles = settings.MEDIA_BUNDLES
        for bundle_name, data in bundles.items():
            if restrict_bundles and bundle_name not in args:
                continue
            self.compile_media_bundle(
                bundle_name, data['type'], data["files"])
    
    def _remove_cache_dirs_before(self, num_to_keep):
        """
        we remove all but the last export, since the build can fail at the next step
        in which case it will still need the previous build there
        """
        base = os.path.dirname(get_cache_dir())
        if not os.path.exists(os.path.join(os.getcwd(), "media/static-cache")):
            return 
        targets = [os.path.join(base, x) for x 
                   in sorted_ls("media/static-cache/")
                   if x.startswith(".") is False][:-num_to_keep]
        [shutil.rmtree(t) for t in targets ]

    def _copy_temp_dir_to_cache_dir(self):
        cache_dir = get_cache_dir()
        if os.path.exists(cache_dir):
            shutil.rmtree(cache_dir)
        for filename in os.listdir(self.temp_dir):
            shutil.move(os.path.join(self.temp_dir, filename), 
                        os.path.join(cache_dir, filename))

    def _copy_files_with_public_urls_from_cache_dir_to_static_dir(self):
        cache_dir = get_cache_dir()
        for file in NO_UNIQUE_URL:
            filename = file['name']
            from_path = os.path.join(cache_dir, filename)
            to_path =  os.path.join(settings.STATIC_ROOT, filename)
            if os.path.exists(to_path):
                os.remove(to_path)
            shutil.copyfile(from_path, to_path)

    def _make_mirosubs_copies_of_files_with_public_urls(self):
        # for backwards compatibilty with old mirosubs names
        for file in NO_UNIQUE_URL:
            filename = file['name']
            mirosubs_filename = re.sub(
                r'unisubs\-', 'mirosubs-',
                filename)
            if filename != mirosubs_filename:
                from_path = os.path.join(settings.STATIC_ROOT, filename)
                to_path = os.path.join(settings.STATIC_ROOT, mirosubs_filename)
                print("For backwards compatibility, copying from {0} to {1}".format(
                        from_path, to_path))
                shutil.copyfile(from_path, to_path)

    def handle(self, *args, **options):
        """
        There are three directories involved here:
        
        temp_dir: /tmp/static-[commit guid]-[time] This is the working dir
            for the compilation.
        MEDIA_ROOT: regular media root directory for django project
        cache_dir: STATIC_ROOT/static-cache/[commit guid] where compiled 
            media ends up
        """
        self.temp_dir = self._create_temp_dir()
        logging.info(("Starting static media compilation with "
                      "temp_dir {0} and cache_dir {1}").format(
                self.temp_dir, get_cache_dir()));
        self.verbosity = int(options.get('verbosity'))
        self.test_str_version = bool(options.get('test_str_version'))
        self.keeps_previous = bool(options.get('keeps_previous'))
        restrict_bundles = bool(args)

        os.chdir(settings.PROJECT_ROOT)
        self._copy_static_root_to_temp_dir() 
        self._compile_conf_and_embed_js()
        self._compile_media_bundles(restrict_bundles, args)
            
        if not self.keeps_previous:
            self._remove_cache_dirs_before(1)

        self._copy_temp_dir_to_cache_dir()
        self._copy_files_with_public_urls_from_cache_dir_to_static_dir()
        self._make_mirosubs_copies_of_files_with_public_urls()

        if self.test_str_version:
            self.test_string_version()

    def test_string_version(self):
        """
        Make sure all the compiled files have the version name appended
        """
        version_str = _make_version_debug_string()
        for file in NO_UNIQUE_URL:
            filename = file['name']
            # we only need compiled sutff (widgetizerprimer breaks the stable urls = compiled assumption
            if os.path.basename(filename) not in settings.MEDIA_BUNDLES.keys():
                continue
            to_path =  os.path.join(settings.STATIC_ROOT, filename)
            
            data = open(to_path).read()
            assert(data.endswith(version_str))
