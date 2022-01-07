import argparse
import json

from rdk import ACCEPTED_RESOURCE_TYPES
from rdk.datatypes.TestCI import TestCI


def sample_ci(self):
    args = get_sample_ci_parser().parse_args(args.command_args, args)

    my_test_ci = TestCI(args.ci_type)
    print(json.dumps(my_test_ci.get_json(), indent=4))


def get_sample_ci_parser():
    parser = argparse.ArgumentParser(
        prog="rdk sample-ci",
        description="Provides a way to see sample configuration items for most supported resource types.",
    )
    parser.add_argument(
        "ci_type",
        metavar="<resource type>",
        help='Resource name (e.g. "AWS::EC2::Instance") to display a sample CI JSON document for.',
        choices=ACCEPTED_RESOURCE_TYPES,
    )
    return parser
