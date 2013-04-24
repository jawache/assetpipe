from collections import OrderedDict
import os
import StringIO
from django.conf import settings
from ..base import Processor

class ClosureBuilder(Processor):
    def __init__(self, pipeline, namespaces):
        super(ClosureBuilder, self).__init__(pipeline)
        self.namespaces = namespaces

    def modify_expected_output_filenames(self, filenames):
        #TODO: modify these here
        return filenames

    def process(self, inputs):
        """
            Runs the closurebuilder.py script to generate a single JS file.
        """
        assert(self.namespaces)
        assert(inputs)

        from subprocess import Popen, PIPE

        outputs = OrderedDict()

        builder = settings.CLOSURE_BUILDER_BINARY
        try:
            command = ['python', builder, "--output_mode", "script" ]

            closure_dir = os.path.dirname(os.path.dirname(os.path.dirname(builder)))
            closure_deps = "%s/goog/deps.js" % closure_dir
            assert(os.path.exists(closure_deps))

            command.extend([ "-f", "--js", closure_deps ])
            command.extend(["--root", os.path.dirname(closure_dir)])

            for n in self.namespaces:
                command.extend(['--namespace', n])

            command.extend(inputs.keys())

            cmd = Popen(
                command, stdin=PIPE, stdout=PIPE, stderr=PIPE,
                universal_newlines=True
            )
            output, error = cmd.communicate()
            assert cmd.wait() == 0, 'Command returned bad result:\n%s' % error

            file_out = StringIO.StringIO()
            file_out.write(output)
            file_out.seek(0)

        except Exception, e:
            raise ValueError("Failed to execute closure-builder. "
                    "Please make sure that you have installed Python "
                    "and that it's in your PATH and that you've configured "
                    "CLOSURE_BUILDER_BINARY in your settings correctly.\n"
                    "Error was: %s" % e)

        #Closure builder only ever returns 1 file
        filename = inputs.keys()[0]
        outputs[filename] = file_out
        return outputs