from collections import OrderedDict
import StringIO
from ..base import Processor


class Bundle(Processor):

    def __init__(self,  output_file_name):
        self.output_file_name = output_file_name

    def modify_expected_output_filenames(self, filenames):
        return [self.output_file_name]

    def process(self, inputs):
        """
            Concatenates the inputs into a single file
        """
        output = StringIO.StringIO()
        for contents in inputs.values():
            output.write(contents.read())
        output.seek(0) #Rewind to the beginning
        return OrderedDict([(self.output_file_name, output)])

