from collections import OrderedDict
import os
from inspect import getsource
import StringIO
import glob
import logging
from hashlib import md5

from .compilers.scss import SCSS
from .compilers.closure import Closure
from .minifiers.yui import YUI

from .base import NullCompiler, NullMinifier, NullOutputter
from .outputters.blobstore import Blobstore
from .outputters.filesystem import Filesystem

COMPILERS = {}
MINIFIERS = {}
OUTPUTTERS = {}

COMPILERS_BY_EXTENSION = {}

def register_compiler(name, compiler_class, extensions=None):
    COMPILERS[name] = compiler_class
    if extensions:
        for ext in extensions:
            COMPILERS_BY_EXTENSION[ext.lstrip(".")] = compiler_class

def register_minifier(name, minifier_class):
    MINIFIERS[name] = minifier_class

def register_outputter(name, outputter_class):
    OUTPUTTERS[name] = outputter_class

register_compiler("null", NullCompiler)
register_minifier("null", NullMinifier)
register_outputter("null", NullOutputter)

register_compiler("scss", SCSS, [".scss"])
register_compiler("closure", Closure)

register_minifier("yui", YUI)

register_outputter("blobstore", Blobstore)
register_outputter("filesystem", Filesystem)

class Node(object):
    def __init__(self, parent):
        #the list of files (including wildcards) which we started with
        self.inputs = []

        #the expanded list of input files, with folder/* wildcards expanded
        self.input_files = []

        # filename:StringIO(contents) mappings of the files we will output
        # this is what gets modified in each step of the pipeline
        self.outputs = OrderedDict()

        self.parent = parent
        self.child = None
        if parent:
            parent.child = self

    def _root(self):
        if self.parent:
            return self.parent._root()
        return self

    def run(self):
        if self.any_dirty():
            logging.debug("PIPELINE: Running pipeline")
            self._root()._run()
        else:
            logging.error("PIPELINE: NOT running clean pipeline")

    def _run(self):
        #TODO: why do we need this?
        if self.parent:
            self.outputs = self.parent.outputs.copy()

        self.do_run()

        if self.child:
            self.child._run()

    def do_run(self):
        """ Alter self.outputs. """
        raise NotImplementedError()

    def is_dirty(self): raise NotImplementedError()

    def any_dirty(self):
        n = self._root()
        while n.child:
            if n.is_dirty():
                return True
            n = n.child

        return n.is_dirty()

    def update_hash(self, *args, **kwargs):
        parts = [ self.__class__.__name__ ] + [ str(x) for x in args ]
        for k in sorted(kwargs.keys()):
            parts.extend([k, str(kwargs[k]) ])

        hasher = md5()
        for part in parts:
            hasher.update(part)

        self.hash = hasher.hexdigest()


    #Generic methods which allow chaining of the nodes
    def Output(self, outputter, url_root=None, directory=None):
        from django.conf import settings
        url_root = url_root or settings.STATIC_URL
        return OutputNode(self, outputter, url_root, directory)

    def Minify(self, minifier):
        return MinifyNode(self, minifier)

    def Bundle(self, output_name):
        return BundleNode(self, output_name)

    def Compile(self, compiler_name=None, **kwargs):
        return CompileNode(self, compiler_name, **kwargs)



class OutputNode(Node):
    def __init__(self, parent, outputter_name, url_root, directory=None):
        super(OutputNode, self).__init__(parent)

        self.update_hash(parent, outputter_name, url_root, directory)

        self._root().url_root = url_root
        self.outputter = OUTPUTTERS[outputter_name](directory)

    def _build_output_filename(self, filename):
        hsh = self._root().pipeline_hash
        part, ext = os.path.splitext(filename)
        return ".".join([part, hsh, ext.lstrip(".")])

    def output_urls(self):
        return [ self._root().url_root + self._build_output_filename(x) for x in self.outputs.keys() ]

    def do_run(self):
        #OutputNode is the only type of node which does not set self.outputs
        for filename, contents in self.outputs.items():
            filename = self._build_output_filename(filename)
            self.outputter.output(filename, contents)


    def serve(self, filename):
        return self.outputter.serve(filename)

    def is_dirty(self):
        self._root().pipeline_hash = generate_pipeline_hash(self._root(), self._root().input_files, self._root().input_files_are_filenames)

        logging.error("PIPELINE HASH: %s", self._root().pipeline_hash)
        for f in self._root().outputs.keys():
            if not self.outputter.file_up_to_date(self._build_output_filename(f)):
                return True
        return False

