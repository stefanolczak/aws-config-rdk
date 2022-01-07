#    Copyright 2017-2021 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance with the License. A copy of the License is located at
#
#        http://aws.amazon.com/apache2.0/
#
#    or in the "license" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.
import argparse
import base64

import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from boto3 import session
import yaml
from builtins import input
from datetime import datetime
from os import path
import uuid
import boto3
import botocore
from botocore.exceptions import ClientError, EndpointConnectionError
from rdk.Util import Util
from rdk.commands import *

# sphinx-argparse is a delight.
try:
    from rdk import MY_VERSION
except ImportError:
    MY_VERSION = "<version>"
    pass


def get_command_parser():
    # This is needed to get sphinx to auto-generate the CLI documentation correctly.
    if "__version__" not in globals() and "__version__" not in locals():
        __version__ = "<version>"

    parser = argparse.ArgumentParser(
        # formatter_class=argparse.RawDescriptionHelpFormatter,
        description="The RDK is a command-line utility for authoring, deploying, and testing custom AWS Config rules."
    )
    parser.add_argument("-p", "--profile", help="[optional] indicate which Profile to use.")
    parser.add_argument("-k", "--access-key-id", help="[optional] Access Key ID to use.")
    parser.add_argument("-s", "--secret-access-key", help="[optional] Secret Access Key to use.")
    parser.add_argument("-r", "--region", help="Select the region to run the command in.")
    parser.add_argument(
        "-f",
        "--region-file",
        help="[optional] File to specify which regions to run the command in parallel. Supported for init, deploy, and undeploy.",
    )
    parser.add_argument(
        "--region-set",
        help="[optional] Set of regions within the region file with which to run the command in parallel. Looks for a 'default' region set if not specified.",
    )
    # parser.add_argument('--verbose','-v', action='count')
    # Removed for now from command choices: 'test-remote', 'status'
    parser.add_argument(
        "command",
        metavar="<command>",
        help="Command to run.  Refer to the usage instructions for each command for more details",
        choices=[
            "clean",
            "create",
            "create-region-set",
            "create-rule-template",
            "deploy",
            "deploy-organization",
            "export",
            "init",
            "logs",
            "modify",
            "rulesets",
            "sample-ci",
            "test-local",
            "undeploy",
            "undeploy-organization",
        ],
    )
    parser.add_argument(
        "command_args",
        metavar="<command arguments>",
        nargs=argparse.REMAINDER,
        help="Run `rdk <command> --help` to see command-specific arguments.",
    )
    parser.add_argument(
        "-v",
        "--version",
        help="Display the version of this tool",
        action="version",
        version="%(prog)s " + MY_VERSION,
    )

    return parser


def parse_region_file(args):
    region_set = "default"
    if args.region_set:
        region_set = args.region_set
    try:
        region_text = yaml.safe_load(open(args.region_file, "r"))
        return region_text[region_set]
    except Exception:
        raise SyntaxError(f"Error reading regions: {region_set} in file: {args.region_file}")


def run_multi_region(args):
    my_rdk = rdk(args)
    return_val = my_rdk.process_command()
    return return_val


class rdk:
    def __init__(self, args):
        self.args = args

    @staticmethod
    def get_command_parser(self):
        return get_command_parser()

    def process_command(self):
        command = eval(self.args.command.replace("-", "_"))
        exit_code = command(self.args)

        return exit_code

    """
    These are never used in the old code, find their intended purpose and create
    #TODO
    def test_remote(self):
        print("Running test_remote!")
        self.__parse_test_args()

        # Construct our list of rules to test.
        rule_names = self.__get_rule_list_for_command()

        # Create our Lambda client.
        my_session = self.__get_boto_session()
        my_lambda_client = my_session.client("lambda")

        for rule_name in rule_names:
            print("Testing " + rule_name)

            # Get CI JSON from either the CLI or one of the stored templates.
            my_cis = self.__get_test_CIs(rule_name)

            my_parameters = {}
            if self.args.test_parameters:
                my_parameters = json.loads(self.args.test_parameters)

            for my_ci in my_cis:
                print("\t\tTesting CI " + my_ci["resourceType"])

                # Generate test event from templates
                test_event = json.load(
                    open(
                        os.path.join(path.dirname(__file__), "template", EVENT_TEMPLATE_FILENAME),
                        "r",
                    ),
                    strict=False,
                )
                my_invoking_event = json.loads(test_event["invokingEvent"])
                my_invoking_event["configurationItem"] = my_ci
                my_invoking_event["notificationCreationTime"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
                test_event["invokingEvent"] = json.dumps(my_invoking_event)
                test_event["ruleParameters"] = json.dumps(my_parameters)

                # Get the Lambda function associated with the Rule
                stack_name = self.__get_stack_name_from_rule_name(rule_name)
                my_lambda_arn = self.__get_lambda_arn_for_stack(stack_name)

                # Call Lambda function with test event.
                result = my_lambda_client.invoke(
                    FunctionName=my_lambda_arn,
                    InvocationType="RequestResponse",
                    LogType="Tail",
                    Payload=json.dumps(test_event),
                )

                # If there's an error dump execution logs to the terminal, if not print out the value returned by the lambda function.
                if "FunctionError" in result:
                    print(base64.b64decode(str(result["LogResult"])))
                else:
                    print("\t\t\t" + result["Payload"].read())
                    if self.args.verbose:
                        print(base64.b64decode(str(result["LogResult"])))
        return 0
    #TODO
    def status(self):
        print("Running status!")
        return 0
    #TODO
    def create_terraform_template(self):
        self.args = get_create_rule_template_parser().parse_args(self.args.command_args, self.args)

        if self.args.rulesets:
            self.args.rulesets = self.args.rulesets.split(",")

        print("Generating Terraform template!")

        template = self.__generate_terraform_shell(self.args)

        rule_names = self.__get_rule_list_for_command()

        for rule_name in rule_names:
            rule_input_params = self.__generate_rule_terraform_params(rule_name)
            rule_def = self.__generate_rule_terraform(rule_name)
            template.append(rule_input_params)
            template.append(rule_def)

        output_file = open(self.args.output_file, "w")
        output_file.write(json.dumps(template, indent=2))
        print("CloudFormation template written to " + self.args.output_file)
    #TODO
    def __parse_test_args(self):
        self.args = get_test_parser(self.args.command).parse_args(self.args.command_args, self.args)

        if self.args.all and self.args.rulename:
            print("You may specify either specific rules or --all, but not both.")
            return 1

        if self.args.rulesets:
            self.args.rulesets = self.args.rulesets.split(",")

        return self.args
    """