class MinifyNode(Node):
    def __init__(self, parent, minifier):
        super(MinifyNode, self).__init__(parent)

        self.update_hash(parent, minifier)

        self.minifier_name = minifier

    def do_run(self):
        self.outputs = MINIFIERS[self.minifier_name]().minify(self.outputs)

    def is_dirty(self):
        return False

class BundleNode(Node):
    def __init__(self, parent, output_file_name):
        super(BundleNode, self).__init__(parent)
        self.update_hash(parent, output_file_name)
        self.output_file_name = output_file_name

    def do_run(self):
        """
            Concatenates the inputs into a single file
        """
        output = StringIO.StringIO()
        for contents in self.outputs.values():
            output.write(contents.read())
        output.seek(0) #Rewind to the beginning
        self.outputs = OrderedDict([(self.output_file_name, output)])

    def is_dirty(self):
        return False

class CompileNode(Node):
    def __init__(self, parent, compiler_name, **kwargs):
        super(CompileNode, self).__init__(parent)

        self.update_hash(parent, compiler_name, **kwargs)

        self.kwargs = kwargs
        self.compiler_name = compiler_name

    def do_run(self):
        if self.compiler_name:
            compiler = COMPILERS[self.compiler_name](**self.kwargs)
        else:
            filename, ext = os.path.splitext(self.outputs[0])
            ext = ext.lstrip(".")

            if ext in COMPILERS_BY_EXTENSION:
                compiler = COMPILERS_BY_EXTENSION[ext](**self.kwargs)
            else:
                raise ValueError("Couldn't find compiler for extension: '%s'" % ext)

        self.outputs = compiler.compile(self.outputs)

    def is_dirty(self):
        return False

def generate_pipeline_hash(root_node, inputs, filenames=True):
    """
        Returns a hash representing this pipeline combined with its inputs
    """
    hasher = md5()
    for inp in sorted(inputs):
        if filenames:
            u = str(os.path.getmtime(inp))
            logging.error("%s - %s", inp, u)
            hasher.update(u)
        else:
            hasher.update(inp)

    return hasher.hexdigest()

class Gather(Node):
    """ The starting node of every pipeline.  Picks up the specified
        files from the filesystem and puts them into self.outputs.
    """
    def __init__(self, inputs, filenames=True):
        super(Gather, self).__init__(None)

        self.update_hash(inputs, filenames)

        #Try to glob match the inputs
        expanded_inputs = [] #will contain the full list of files, not just folder/*
        for inp in inputs:
            if filenames:
                #Deal with wildcards which refer to directories
                matches = glob.glob(inp)
                if matches:
                    expanded_inputs.extend(matches)
                else:
                    expanded_inputs.append(inp)
            else:
                expanded_inputs.append(inp)

        self.input_files = expanded_inputs if filenames else inputs
        self.input_files_are_filenames = filenames
        self.pipeline_hash = generate_pipeline_hash(self, self.input_files, self.input_files_are_filenames)


    def do_run(self):
        """ Picks up file contents to start.
        """
        outputs = OrderedDict()
        for inp in self.input_files:
            if isinstance(inp, basestring):
                f = open(inp, "r")
                content = f.read()
                f.close()
            else:
                content = inp

            output = StringIO.StringIO()
            output.write(content)
            output.seek(0) #Rewind to the beginning
            outputs[inp] = output
        self.outputs = outputs

    def is_dirty(self):
        return False
