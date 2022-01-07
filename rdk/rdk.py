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
from rdk.commands.Init import Init
from rdk.commands.Clean import Clean
from rdk.commands.Create import Create
from rdk.commands.Deploy import Deploy
from rdk.commands.CreateRegionSet import CreateRegionSet
from rdk.commands.CreateRuleTemplate import CreateRuleTemplate
from rdk.commands.Export import Export
from rdk.datatypes.util import util
from rdk.datatypes.TestCI import TestCI


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


def get_clean_parser():
    parser = argparse.ArgumentParser(
        prog="rdk clean",
        description="Removes AWS Config from the account.  This will disable all Config rules and no configuration changes will be recorded!",
    )
    parser.add_argument(
        "--force",
        required=False,
        action="store_true",
        help="[optional] Clean account without prompting for confirmation.",
    )

    return parser


def get_create_parser():
    return get_rule_parser(True, "create")


def get_modify_parser():
    return get_rule_parser(False, "modify")


def get_rule_parser(is_required, command):
    usage_string = "[--runtime <runtime>] [--resource-types <resource types>] [--maximum-frequency <max execution frequency>] [--input-parameters <parameter JSON>] [--tags <tags JSON>] [--rulesets <RuleSet tags>]"

    if is_required:
        usage_string = "[ --resource-types <resource types> | --maximum-frequency <max execution frequency> ] [optional configuration flags] [--runtime <runtime>] [--rulesets <RuleSet tags>]"

    parser = argparse.ArgumentParser(
        prog="rdk " + command,
        usage="rdk " + command + " <rulename> " + usage_string,
        description="Rules are stored in their own directory along with their metadata.  This command is used to "
        + command
        + " the Rule and metadata.",
    )
    parser.add_argument("rulename", metavar="<rulename>", help="Rule name to create/modify")
    runtime_group = parser.add_mutually_exclusive_group()
    runtime_group.add_argument(
        "-R",
        "--runtime",
        required=False,
        help="Runtime for lambda function",
        choices=[
            "nodejs4.3",
            "java8",
            "python3.6",
            "python3.6-lib",
            "python3.7",
            "python3.7-lib",
            "python3.8",
            "python3.8-lib",
            "python3.9",
            "python3.9-lib",
            "dotnetcore1.0",
            "dotnetcore2.0",
        ],
        metavar="",
    )
    runtime_group.add_argument(
        "--source-identifier",
        required=False,
        help="[optional] Used only for creating Managed Rules.",
    )
    parser.add_argument(
        "-l",
        "--custom-lambda-name",
        required=False,
        help="[optional] Provide custom lambda name",
    )
    parser.set_defaults(runtime="python3.6-lib")
    parser.add_argument(
        "-r",
        "--resource-types",
        required=False,
        help="[optional] Resource types that will trigger event-based Rule evaluation",
    )
    parser.add_argument(
        "-m",
        "--maximum-frequency",
        required=False,
        help="[optional] Maximum execution frequency for scheduled Rules",
        choices=[
            "One_Hour",
            "Three_Hours",
            "Six_Hours",
            "Twelve_Hours",
            "TwentyFour_Hours",
        ],
    )
    parser.add_argument(
        "-i",
        "--input-parameters",
        help="[optional] JSON for required Config parameters.",
    )
    parser.add_argument("--optional-parameters", help="[optional] JSON for optional Config parameters.")
    parser.add_argument(
        "--tags",
        help="[optional] JSON for tags to be applied to all CFN created resources.",
    )
    parser.add_argument(
        "-s",
        "--rulesets",
        required=False,
        help="[optional] comma-delimited list of RuleSet names to add this Rule to.",
    )
    parser.add_argument(
        "--remediation-action",
        required=False,
        help="[optional] SSM document for remediation.",
    )
    parser.add_argument(
        "--remediation-action-version",
        required=False,
        help="[optional] SSM document version for remediation action.",
    )
    parser.add_argument(
        "--auto-remediate",
        action="store_true",
        required=False,
        help="[optional] Set the SSM remediation to trigger automatically.",
    )
    parser.add_argument(
        "--auto-remediation-retry-attempts",
        required=False,
        help="[optional] Number of times to retry automated remediation.",
    )
    parser.add_argument(
        "--auto-remediation-retry-time",
        required=False,
        help="[optional] Duration of automated remediation retries.",
    )
    parser.add_argument(
        "--remediation-concurrent-execution-percent",
        required=False,
        help="[optional] Concurrent execution rate of the SSM document for remediation.",
    )
    parser.add_argument(
        "--remediation-error-rate-percent",
        required=False,
        help='[optional] Error rate that will mark the batch as "failed" for SSM remediation execution.',
    )
    parser.add_argument(
        "--remediation-parameters",
        required=False,
        help="[optional] JSON-formatted string of additional parameters required by the SSM document.",
    )
    parser.add_argument(
        "--automation-document",
        required=False,
        help="[optional, beta] JSON-formatted string of the SSM Automation Document.",
    )
    parser.add_argument(
        "--skip-supported-resource-check",
        required=False,
        action="store_true",
        help="[optional] Skip the check for whether the resource type is supported or not.",
    )

    return parser


def get_undeploy_parser():
    return get_deployment_parser(ForceArgument=True, Command="undeploy")


def get_undeploy_organization_parser():
    return get_deployment_organization_parser(ForceArgument=True, Command="undeploy")


def get_deploy_parser():
    return get_deployment_parser()


def get_deployment_parser(ForceArgument=False, Command="deploy"):
    direction = "to"
    if Command == "undeploy":
        direction = "from"

    parser = argparse.ArgumentParser(
        prog="rdk " + Command,
        description="Used to " + Command + " the Config Rule " + direction + " the target account.",
    )
    parser.add_argument(
        "rulename",
        metavar="<rulename>",
        nargs="*",
        help="Rule name(s) to deploy.  Rule(s) will be pushed to AWS.",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="All rules in the working directory will be deployed.",
    )
    parser.add_argument("-s", "--rulesets", required=False, help="comma-delimited list of RuleSet names")
    parser.add_argument(
        "-f",
        "--functions-only",
        action="store_true",
        required=False,
        help="[optional] Only deploy Lambda functions.  Useful for cross-account deployments.",
    )
    parser.add_argument(
        "--stack-name",
        required=False,
        help='[optional] CloudFormation Stack name for use with --functions-only option.  If omitted, "RDK-Config-Rule-Functions" will be used.',
    )
    parser.add_argument(
        "--custom-code-bucket",
        required=False,
        help="[optional] Provide the custom code S3 bucket name, which is not created with rdk init, for generated cloudformation template storage.",
    )
    parser.add_argument(
        "--rdklib-layer-arn",
        required=False,
        help="[optional] Lambda Layer ARN that contains the desired rdklib.  Note that Lambda Layers are region-specific.",
    )
    parser.add_argument(
        "--lambda-role-arn",
        required=False,
        help='[optional] Assign existing iam role to lambda functions. If omitted, "rdkLambdaRole" will be created.',
    )
    parser.add_argument(
        "--lambda-role-name",
        required=False,
        help="[optional] Assign existing iam role to lambda functions. If added, will look for a lambda role in the current account with the given name",
    )
    parser.add_argument(
        "--lambda-layers",
        required=False,
        help="[optional] Comma-separated list of Lambda Layer ARNs to deploy with your Lambda function(s).",
    )
    parser.add_argument(
        "--lambda-subnets",
        required=False,
        help="[optional] Comma-separated list of Subnets to deploy your Lambda function(s).",
    )
    parser.add_argument(
        "--lambda-security-groups",
        required=False,
        help="[optional] Comma-separated list of Security Groups to deploy with your Lambda function(s).",
    )
    parser.add_argument(
        "--lambda-timeout",
        required=False,
        default=60,
        help="[optional] Timeout (in seconds) for the lambda function",
        type=str,
    )
    parser.add_argument(
        "--boundary-policy-arn",
        required=False,
        help='[optional] Boundary Policy ARN that will be added to "rdkLambdaRole".',
    )
    parser.add_argument(
        "-g",
        "--generated-lambda-layer",
        required=False,
        action="store_true",
        help="[optional] Forces rdk deploy to use the Python(3.6-lib,3.7-lib,3.8-lib,) lambda layer generated by rdk init --generate-lambda-layer",
    )
    parser.add_argument(
        "--custom-layer-name",
        required=False,
        default="rdklib-layer",
        action="store_true",
        help='[optional] To use with --generated-lambda-layer, forces the flag to look for a specific lambda-layer name. If omitted, "rdklib-layer" will be used',
    )

    if ForceArgument:
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="[optional] Remove selected Rules from account without prompting for confirmation.",
        )
    return parser


def get_deployment_organization_parser(ForceArgument=False, Command="deploy-organization"):
    direction = "to"
    if Command == "undeploy":
        direction = "from"

    parser = argparse.ArgumentParser(
        prog="rdk " + Command,
        description="Used to " + Command + " the Config Rule " + direction + " the target Organization.",
    )
    parser.add_argument(
        "rulename",
        metavar="<rulename>",
        nargs="*",
        help="Rule name(s) to deploy.  Rule(s) will be pushed to AWS.",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="All rules in the working directory will be deployed.",
    )
    parser.add_argument("-s", "--rulesets", required=False, help="comma-delimited list of RuleSet names")
    parser.add_argument(
        "-f",
        "--functions-only",
        action="store_true",
        required=False,
        help="[optional] Only deploy Lambda functions.  Useful for cross-account deployments.",
    )
    parser.add_argument(
        "--stack-name",
        required=False,
        help='[optional] CloudFormation Stack name for use with --functions-only option.  If omitted, "RDK-Config-Rule-Functions" will be used.',
    )
    parser.add_argument(
        "--custom-code-bucket",
        required=False,
        help="[optional] Provide the custom code S3 bucket name, which is not created with rdk init, for generated cloudformation template storage.",
    )
    parser.add_argument(
        "--rdklib-layer-arn",
        required=False,
        help="[optional] Lambda Layer ARN that contains the desired rdklib.  Note that Lambda Layers are region-specific.",
    )
    parser.add_argument(
        "--lambda-role-arn",
        required=False,
        help='[optional] Assign existing iam role to lambda functions. If omitted, "rdkLambdaRole" will be created.',
    )
    parser.add_argument(
        "--lambda-role-name",
        required=False,
        help="[optional] Assign existing iam role to lambda functions. If added, will look for a lambda role in the current account with the given name",
    )
    parser.add_argument(
        "--lambda-layers",
        required=False,
        help="[optional] Comma-separated list of Lambda Layer ARNs to deploy with your Lambda function(s).",
    )
    parser.add_argument(
        "--lambda-subnets",
        required=False,
        help="[optional] Comma-separated list of Subnets to deploy your Lambda function(s).",
    )
    parser.add_argument(
        "--lambda-security-groups",
        required=False,
        help="[optional] Comma-separated list of Security Groups to deploy with your Lambda function(s).",
    )
    parser.add_argument(
        "--lambda-timeout",
        required=False,
        default=60,
        help="[optional] Timeout (in seconds) for the lambda function",
        type=str,
    )
    parser.add_argument(
        "--boundary-policy-arn",
        required=False,
        help='[optional] Boundary Policy ARN that will be added to "rdkLambdaRole".',
    )

    if ForceArgument:
        parser.add_argument(
            "--force",
            required=False,
            action="store_true",
            help="[optional] Remove selected Rules from account without prompting for confirmation.",
        )
    return parser


def get_export_parser(ForceArgument=False, Command="export"):

    parser = argparse.ArgumentParser(
        prog="rdk " + Command,
        description="Used to " + Command + " the Config Rule to terraform file.",
    )
    parser.add_argument(
        "rulename",
        metavar="<rulename>",
        nargs="*",
        help="Rule name(s) to export to a file.",
    )
    parser.add_argument("-s", "--rulesets", required=False, help="comma-delimited list of RuleSet names")
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="All rules in the working directory will be deployed.",
    )
    parser.add_argument(
        "--lambda-layers",
        required=False,
        help="[optional] Comma-separated list of Lambda Layer ARNs to deploy with your Lambda function(s).",
    )
    parser.add_argument(
        "--lambda-subnets",
        required=False,
        help="[optional] Comma-separated list of Subnets to deploy your Lambda function(s).",
    )
    parser.add_argument(
        "--lambda-security-groups",
        required=False,
        help="[optional] Comma-separated list of Security Groups to deploy with your Lambda function(s).",
    )
    parser.add_argument(
        "--lambda-timeout",
        required=False,
        default=60,
        help="[optional] Timeout (in seconds) for the lambda function",
        type=str,
    )
    parser.add_argument(
        "--lambda-role-arn",
        required=False,
        help="[optional] Assign existing iam role to lambda functions. If omitted, new lambda role will be created.",
    )
    parser.add_argument(
        "--lambda-role-name",
        required=False,
        help="[optional] Assign existing iam role to lambda functions. If added, will look for a lambda role in the current account with the given name",
    )
    parser.add_argument(
        "--rdklib-layer-arn",
        required=False,
        help="[optional] Lambda Layer ARN that contains the desired rdklib.  Note that Lambda Layers are region-specific.",
    )
    parser.add_argument(
        "-v",
        "--version",
        required=True,
        help="Terraform version",
        choices=["0.11", "0.12"],
    )
    parser.add_argument("-f", "--format", required=True, help="Export Format", choices=["terraform"])
    parser.add_argument(
        "-g",
        "--generated-lambda-layer",
        required=False,
        action="store_true",
        help="[optional] Forces rdk deploy to use the Python(3.6-lib,3.7-lib,3.8-lib,) lambda layer generated by rdk init --generate-lambda-layer",
    )
    parser.add_argument(
        "--custom-layer-name",
        required=False,
        action="store_true",
        help='[optional] To use with --generated-lambda-layer, forces the flag to look for a specific lambda-layer name. If omitted, "rdklib-layer" will be used',
    )

    return parser


def get_test_parser(command):
    parser = argparse.ArgumentParser(prog="rdk " + command, description="Used to run tests on your Config Rule code.")
    parser.add_argument(
        "rulename",
        metavar="<rulename>[,<rulename>,...]",
        nargs="*",
        help="Rule name(s) to test",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Test will be run against all rules in the working directory.",
    )
    parser.add_argument("--test-ci-json", "-j", help="[optional] JSON for test CI for testing.")
    parser.add_argument("--test-ci-types", "-t", help="[optional] CI type to use for testing.")
    parser.add_argument("--verbose", "-v", action="store_true", help="[optional] Enable full log output")
    parser.add_argument(
        "-s",
        "--rulesets",
        required=False,
        help="[optional] comma-delimited list of RuleSet names",
    )
    return parser


def get_test_local_parser():
    return get_test_parser("test-local")


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


def get_logs_parser():
    parser = argparse.ArgumentParser(
        prog="rdk logs",
        usage="rdk logs <rulename> [-n/--number NUMBER] [-f/--follow]",
        description="Displays CloudWatch logs for the Lambda Function for the specified Rule.",
    )
    parser.add_argument("rulename", metavar="<rulename>", help="Rule whose logs will be displayed")
    parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="[optional] Continuously poll Lambda logs and write to stdout.",
    )
    parser.add_argument(
        "-n",
        "--number",
        default=3,
        help="[optional] Number of previous logged events to display.",
    )
    return parser


def get_rulesets_parser():
    parser = argparse.ArgumentParser(
        prog="rdk rulesets",
        usage="rdk rulesets [list | [ [ add | remove ] <ruleset> <rulename> ]",
        description="Used to describe and manipulate RuleSet tags on Rules.",
    )
    parser.add_argument("subcommand", help="One of list, add, or remove")
    parser.add_argument("ruleset", nargs="?", help="Name of RuleSet")
    parser.add_argument("rulename", nargs="?", help="Name of Rule to be added or removed")
    return parser


def get_create_rule_template_parser():
    parser = argparse.ArgumentParser(
        prog="rdk create-rule-template",
        description="Outputs a CloudFormation template that can be used to deploy Config Rules in other AWS Accounts.",
    )
    parser.add_argument(
        "rulename",
        metavar="<rulename>",
        nargs="*",
        help="Rule name(s) to include in template.  A CloudFormation template will be created, but Rule(s) will not be pushed to AWS.",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="All rules in the working directory will be included in the generated CloudFormation template.",
    )
    parser.add_argument(
        "-s",
        "--rulesets",
        required=False,
        help="comma-delimited RuleSet names to be included in the generated template.",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        required=True,
        default="RDK-Config-Rules",
        help="filename of generated CloudFormation template",
    )
    parser.add_argument(
        "-t",
        "--tag-config-rules-script",
        required=False,
        help="filename of generated script to tag config rules with the tags in each parameter.json",
    )
    parser.add_argument(
        "--config-role-arn",
        required=False,
        help='[optional] Assign existing iam role as config role. If omitted, "config-role" will be created.',
    )
    parser.add_argument(
        "--rules-only",
        action="store_true",
        help="[optional] Generate a CloudFormation Template that only includes the Config Rules and not the Bucket, Configuration Recorder, and Delivery Channel.",
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
        command = eval("".join([x.capitalize() for x in self.args.command.split("-",)]))
        exit_code = command(self.args).run()

        return exit_code

    def modify(self):
        # Parse the command-line arguments necessary for modifying a Config Rule.
        self.__parse_rule_args(False)

        print("Running modify!")

        self.args.rulename = self.__clean_rule_name(self.args.rulename)

        # Get existing parameters
        old_params, tags = self.__get_rule_parameters(self.args.rulename)

        if not self.args.custom_lambda_name and "CustomLambdaName" in old_params:
            self.args.custom_lambda_name = old_params["CustomLambdaName"]

        if not self.args.resource_types and "SourceEvents" in old_params:
            self.args.resource_types = old_params["SourceEvents"]

        if not self.args.maximum_frequency and "SourcePeriodic" in old_params:
            self.args.maximum_frequency = old_params["SourcePeriodic"]

        if not self.args.runtime and old_params["SourceRuntime"]:
            self.args.runtime = old_params["SourceRuntime"]

        if not self.args.input_parameters and "InputParameters" in old_params:
            self.args.input_parameters = old_params["InputParameters"]

        if not self.args.optional_parameters and "OptionalParameters" in old_params:
            self.args.optional_parameters = old_params["OptionalParameters"]

        if not self.args.source_identifier and "SourceIdentifier" in old_params:
            self.args.source_identifier = old_params["SourceIdentifier"]

        if not self.args.tags and tags:
            self.args.tags = tags

        if not self.args.remediation_action and "Remediation" in old_params:
            params = old_params["Remediation"]
            self.args.auto_remediate = params.get("Automatic", "")
            execution_controls = params.get("ExecutionControls", "")
            if execution_controls:
                ssm_controls = execution_controls["SsmControls"]
                self.args.remediation_concurrent_execution_percent = ssm_controls.get(
                    "ConcurrentExecutionRatePercentage", ""
                )
                self.args.remediation_error_rate_percent = ssm_controls.get("ErrorPercentage", "")
            self.args.remediation_parameters = json.dumps(params["Parameters"]) if params.get("Parameters") else None
            self.args.auto_remediation_retry_attempts = params.get("MaximumAutomaticAttempts", "")
            self.args.auto_remediation_retry_time = params.get("RetryAttemptSeconds", "")
            self.args.remediation_action = params.get("TargetId", "")
            self.args.remediation_action_version = params.get("TargetVersion", "")

        if "RuleSets" in old_params:
            if not self.args.rulesets:
                self.args.rulesets = old_params["RuleSets"]

        # Write the parameters to a file in the rule directory.
        self.__populate_params()

        print("Modified Rule '" + self.args.rulename + "'.  Use the `deploy` command to push your changes to AWS.")

    def undeploy(self):
        self.__parse_deploy_args(ForceArgument=True)

        if not self.args.force:
            confirmation = False
            while not confirmation:
                my_input = input("Delete specified Rules and Lambda Functions from your AWS Account? (y/N): ")
                if my_input.lower() == "y":
                    confirmation = True
                if my_input.lower() == "n" or my_input == "":
                    sys.exit(0)

        # get the rule names
        rule_names = self.__get_rule_list_for_command()

        # create custom session based on whatever credentials are available to us.
        my_session = self.__get_boto_session()

        print(f"[{my_session.region_name}]: Running un-deploy!")

        # Collect a list of all of the CloudFormation templates that we delete.  We'll need it at the end to make sure everything worked.
        deleted_stacks = []

        cfn_client = my_session.client("cloudformation")

        if self.args.functions_only:
            try:
                cfn_client.delete_stack(StackName=self.args.stack_name)
                deleted_stacks.append(self.args.stack_name)
            except ClientError as ce:
                print(
                    f"[{my_session.region_name}]: Client Error encountered attempting to delete CloudFormation stack for Lambda Functions: "
                    + str(ce)
                )
            except Exception as e:
                print(
                    f"[{my_session.region_name}]: Exception encountered attempting to delete CloudFormation stack for Lambda Functions: "
                    + str(e)
                )

            return

        for rule_name in rule_names:
            try:
                cfn_client.delete_stack(StackName=self.__get_stack_name_from_rule_name(rule_name))
                deleted_stacks.append(self.__get_stack_name_from_rule_name(rule_name))
            except ClientError as ce:
                print(
                    f"[{my_session.region_name}]: Client Error encountered attempting to delete CloudFormation stack for Rule: "
                    + str(ce)
                )
            except Exception as e:
                print(
                    f"[{my_session.region_name}]: Exception encountered attempting to delete CloudFormation stack for Rule: "
                    + str(e)
                )

        print(f"[{my_session.region_name}]: Rule removal initiated. Waiting for Stack Deletion to complete.")

        for stack_name in deleted_stacks:
            self.__wait_for_cfn_stack(cfn_client, stack_name)

        print(f"[{my_session.region_name}]: Rule removal complete, but local files have been preserved.")
        print(f"[{my_session.region_name}]: To re-deploy, use the 'deploy' command.")

    def undeploy_organization(self):
        self.__parse_deploy_args(ForceArgument=True)

        if not self.args.force:
            confirmation = False
            while not confirmation:
                my_input = input("Delete specified Rules and Lambda Functions from your Organization? (y/N): ")
                if my_input.lower() == "y":
                    confirmation = True
                if my_input.lower() == "n" or my_input == "":
                    sys.exit(0)

        # get the rule names
        rule_names = self.__get_rule_list_for_command()

        print("Running Organization un-deploy!")

        # create custom session based on whatever credentials are available to us.
        my_session = self.__get_boto_session()

        # Collect a list of all of the CloudFormation templates that we delete.  We'll need it at the end to make sure everything worked.
        deleted_stacks = []

        cfn_client = my_session.client("cloudformation")

        if self.args.functions_only:
            try:
                cfn_client.delete_stack(StackName=self.args.stack_name)
                deleted_stacks.append(self.args.stack_name)
            except ClientError as ce:
                print(
                    "Client Error encountered attempting to delete CloudFormation stack for Lambda Functions: "
                    + str(ce)
                )
            except Exception as e:
                print("Exception encountered attempting to delete CloudFormation stack for Lambda Functions: " + str(e))

            return

        for rule_name in rule_names:
            try:
                cfn_client.delete_stack(StackName=self.__get_stack_name_from_rule_name(rule_name))
                deleted_stacks.append(self.__get_stack_name_from_rule_name(rule_name))
            except ClientError as ce:
                print("Client Error encountered attempting to delete CloudFormation stack for Rule: " + str(ce))
            except Exception as e:
                print("Exception encountered attempting to delete CloudFormation stack for Rule: " + str(e))

        print("Rule removal initiated. Waiting for Stack Deletion to complete.")

        for stack_name in deleted_stacks:
            self.__wait_for_cfn_stack(cfn_client, stack_name)

        print("Rule removal complete, but local files have been preserved.")
        print("To re-deploy, use the 'deploy-organization' command.")

    def deploy(self):
        self.__parse_deploy_args()

        # get the rule names
        rule_names = self.__get_rule_list_for_command()

        # run the deploy code
        print(f"[{self.args.region}]: Running deploy!")

        # create custom session based on whatever credentials are available to us
        my_session = self.__get_boto_session()

        # get accountID
        identity_details = self.__get_caller_identity_details(my_session)
        account_id = identity_details["account_id"]
        partition = identity_details["partition"]

        if self.args.custom_code_bucket:
            code_bucket_name = self.args.custom_code_bucket
        else:
            code_bucket_name = CODE_BUCKET_PREFIX + account_id + "-" + my_session.region_name

        # If we're only deploying the Lambda functions (and role + permissions), branch here.  Someday the "main" execution path should use the same generated CFN templates for single-account deployment.
        if self.args.functions_only:
            # Generate the template
            function_template = self.__create_function_cloudformation_template()

            # Generate CFN parameter json
            cfn_params = [
                {
                    "ParameterKey": "SourceBucket",
                    "ParameterValue": code_bucket_name,
                }
            ]

            # Write template to S3
            my_s3_client = my_session.client("s3")
            my_s3_client.put_object(
                Body=bytes(function_template.encode("utf-8")),
                Bucket=code_bucket_name,
                Key=self.args.stack_name + ".json",
            )

            # Package code and push to S3
            s3_code_objects = {}
            for rule_name in rule_names:
                rule_params, cfn_tags = self.__get_rule_parameters(rule_name)
                if "SourceIdentifier" in rule_params:
                    print(f"[{my_session.region_name}]: Skipping code packaging for Managed Rule.")
                else:
                    s3_dst = self.__upload_function_code(
                        rule_name, rule_params, account_id, my_session, code_bucket_name
                    )
                    s3_code_objects[rule_name] = s3_dst

            my_cfn = my_session.client("cloudformation")

            # Generate the template_url regardless of region using the s3 sdk
            config = my_s3_client._client_config
            config.signature_version = botocore.UNSIGNED
            template_url = boto3.client("s3", config=config).generate_presigned_url(
                "get_object",
                ExpiresIn=0,
                Params={
                    "Bucket": code_bucket_name,
                    "Key": self.args.stack_name + ".json",
                },
            )

            # Check if stack exists.  If it does, update it.  If it doesn't, create it.

            try:
                my_stack = my_cfn.describe_stacks(StackName=self.args.stack_name)

                # If we've gotten here, stack exists and we should update it.
                print(f"[{my_session.region_name}]: Updating CloudFormation Stack for Lambda functions.")
                try:

                    cfn_args = {
                        "StackName": self.args.stack_name,
                        "TemplateURL": template_url,
                        "Parameters": cfn_params,
                        "Capabilities": ["CAPABILITY_IAM"],
                    }

                    # If no tags key is specified, or if the tags dict is empty
                    if cfn_tags is not None:
                        cfn_args["Tags"] = cfn_tags

                    response = my_cfn.update_stack(**cfn_args)

                    # wait for changes to propagate.
                    self.__wait_for_cfn_stack(my_cfn, self.args.stack_name)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ValidationError":
                        if "No updates are to be performed." in str(e):
                            # No changes made to Config rule definition, so CloudFormation won't do anything.
                            print(f"[{my_session.region_name}]: No changes to Config Rule configurations.")
                        else:
                            # Something unexpected has gone wrong.  Emit an error and bail.
                            print(e)
                            return 1
                    else:
                        raise

                # Push lambda code to functions.
                for rule_name in rule_names:
                    rule_params, cfn_tags = self.__get_rule_parameters(rule_name)
                    my_lambda_arn = self.__get_lambda_arn_for_rule(
                        rule_name,
                        partition,
                        my_session.region_name,
                        account_id,
                        rule_params,
                    )
                    if "SourceIdentifier" in rule_params:
                        print(f"[{my_session.region_name}]: Skipping Lambda upload for Managed Rule.")
                        continue

                    print(f"[{my_session.region_name}]: Publishing Lambda code...")
                    my_lambda_client = my_session.client("lambda")
                    my_lambda_client.update_function_code(
                        FunctionName=my_lambda_arn,
                        S3Bucket=code_bucket_name,
                        S3Key=s3_code_objects[rule_name],
                        Publish=True,
                    )
                    print(f"[{my_session.region_name}]: Lambda code updated.")
            except ClientError as e:
                # If we're in the exception, the stack does not exist and we should create it.
                print(f"[{my_session.region_name}]: Creating CloudFormation Stack for Lambda Functions.")

                cfn_args = {
                    "StackName": self.args.stack_name,
                    "TemplateURL": template_url,
                    "Parameters": cfn_params,
                    "Capabilities": ["CAPABILITY_IAM"],
                }

                # If no tags key is specified, or if the tags dict is empty
                if cfn_tags is not None:
                    cfn_args["Tags"] = cfn_tags

                response = my_cfn.create_stack(**cfn_args)

                # wait for changes to propagate.
                self.__wait_for_cfn_stack(my_cfn, self.args.stack_name)

            # We're done!  Return with great success.
            sys.exit(0)

        # If we're deploying both the functions and the Config rules, run the following process:
        for rule_name in rule_names:
            rule_params, cfn_tags = self.__get_rule_parameters(rule_name)

            # create CFN Parameters common for Managed and Custom
            source_events = "NONE"
            if "SourceEvents" in rule_params:
                source_events = rule_params["SourceEvents"]

            source_periodic = "NONE"
            if "SourcePeriodic" in rule_params:
                source_periodic = rule_params["SourcePeriodic"]

            combined_input_parameters = {}
            if "InputParameters" in rule_params:
                combined_input_parameters.update(json.loads(rule_params["InputParameters"]))

            if "OptionalParameters" in rule_params:
                # Remove empty parameters
                keys_to_delete = []
                optional_parameters_json = json.loads(rule_params["OptionalParameters"])
                for key, value in optional_parameters_json.items():
                    if not value:
                        keys_to_delete.append(key)
                for key in keys_to_delete:
                    del optional_parameters_json[key]
                combined_input_parameters.update(optional_parameters_json)

            if "SourceIdentifier" in rule_params:
                print("Found Managed Rule.")
                # create CFN Parameters for Managed Rules

                try:
                    rule_description = rule_params["Description"]
                except KeyError:
                    rule_description = rule_name
                my_params = [
                    {
                        "ParameterKey": "RuleName",
                        "ParameterValue": rule_name,
                    },
                    {
                        "ParameterKey": "Description",
                        "ParameterValue": rule_description,
                    },
                    {
                        "ParameterKey": "SourceEvents",
                        "ParameterValue": source_events,
                    },
                    {
                        "ParameterKey": "SourcePeriodic",
                        "ParameterValue": source_periodic,
                    },
                    {
                        "ParameterKey": "SourceInputParameters",
                        "ParameterValue": json.dumps(combined_input_parameters),
                    },
                    {
                        "ParameterKey": "SourceIdentifier",
                        "ParameterValue": rule_params["SourceIdentifier"],
                    },
                ]
                my_cfn = my_session.client("cloudformation")
                if "Remediation" in rule_params:
                    print(f"[{my_session.region_name}]: Build The CFN Template with Remediation Settings")
                    cfn_body = os.path.join(
                        path.dirname(__file__),
                        "template",
                        "configManagedRuleWithRemediation.json",
                    )
                    template_body = open(cfn_body, "r").read()
                    json_body = json.loads(template_body)
                    remediation = self.__create_remediation_cloudformation_block(rule_params["Remediation"])
                    json_body["Resources"]["Remediation"] = remediation

                    if "SSMAutomation" in rule_params:
                        # Reference the SSM Automation Role Created, if IAM is created
                        print(f"[{my_session.region_name}]: Building SSM Automation Section")
                        ssm_automation = self.__create_automation_cloudformation_block(
                            rule_params["SSMAutomation"],
                            self.__get_alphanumeric_rule_name(rule_name),
                        )
                        json_body["Resources"][
                            self.__get_alphanumeric_rule_name(rule_name + "RemediationAction")
                        ] = ssm_automation
                        if "IAM" in rule_params["SSMAutomation"]:
                            print(f"[{my_session.region_name}]: Lets Build IAM Role and Policy")
                            # TODO Check For IAM Settings
                            json_body["Resources"]["Remediation"]["Properties"]["Parameters"]["AutomationAssumeRole"][
                                "StaticValue"
                            ]["Values"] = [
                                {
                                    "Fn::GetAtt": [
                                        self.__get_alphanumeric_rule_name(rule_name + "Role"),
                                        "Arn",
                                    ]
                                }
                            ]

                            (ssm_iam_role, ssm_iam_policy,) = self.__create_automation_iam_cloudformation_block(
                                rule_params["SSMAutomation"],
                                self.__get_alphanumeric_rule_name(rule_name),
                            )
                            json_body["Resources"][self.__get_alphanumeric_rule_name(rule_name + "Role")] = ssm_iam_role
                            json_body["Resources"][
                                self.__get_alphanumeric_rule_name(rule_name + "Policy")
                            ] = ssm_iam_policy

                            print(f"[{my_session.region_name}]: Build Supporting SSM Resources")
                            resource_depends_on = [
                                "rdkConfigRule",
                                self.__get_alphanumeric_rule_name(rule_name + "RemediationAction"),
                            ]
                            # Builds SSM Document Before Config RUle
                            json_body["Resources"]["Remediation"]["DependsOn"] = resource_depends_on
                            json_body["Resources"]["Remediation"]["Properties"]["TargetId"] = {
                                "Ref": self.__get_alphanumeric_rule_name(rule_name + "RemediationAction")
                            }

                    try:
                        my_stack_name = self.__get_stack_name_from_rule_name(rule_name)
                        my_stack = my_cfn.describe_stacks(StackName=my_stack_name)
                        # If we've gotten here, stack exists and we should update it.
                        print(f"[{my_session.region_name}]: Updating CloudFormation Stack for " + rule_name)
                        try:
                            cfn_args = {
                                "StackName": my_stack_name,
                                "TemplateBody": json.dumps(json_body, indent=2),
                                "Parameters": my_params,
                                "Capabilities": [
                                    "CAPABILITY_IAM",
                                    "CAPABILITY_NAMED_IAM",
                                ],
                            }

                            # If no tags key is specified, or if the tags dict is empty
                            if cfn_tags is not None:
                                cfn_args["Tags"] = cfn_tags

                            response = my_cfn.update_stack(**cfn_args)
                        except ClientError as e:
                            if e.response["Error"]["Code"] == "ValidationError":
                                if "No updates are to be performed." in str(e):
                                    # No changes made to Config rule definition, so CloudFormation won't do anything.
                                    print(f"[{my_session.region_name}]: No changes to Config Rule.")
                                else:
                                    # Something unexpected has gone wrong.  Emit an error and bail.
                                    print(e)
                                    return 1
                            else:
                                raise
                    except ClientError as e:
                        # If we're in the exception, the stack does not exist and we should create it.
                        print(f"[{my_session.region_name}]: Creating CloudFormation Stack for " + rule_name)

                        if "Remediation" in rule_params:
                            cfn_args = {
                                "StackName": my_stack_name,
                                "TemplateBody": json.dumps(json_body, indent=2),
                                "Parameters": my_params,
                                "Capabilities": [
                                    "CAPABILITY_IAM",
                                    "CAPABILITY_NAMED_IAM",
                                ],
                            }

                        else:
                            cfn_args = {
                                "StackName": my_stack_name,
                                "TemplateBody": open(cfn_body, "r").read(),
                                "Parameters": my_params,
                            }

                        if cfn_tags is not None:
                            cfn_args["Tags"] = cfn_tags

                        response = my_cfn.create_stack(**cfn_args)

                    # wait for changes to propagate.
                    self.__wait_for_cfn_stack(my_cfn, my_stack_name)
                    continue

                else:
                    # deploy config rule
                    cfn_body = os.path.join(path.dirname(__file__), "template", "configManagedRule.json")

                    try:
                        my_stack_name = self.__get_stack_name_from_rule_name(rule_name)
                        my_stack = my_cfn.describe_stacks(StackName=my_stack_name)
                        # If we've gotten here, stack exists and we should update it.
                        print(f"[{self.args.region}]: Updating CloudFormation Stack for " + rule_name)
                        try:
                            cfn_args = {
                                "StackName": my_stack_name,
                                "TemplateBody": open(cfn_body, "r").read(),
                                "Parameters": my_params,
                            }

                            # If no tags key is specified, or if the tags dict is empty
                            if cfn_tags is not None:
                                cfn_args["Tags"] = cfn_tags

                            response = my_cfn.update_stack(**cfn_args)
                        except ClientError as e:
                            if e.response["Error"]["Code"] == "ValidationError":
                                if "No updates are to be performed." in str(e):
                                    # No changes made to Config rule definition, so CloudFormation won't do anything.
                                    print(f"[{my_session.region_name}]: No changes to Config Rule.")
                                else:
                                    # Something unexpected has gone wrong.  Emit an error and bail.
                                    print(f"[{my_session.region_name}]:  {e}")
                                    return 1
                            else:
                                raise
                    except ClientError as e:
                        # If we're in the exception, the stack does not exist and we should create it.
                        print(f"[{self.args.region}]: Creating CloudFormation Stack for " + rule_name)
                        cfn_args = {
                            "StackName": my_stack_name,
                            "TemplateBody": open(cfn_body, "r").read(),
                            "Parameters": my_params,
                        }

                        if cfn_tags is not None:
                            cfn_args["Tags"] = cfn_tags

                        response = my_cfn.create_stack(**cfn_args)

                    # wait for changes to propagate.
                    self.__wait_for_cfn_stack(my_cfn, my_stack_name)

                # Cloudformation is not supporting tagging config rule currently.
                if cfn_tags is not None and len(cfn_tags) > 0:
                    self.__tag_config_rule(rule_name, cfn_tags, my_session)

                continue

            print(f"[{my_session.region_name}]: Found Custom Rule.")

            s3_src = ""
            s3_dst = self.__upload_function_code(rule_name, rule_params, account_id, my_session, code_bucket_name)

            # create CFN Parameters for Custom Rules
            lambdaRoleArn = ""
            if self.args.lambda_role_arn:
                print(f"[{my_session.region_name}]: Existing IAM Role provided: " + self.args.lambda_role_arn)
                lambdaRoleArn = self.args.lambda_role_arn
            elif self.args.lambda_role_name:
                print(f"[{my_session.region_name}]: Finding IAM Role: " + self.args.lambda_role_name)
                arn = f"arn:{partition}:iam::{account_id}:role/Rdk-Lambda-Role"
                lambdaRoleArn = arn

            if self.args.boundary_policy_arn:
                print(f"[{my_session.region_name}]: Boundary Policy provided: " + self.args.boundary_policy_arn)
                boundaryPolicyArn = self.args.boundary_policy_arn
            else:
                boundaryPolicyArn = ""

            try:
                rule_description = rule_params["Description"]
            except KeyError:
                rule_description = rule_name

            my_params = [
                {
                    "ParameterKey": "RuleName",
                    "ParameterValue": rule_name,
                },
                {
                    "ParameterKey": "RuleLambdaName",
                    "ParameterValue": self.__get_lambda_name(rule_name, rule_params),
                },
                {
                    "ParameterKey": "Description",
                    "ParameterValue": rule_description,
                },
                {
                    "ParameterKey": "LambdaRoleArn",
                    "ParameterValue": lambdaRoleArn,
                },
                {
                    "ParameterKey": "BoundaryPolicyArn",
                    "ParameterValue": boundaryPolicyArn,
                },
                {
                    "ParameterKey": "SourceBucket",
                    "ParameterValue": code_bucket_name,
                },
                {
                    "ParameterKey": "SourcePath",
                    "ParameterValue": s3_dst,
                },
                {
                    "ParameterKey": "SourceRuntime",
                    "ParameterValue": self.__get_runtime_string(rule_params),
                },
                {
                    "ParameterKey": "SourceEvents",
                    "ParameterValue": source_events,
                },
                {
                    "ParameterKey": "SourcePeriodic",
                    "ParameterValue": source_periodic,
                },
                {
                    "ParameterKey": "SourceInputParameters",
                    "ParameterValue": json.dumps(combined_input_parameters),
                },
                {
                    "ParameterKey": "SourceHandler",
                    "ParameterValue": self.__get_handler(rule_name, rule_params),
                },
                {
                    "ParameterKey": "Timeout",
                    "ParameterValue": str(self.args.lambda_timeout),
                },
            ]
            layers = self.__get_lambda_layers(my_session, self.args, rule_params)

            if self.args.lambda_layers:
                additional_layers = self.args.lambda_layers.split(",")
                layers.extend(additional_layers)

            if layers:
                my_params.append({"ParameterKey": "Layers", "ParameterValue": ",".join(layers)})

            if self.args.lambda_security_groups and self.args.lambda_subnets:
                my_params.append(
                    {
                        "ParameterKey": "SecurityGroupIds",
                        "ParameterValue": self.args.lambda_security_groups,
                    }
                )
                my_params.append(
                    {
                        "ParameterKey": "SubnetIds",
                        "ParameterValue": self.args.lambda_subnets,
                    }
                )

            # create json of CFN template
            cfn_body = os.path.join(path.dirname(__file__), "template", "configRule.json")
            template_body = open(cfn_body, "r").read()
            json_body = json.loads(template_body)

            remediation = ""
            if "Remediation" in rule_params:
                remediation = self.__create_remediation_cloudformation_block(rule_params["Remediation"])
                json_body["Resources"]["Remediation"] = remediation

                if "SSMAutomation" in rule_params:
                    ##AWS needs to build the SSM before the Config Rule
                    resource_depends_on = [
                        "rdkConfigRule",
                        self.__get_alphanumeric_rule_name(rule_name + "RemediationAction"),
                    ]
                    remediation["DependsOn"] = resource_depends_on
                    # Add JSON Reference to SSM Document { "Ref" : "MyEC2Instance" }
                    remediation["Properties"]["TargetId"] = {
                        "Ref": self.__get_alphanumeric_rule_name(rule_name + "RemediationAction")
                    }

            if "SSMAutomation" in rule_params:
                print(f"[{my_session.region_name}]: Building SSM Automation Section")

                ssm_automation = self.__create_automation_cloudformation_block(rule_params["SSMAutomation"], rule_name)
                json_body["Resources"][
                    self.__get_alphanumeric_rule_name(rule_name + "RemediationAction")
                ] = ssm_automation
                if "IAM" in rule_params["SSMAutomation"]:
                    print("Lets Build IAM Role and Policy")
                    # TODO Check For IAM Settings
                    json_body["Resources"]["Remediation"]["Properties"]["Parameters"]["AutomationAssumeRole"][
                        "StaticValue"
                    ]["Values"] = [
                        {
                            "Fn::GetAtt": [
                                self.__get_alphanumeric_rule_name(rule_name + "Role"),
                                "Arn",
                            ]
                        }
                    ]

                    (
                        ssm_iam_role,
                        ssm_iam_policy,
                    ) = self.__create_automation_iam_cloudformation_block(rule_params["SSMAutomation"], rule_name)
                    json_body["Resources"][self.__get_alphanumeric_rule_name(rule_name + "Role")] = ssm_iam_role
                    json_body["Resources"][self.__get_alphanumeric_rule_name(rule_name + "Policy")] = ssm_iam_policy

            # debugging
            # print(json.dumps(json_body, indent=2))

            # deploy config rule
            my_cfn = my_session.client("cloudformation")
            try:
                my_stack_name = self.__get_stack_name_from_rule_name(rule_name)
                my_stack = my_cfn.describe_stacks(StackName=my_stack_name)
                # If we've gotten here, stack exists and we should update it.
                print(f"[{self.args.region}]: Updating CloudFormation Stack for " + rule_name)
                try:
                    cfn_args = {
                        "StackName": my_stack_name,
                        "TemplateBody": json.dumps(json_body, indent=2),
                        "Parameters": my_params,
                        "Capabilities": ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
                    }

                    # If no tags key is specified, or if the tags dict is empty
                    if cfn_tags is not None:
                        cfn_args["Tags"] = cfn_tags

                    response = my_cfn.update_stack(**cfn_args)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ValidationError":

                        if "No updates are to be performed." in str(e):
                            # No changes made to Config rule definition, so CloudFormation won't do anything.
                            print(f"[{my_session.region_name}]: No changes to Config Rule.")
                        else:
                            # Something unexpected has gone wrong.  Emit an error and bail.
                            print(f"[{my_session.region_name}]: Validation Error on CFN\n")
                            print(f"[{my_session.region_name}]: " + json.dumps(cfn_args) + "\n")
                            print(f"[{my_session.region_name}]: {e}\n")
                            return 1
                    else:
                        raise

                my_lambda_arn = self.__get_lambda_arn_for_stack(my_stack_name)

                print(f"[{my_session.region_name}]: Publishing Lambda code...")
                my_lambda_client = my_session.client("lambda")
                my_lambda_client.update_function_code(
                    FunctionName=my_lambda_arn,
                    S3Bucket=code_bucket_name,
                    S3Key=s3_dst,
                    Publish=True,
                )
                print(f"[{my_session.region_name}]: Lambda code updated.")
            except ClientError as e:
                # If we're in the exception, the stack does not exist and we should create it.
                print(f"[{my_session.region_name}]: Creating CloudFormatioon Stack for " + rule_name)
                cfn_args = {
                    "StackName": my_stack_name,
                    "TemplateBody": json.dumps(json_body, indent=2),
                    "Parameters": my_params,
                    "Capabilities": ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
                }

                if cfn_tags is not None:
                    cfn_args["Tags"] = cfn_tags

                response = my_cfn.create_stack(**cfn_args)

            # wait for changes to propagate.
            self.__wait_for_cfn_stack(my_cfn, my_stack_name)

            # Cloudformation is not supporting tagging config rule currently.
            if cfn_tags is not None and len(cfn_tags) > 0:
                self.__tag_config_rule(rule_name, cfn_tags, my_session)

        print(f"[{my_session.region_name}]: Config deploy complete.")

        return 0

    def deploy_organization(self):
        self.__parse_deploy_organization_args()

        # get the rule names
        rule_names = self.__get_rule_list_for_command()

        # run the deploy code
        print("Running Organization deploy!")

        # create custom session based on whatever credentials are available to us
        my_session = self.__get_boto_session()

        # get accountID
        identity_details = self.__get_caller_identity_details(my_session)
        account_id = identity_details["account_id"]
        partition = identity_details["partition"]

        if self.args.custom_code_bucket:
            code_bucket_name = self.args.custom_code_bucket
        else:
            code_bucket_name = CODE_BUCKET_PREFIX + account_id + "-" + my_session.region_name

        # If we're only deploying the Lambda functions (and role + permissions), branch here.  Someday the "main" execution path should use the same generated CFN templates for single-account deployment.
        if self.args.functions_only:
            print("We don't handle Function Only deployment for Organizations")
            sys.exit(1)

        # If we're deploying both the functions and the Config rules, run the following process:
        for rule_name in rule_names:
            rule_params, cfn_tags = self.__get_rule_parameters(rule_name)

            # create CFN Parameters common for Managed and Custom
            source_events = "NONE"
            if "Remediation" in rule_params:
                print(
                    f"WARNING: Organization Rules with Remediation is not supported at the moment. {rule_name} will be deployed without auto-remediation."
                )

            if "SourceEvents" in rule_params:
                source_events = rule_params["SourceEvents"]

            source_periodic = "NONE"
            if "SourcePeriodic" in rule_params:
                source_periodic = rule_params["SourcePeriodic"]

            combined_input_parameters = {}
            if "InputParameters" in rule_params:
                combined_input_parameters.update(json.loads(rule_params["InputParameters"]))

            if "OptionalParameters" in rule_params:
                # Remove empty parameters
                keys_to_delete = []
                optional_parameters_json = json.loads(rule_params["OptionalParameters"])
                for key, value in optional_parameters_json.items():
                    if not value:
                        keys_to_delete.append(key)
                for key in keys_to_delete:
                    del optional_parameters_json[key]
                combined_input_parameters.update(optional_parameters_json)

            if "SourceIdentifier" in rule_params:
                print("Found Managed Rule.")
                # create CFN Parameters for Managed Rules

                try:
                    rule_description = rule_params["Description"]
                except KeyError:
                    rule_description = rule_name
                my_params = [
                    {
                        "ParameterKey": "RuleName",
                        "ParameterValue": rule_name,
                    },
                    {
                        "ParameterKey": "Description",
                        "ParameterValue": rule_description,
                    },
                    {
                        "ParameterKey": "SourceEvents",
                        "ParameterValue": source_events,
                    },
                    {
                        "ParameterKey": "SourcePeriodic",
                        "ParameterValue": source_periodic,
                    },
                    {
                        "ParameterKey": "SourceInputParameters",
                        "ParameterValue": json.dumps(combined_input_parameters),
                    },
                    {
                        "ParameterKey": "SourceIdentifier",
                        "ParameterValue": rule_params["SourceIdentifier"],
                    },
                ]
                my_cfn = my_session.client("cloudformation")

                # deploy config rule
                cfn_body = os.path.join(
                    path.dirname(__file__),
                    "template",
                    "configManagedRuleOrganization.json",
                )

                try:
                    my_stack_name = self.__get_stack_name_from_rule_name(rule_name)
                    my_stack = my_cfn.describe_stacks(StackName=my_stack_name)
                    # If we've gotten here, stack exists and we should update it.
                    print("Updating CloudFormation Stack for " + rule_name)
                    try:
                        cfn_args = {
                            "StackName": my_stack_name,
                            "TemplateBody": open(cfn_body, "r").read(),
                            "Parameters": my_params,
                        }

                        # If no tags key is specified, or if the tags dict is empty
                        if cfn_tags is not None:
                            cfn_args["Tags"] = cfn_tags

                        response = my_cfn.update_stack(**cfn_args)
                    except ClientError as e:
                        if e.response["Error"]["Code"] == "ValidationError":
                            if "No updates are to be performed." in str(e):
                                # No changes made to Config rule definition, so CloudFormation won't do anything.
                                print("No changes to Config Rule.")
                            else:
                                # Something unexpected has gone wrong.  Emit an error and bail.
                                print(e)
                                return 1
                        else:
                            raise
                except ClientError as e:
                    # If we're in the exception, the stack does not exist and we should create it.
                    print("Creating CloudFormation Stack for " + rule_name)
                    cfn_args = {
                        "StackName": my_stack_name,
                        "TemplateBody": open(cfn_body, "r").read(),
                        "Parameters": my_params,
                    }

                    if cfn_tags is not None:
                        cfn_args["Tags"] = cfn_tags

                    response = my_cfn.create_stack(**cfn_args)

                # wait for changes to propagate.
                self.__wait_for_cfn_stack(my_cfn, my_stack_name)

                # Cloudformation is not supporting tagging config rule currently.
                if cfn_tags is not None and len(cfn_tags) > 0:
                    print(
                        "WARNING: Tagging is not supported for organization config rules. Only the cloudformation template will be tagged."
                    )

                continue

            print("Found Custom Rule.")

            s3_src = ""
            s3_dst = self.__upload_function_code(rule_name, rule_params, account_id, my_session, code_bucket_name)

            # create CFN Parameters for Custom Rules
            lambdaRoleArn = ""
            if self.args.lambda_role_arn:
                print("Existing IAM Role provided: " + self.args.lambda_role_arn)
                lambdaRoleArn = self.args.lambda_role_arn
            elif self.args.lambda_role_name:
                print(f"[{my_session.region_name}]: Finding IAM Role: " + self.args.lambda_role_name)
                arn = f"arn:{partition}:iam::{account_id}:role/Rdk-Lambda-Role"
                lambdaRoleArn = arn

            if self.args.boundary_policy_arn:
                print("Boundary Policy provided: " + self.args.boundary_policy_arn)
                boundaryPolicyArn = self.args.boundary_policy_arn
            else:
                boundaryPolicyArn = ""

            try:
                rule_description = rule_params["Description"]
            except KeyError:
                rule_description = rule_name

            my_params = [
                {
                    "ParameterKey": "RuleName",
                    "ParameterValue": rule_name,
                },
                {
                    "ParameterKey": "RuleLambdaName",
                    "ParameterValue": self.__get_lambda_name(rule_name, rule_params),
                },
                {
                    "ParameterKey": "Description",
                    "ParameterValue": rule_description,
                },
                {
                    "ParameterKey": "LambdaRoleArn",
                    "ParameterValue": lambdaRoleArn,
                },
                {
                    "ParameterKey": "BoundaryPolicyArn",
                    "ParameterValue": boundaryPolicyArn,
                },
                {
                    "ParameterKey": "SourceBucket",
                    "ParameterValue": code_bucket_name,
                },
                {
                    "ParameterKey": "SourcePath",
                    "ParameterValue": s3_dst,
                },
                {
                    "ParameterKey": "SourceRuntime",
                    "ParameterValue": self.__get_runtime_string(rule_params),
                },
                {
                    "ParameterKey": "SourceEvents",
                    "ParameterValue": source_events,
                },
                {
                    "ParameterKey": "SourcePeriodic",
                    "ParameterValue": source_periodic,
                },
                {
                    "ParameterKey": "SourceInputParameters",
                    "ParameterValue": json.dumps(combined_input_parameters),
                },
                {
                    "ParameterKey": "SourceHandler",
                    "ParameterValue": self.__get_handler(rule_name, rule_params),
                },
                {
                    "ParameterKey": "Timeout",
                    "ParameterValue": str(self.args.lambda_timeout),
                },
            ]
            layers = self.__get_lambda_layers(session, self.args, params)

            if self.args.lambda_layers:
                additional_layers = self.args.lambda_layers.split(",")
                layers.extend(additional_layers)

            if layers:
                my_params.append({"ParameterKey": "Layers", "ParameterValue": ",".join(layers)})

            if self.args.lambda_security_groups and self.args.lambda_subnets:
                my_params.append(
                    {
                        "ParameterKey": "SecurityGroupIds",
                        "ParameterValue": self.args.lambda_security_groups,
                    }
                )
                my_params.append(
                    {
                        "ParameterKey": "SubnetIds",
                        "ParameterValue": self.args.lambda_subnets,
                    }
                )

            # create json of CFN template
            cfn_body = os.path.join(path.dirname(__file__), "template", "configRuleOrganization.json")
            template_body = open(cfn_body, "r").read()
            json_body = json.loads(template_body)

            # debugging
            # print(json.dumps(json_body, indent=2))

            # deploy config rule
            my_cfn = my_session.client("cloudformation")
            try:
                my_stack_name = self.__get_stack_name_from_rule_name(rule_name)
                my_stack = my_cfn.describe_stacks(StackName=my_stack_name)
                # If we've gotten here, stack exists and we should update it.
                print("Updating CloudFormation Stack for " + rule_name)
                try:
                    cfn_args = {
                        "StackName": my_stack_name,
                        "TemplateBody": json.dumps(json_body),
                        "Parameters": my_params,
                        "Capabilities": ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
                    }

                    # If no tags key is specified, or if the tags dict is empty
                    if cfn_tags is not None:
                        cfn_args["Tags"] = cfn_tags

                    response = my_cfn.update_stack(**cfn_args)
                except ClientError as e:
                    if e.response["Error"]["Code"] == "ValidationError":

                        if "No updates are to be performed." in str(e):
                            # No changes made to Config rule definition, so CloudFormation won't do anything.
                            print("No changes to Config Rule.")
                        else:
                            # Something unexpected has gone wrong.  Emit an error and bail.
                            print("Validation Error on CFN")
                            print(json.dumps(cfn_args))
                            print(e)
                            return 1
                    else:
                        raise

                my_lambda_arn = self.__get_lambda_arn_for_stack(my_stack_name)

                print("Publishing Lambda code...")
                my_lambda_client = my_session.client("lambda")
                my_lambda_client.update_function_code(
                    FunctionName=my_lambda_arn,
                    S3Bucket=code_bucket_name,
                    S3Key=s3_dst,
                    Publish=True,
                )
                print("Lambda code updated.")
            except ClientError as e:
                # If we're in the exception, the stack does not exist and we should create it.
                print("Creating CloudFormation Stack for " + rule_name)
                cfn_args = {
                    "StackName": my_stack_name,
                    "TemplateBody": json.dumps(json_body),
                    "Parameters": my_params,
                    "Capabilities": ["CAPABILITY_IAM", "CAPABILITY_NAMED_IAM"],
                }

                if cfn_tags is not None:
                    cfn_args["Tags"] = cfn_tags

                response = my_cfn.create_stack(**cfn_args)

            # wait for changes to propagate.
            self.__wait_for_cfn_stack(my_cfn, my_stack_name)

            # Cloudformation is not supporting tagging config rule currently.
            if cfn_tags is not None and len(cfn_tags) > 0:
                print(
                    "WARNING: Tagging is not supported for organization config rules. Only the cloudformation template will be tagged."
                )

        print("Config deploy complete.")

        return 0

    def export(self):

        self.__parse_export_args()

        # get the rule names
        rule_names = self.__get_rule_list_for_command("export")

        # run the export code
        print("Running export")

        for rule_name in rule_names:
            rule_params, cfn_tags = self.__get_rule_parameters(rule_name)

            if "SourceIdentifier" in rule_params:
                print("Found Managed Rule, Ignored.")
                print("Export support only Custom Rules.")
                continue

            source_events = []
            if "SourceEvents" in rule_params:
                source_events = [rule_params["SourceEvents"]]

            source_periodic = "NONE"
            if "SourcePeriodic" in rule_params:
                source_periodic = rule_params["SourcePeriodic"]

            combined_input_parameters = {}
            if "InputParameters" in rule_params:
                combined_input_parameters.update(json.loads(rule_params["InputParameters"]))

            if "OptionalParameters" in rule_params:
                # Remove empty parameters
                keys_to_delete = []
                optional_parameters_json = json.loads(rule_params["OptionalParameters"])
                for key, value in optional_parameters_json.items():
                    if not value:
                        keys_to_delete.append(key)
                for key in keys_to_delete:
                    del optional_parameters_json[key]
                combined_input_parameters.update(optional_parameters_json)

            print("Found Custom Rule.")
            s3_src = ""
            s3_dst = self.__package_function_code(rule_name, rule_params)

            layers = []
            rdk_lib_version = "0"
            my_session = self.__get_boto_session()
            layers = self.__get_lambda_layers(my_session, self.args, rule_params)

            if self.args.lambda_layers:
                additional_layers = self.args.lambda_layers.split(",")
                layers.extend(additional_layers)

            subnet_ids = []
            security_group_ids = []
            if self.args.lambda_security_groups:
                security_group_ids = self.args.lambda_security_groups.split(",")

            if self.args.lambda_subnets:
                subnet_ids = self.args.lambda_subnets.split(",")

            lambda_role_arn = "NONE"
            if self.args.lambda_role_arn:
                print("Existing IAM Role provided: " + self.args.lambda_role_arn)
                lambda_role_arn = self.args.lambda_role_arn

            my_params = {
                "rule_name": rule_name,
                "rule_lambda_name": self.__get_lambda_name(rule_name, rule_params),
                "source_runtime": self.__get_runtime_string(rule_params),
                "source_events": source_events,
                "source_periodic": source_periodic,
                "source_input_parameters": json.dumps(combined_input_parameters),
                "source_handler": self.__get_handler(rule_name, rule_params),
                "subnet_ids": subnet_ids,
                "security_group_ids": security_group_ids,
                "lambda_layers": layers,
                "lambda_role_arn": lambda_role_arn,
                "lambda_timeout": str(self.args.lambda_timeout),
            }

            params_file_path = os.path.join(os.getcwd(), RULES_DIR, rule_name, rule_name.lower() + ".tfvars.json")
            parameters_file = open(params_file_path, "w")
            json.dump(my_params, parameters_file, indent=4)
            parameters_file.close()
            # create json of CFN template
            print(self.args.format + " version: " + self.args.version)
            tf_file_body = os.path.join(
                path.dirname(__file__),
                "template",
                self.args.format,
                self.args.version,
                "config_rule.tf",
            )
            tf_file_path = os.path.join(os.getcwd(), RULES_DIR, rule_name, rule_name.lower() + "_rule.tf")
            shutil.copy(tf_file_body, tf_file_path)

            variables_file_body = os.path.join(
                path.dirname(__file__),
                "template",
                self.args.format,
                self.args.version,
                "variables.tf",
            )
            variables_file_path = os.path.join(os.getcwd(), RULES_DIR, rule_name, rule_name.lower() + "_variables.tf")
            shutil.copy(variables_file_body, variables_file_path)
            print("Export completed.This will generate three .tf files.")

    def test_local(self):
        print("Running local test!")
        tests_successful = True

        args = self.__parse_test_args()

        # Construct our list of rules to test.
        rule_names = self.__get_rule_list_for_command()

        for rule_name in rule_names:
            rule_params, rule_tags = self.__get_rule_parameters(rule_name)
            if rule_params["SourceRuntime"] not in (
                "python3.6",
                "python3.6-lib",
                "python3.7",
                "python3.7-lib",
                "python3.8",
                "python3.8-lib",
                "python3.9",
                "python3.9-lib",
            ):
                print("Skipping " + rule_name + " - Runtime not supported for local testing.")
                continue

            print("Testing " + rule_name)
            test_dir = os.path.join(os.getcwd(), RULES_DIR, rule_name)
            print("Looking for tests in " + test_dir)

            if args.verbose == True:
                results = unittest.TextTestRunner(buffer=False, verbosity=2).run(self.__create_test_suite(test_dir))
            else:
                results = unittest.TextTestRunner(buffer=True, verbosity=2).run(self.__create_test_suite(test_dir))

            print(results)

            tests_successful = tests_successful and results.wasSuccessful()
        return int(not tests_successful)

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

    def status(self):
        print("Running status!")
        return 0

    def sample_ci(self):
        self.args = get_sample_ci_parser().parse_args(self.args.command_args, self.args)

        my_test_ci = TestCI(self.args.ci_type)
        print(json.dumps(my_test_ci.get_json(), indent=4))

    def logs(self):
        self.args = get_logs_parser().parse_args(self.args.command_args, self.args)

        self.args.rulename = self.__clean_rule_name(self.args.rulename)

        my_session = self.__get_boto_session()
        cw_logs = my_session.client("logs")
        log_group_name = self.__get_log_group_name()

        # Retrieve the last number of log events as specified by the user.
        try:
            log_streams = cw_logs.describe_log_streams(
                logGroupName=log_group_name,
                orderBy="LastEventTime",
                descending=True,
                limit=int(self.args.number),  # This is the worst-case scenario if there is only one event per stream
            )

            # Sadly we can't just use filter_log_events, since we don't know the timestamps yet and filter_log_events doesn't appear to support ordering.
            my_events = self.__get_log_events(cw_logs, log_streams, int(self.args.number))

            latest_timestamp = 0

            if my_events is None:
                print("No Events to display.")
                return 0

            for event in my_events:
                if event["timestamp"] > latest_timestamp:
                    latest_timestamp = event["timestamp"]

                self.__print_log_event(event)

            if self.args.follow:
                try:
                    while True:
                        # Wait 2 seconds
                        time.sleep(2)

                        # Get all events between now and the timestamp of the most recent event.
                        my_new_events = cw_logs.filter_log_events(
                            logGroupName=log_group_name,
                            startTime=latest_timestamp + 1,
                            endTime=int(time.time()) * 1000,
                            interleaved=True,
                        )

                        for event in my_new_events["events"]:
                            if "timestamp" in event:
                                # Get the timestamp on the most recent event.
                                if event["timestamp"] > latest_timestamp:
                                    latest_timestamp = event["timestamp"]

                                # Print the event.
                                self.__print_log_event(event)
                except KeyboardInterrupt as k:
                    sys.exit(0)

        except cw_logs.exceptions.ResourceNotFoundException as e:
            print(e.response["Error"]["Message"])

    def rulesets(self):
        self.args = get_rulesets_parser().parse_args(self.args.command_args, self.args)

        if self.args.subcommand in ["add", "remove"] and (not self.args.ruleset or not self.args.rulename):
            print("You must specify a ruleset name and a rule for the `add` and `remove` commands.")
            return 1

        if self.args.subcommand == "list":
            self.__list_rulesets()
        elif self.args.subcommand == "add":
            self.__add_ruleset_rule(self.args.ruleset, self.args.rulename)
        elif self.args.subcommand == "remove":
            self.__remove_ruleset_rule(self.args.ruleset, self.args.rulename)
        else:
            print("Unknown subcommand.")

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

    def create_rule_template(self):
        self.args = get_create_rule_template_parser().parse_args(self.args.command_args, self.args)

        if self.args.rulesets:
            self.args.rulesets = self.args.rulesets.split(",")

        script_for_tag = ""

        print("Generating CloudFormation template!")

        # First add the common elements - description, parameters, and resource section header
        template = {}
        template["AWSTemplateFormatVersion"] = "2010-09-09"
        template[
            "Description"
        ] = "AWS CloudFormation template to create custom AWS Config rules. You will be billed for the AWS resources used if you create a stack from this template."

        optional_parameter_group = {"Label": {"default": "Optional"}, "Parameters": []}

        required_parameter_group = {"Label": {"default": "Required"}, "Parameters": []}

        parameters = {}
        parameters["LambdaAccountId"] = {}
        parameters["LambdaAccountId"]["Description"] = "Account ID that contains Lambda functions for Config Rules."
        parameters["LambdaAccountId"]["Type"] = "String"
        parameters["LambdaAccountId"]["MinLength"] = "12"
        parameters["LambdaAccountId"]["MaxLength"] = "12"

        resources = {}
        conditions = {}

        if not self.args.rules_only:
            # Create Config Role
            resources["ConfigRole"] = {}
            resources["ConfigRole"]["Type"] = "AWS::IAM::Role"
            resources["ConfigRole"]["DependsOn"] = "ConfigBucket"
            resources["ConfigRole"]["Properties"] = {
                "RoleName": CONFIG_ROLE_NAME,
                "Path": "/rdk/",
                "ManagedPolicyArns": [
                    {"Fn::Sub": "arn:${AWS::Partition}:iam::aws:policy/service-role/AWSConfigRole"},
                    {"Fn::Sub": "arn:${AWS::Partition}:iam::aws:policy/ReadOnlyAccess"},
                ],
                "AssumeRolePolicyDocument": CONFIG_ROLE_ASSUME_ROLE_POLICY_DOCUMENT,
                "Policies": [
                    {
                        "PolicyName": "DeliveryPermission",
                        "PolicyDocument": CONFIG_ROLE_POLICY_DOCUMENT,
                    }
                ],
            }

            # Create Bucket for Config Data
            resources["ConfigBucket"] = {
                "Type": "AWS::S3::Bucket",
                "Properties": {"BucketName": {"Fn::Sub": CONFIG_BUCKET_PREFIX + "-${AWS::AccountId}-${AWS::Region}"}},
            }

            # Create ConfigurationRecorder and DeliveryChannel
            resources["ConfigurationRecorder"] = {
                "Type": "AWS::Config::ConfigurationRecorder",
                "Properties": {
                    "Name": "default",
                    "RoleARN": {"Fn::GetAtt": ["ConfigRole", "Arn"]},
                    "RecordingGroup": {
                        "AllSupported": True,
                        "IncludeGlobalResourceTypes": True,
                    },
                },
            }
            if self.args.config_role_arn:
                resources["ConfigurationRecorder"]["Properties"]["RoleARN"] = self.args.config_role_arn

            resources["DeliveryChannel"] = {
                "Type": "AWS::Config::DeliveryChannel",
                "Properties": {
                    "Name": "default",
                    "S3BucketName": {"Ref": "ConfigBucket"},
                    "ConfigSnapshotDeliveryProperties": {"DeliveryFrequency": "One_Hour"},
                },
            }

        # Next, go through each rule in our rule list and add the CFN to deploy it.
        rule_names = self.__get_rule_list_for_command()
        for rule_name in rule_names:
            params, tags = self.__get_rule_parameters(rule_name)
            input_params = json.loads(params["InputParameters"])
            for input_param in input_params:
                cfn_param = {}
                cfn_param["Description"] = (
                    "Pass-through to required Input Parameter " + input_param + " for Config Rule " + rule_name
                )
                if len(str(input_params[input_param]).strip()) == 0:
                    default = "<REQUIRED>"
                else:
                    default = str(input_params[input_param])
                cfn_param["Default"] = default
                cfn_param["Type"] = "String"
                cfn_param["MinLength"] = 1
                cfn_param["ConstraintDescription"] = "This parameter is required."

                param_name = self.__get_alphanumeric_rule_name(rule_name) + input_param
                parameters[param_name] = cfn_param
                required_parameter_group["Parameters"].append(param_name)

            if "OptionalParameters" in params:
                optional_params = json.loads(params["OptionalParameters"])
                for optional_param in optional_params:
                    cfn_param = {}
                    cfn_param["Description"] = (
                        "Pass-through to optional Input Parameter " + optional_param + " for Config Rule " + rule_name
                    )
                    cfn_param["Default"] = optional_params[optional_param]
                    cfn_param["Type"] = "String"

                    param_name = self.__get_alphanumeric_rule_name(rule_name) + optional_param

                    parameters[param_name] = cfn_param
                    optional_parameter_group["Parameters"].append(param_name)

                    conditions[param_name] = {"Fn::Not": [{"Fn::Equals": ["", {"Ref": param_name}]}]}

            config_rule = {}
            config_rule["Type"] = "AWS::Config::ConfigRule"
            if not self.args.rules_only:
                config_rule["DependsOn"] = "DeliveryChannel"

            properties = {}
            source = {}
            source["SourceDetails"] = []

            properties["ConfigRuleName"] = rule_name
            try:
                properties["Description"] = params["Description"]
            except KeyError:
                properties["Description"] = rule_name

            # Create the SourceDetails stanza.
            if "SourceEvents" in params:
                # If there are SourceEvents specified for the Rule, generate the Scope clause.
                source_events = params["SourceEvents"].split(",")
                properties["Scope"] = {"ComplianceResourceTypes": source_events}

                # Also add the appropriate event source.
                source["SourceDetails"].append(
                    {
                        "EventSource": "aws.config",
                        "MessageType": "ConfigurationItemChangeNotification",
                    }
                )
            if "SourcePeriodic" in params:
                source["SourceDetails"].append(
                    {
                        "EventSource": "aws.config",
                        "MessageType": "ScheduledNotification",
                        "MaximumExecutionFrequency": params["SourcePeriodic"],
                    }
                )

            # If it's a Managed Rule it will have a SourceIdentifier string in the params and we need to set the source appropriately.  Otherwise, set the source to our custom lambda function.
            if "SourceIdentifier" in params:
                source["Owner"] = "AWS"
                source["SourceIdentifier"] = params["SourceIdentifier"]
                # Check the frequency of the managed rule if defined
                if "SourcePeriodic" in params:
                    properties["MaximumExecutionFrequency"] = params["SourcePeriodic"]
                del source["SourceDetails"]
            else:
                source["Owner"] = "CUSTOM_LAMBDA"
                source["SourceIdentifier"] = {
                    "Fn::Sub": "arn:${AWS::Partition}:lambda:${AWS::Region}:${LambdaAccountId}:function:"
                    + self.__get_lambda_name(rule_name, params)
                }

            properties["Source"] = source

            properties["InputParameters"] = {}

            if "InputParameters" in params:
                for required_param in json.loads(params["InputParameters"]):
                    cfn_param_name = self.__get_alphanumeric_rule_name(rule_name) + required_param
                    properties["InputParameters"][required_param] = {"Ref": cfn_param_name}

            if "OptionalParameters" in params:
                for optional_param in json.loads(params["OptionalParameters"]):
                    cfn_param_name = self.__get_alphanumeric_rule_name(rule_name) + optional_param
                    properties["InputParameters"][optional_param] = {
                        "Fn::If": [
                            cfn_param_name,
                            {"Ref": cfn_param_name},
                            {"Ref": "AWS::NoValue"},
                        ]
                    }

            config_rule["Properties"] = properties
            config_rule_resource_name = self.__get_alphanumeric_rule_name(rule_name) + "ConfigRule"
            resources[config_rule_resource_name] = config_rule

            # If Remediation create the remediation section with potential links to the SSM Details
            if "Remediation" in params:
                remediation = self.__create_remediation_cloudformation_block(params["Remediation"])
                remediation["DependsOn"] = [config_rule_resource_name]
                if not self.args.rules_only:
                    remediation["DependsOn"].append("ConfigRole")

                if "SSMAutomation" in params:
                    ssm_automation = self.__create_automation_cloudformation_block(params["SSMAutomation"], rule_name)
                    # AWS needs to build the SSM before the Config Rule
                    remediation["DependsOn"].append(self.__get_alphanumeric_rule_name(rule_name + "RemediationAction"))
                    # Add JSON Reference to SSM Document { "Ref" : "MyEC2Instance" }
                    remediation["Properties"]["TargetId"] = {
                        "Ref": self.__get_alphanumeric_rule_name(rule_name) + "RemediationAction"
                    }

                    if "IAM" in params["SSMAutomation"]:
                        print("Lets Build IAM Role and Policy For the SSM Document")
                        (
                            ssm_iam_role,
                            ssm_iam_policy,
                        ) = self.__create_automation_iam_cloudformation_block(params["SSMAutomation"], rule_name)
                        resources[self.__get_alphanumeric_rule_name(rule_name + "Role")] = ssm_iam_role
                        resources[self.__get_alphanumeric_rule_name(rule_name + "Policy")] = ssm_iam_policy
                        remediation["Properties"]["Parameters"]["AutomationAssumeRole"]["StaticValue"]["Values"] = [
                            {
                                "Fn::GetAtt": [
                                    self.__get_alphanumeric_rule_name(rule_name + "Role"),
                                    "Arn",
                                ]
                            }
                        ]
                        # Override the placeholder to associate the SSM Document Role with newly crafted role
                        resources[self.__get_alphanumeric_rule_name(rule_name + "RemediationAction")] = ssm_automation
                resources[self.__get_alphanumeric_rule_name(rule_name) + "Remediation"] = remediation

            if tags:
                tags_str = ""
                for tag in tags:
                    tags_str += "Key={},Value={} ".format(tag["Key"], tag["Value"])
                script_for_tag += "aws configservice tag-resource --resources-arn $(aws configservice describe-config-rules --config-rule-names {} --query 'ConfigRules[0].ConfigRuleArn' | tr -d '\"') --tags {} \n".format(
                    rule_name, tags_str
                )

        template["Resources"] = resources
        template["Conditions"] = conditions
        template["Parameters"] = parameters
        template["Metadata"] = {
            "AWS::CloudFormation::Interface": {
                "ParameterGroups": [
                    {
                        "Label": {"default": "Lambda Account ID"},
                        "Parameters": ["LambdaAccountId"],
                    },
                    required_parameter_group,
                    optional_parameter_group,
                ],
                "ParameterLabels": {
                    "LambdaAccountId": {
                        "default": "REQUIRED: Account ID that contains Lambda Function(s) that back the Rules in this template."
                    }
                },
            }
        }

        output_file = open(self.args.output_file, "w")
        output_file.write(json.dumps(template, indent=2))
        print("CloudFormation template written to " + self.args.output_file)

        if script_for_tag:
            print("Found tags on config rules. Cloudformation do not support tagging config rule at the moment")
            print("Generating script for config rules tags")
            script_for_tag = "#! /bin/bash \n" + script_for_tag
            if self.args.tag_config_rules_script:
                with open(self.args.tag_config_rules_script, "w") as rsh:
                    rsh.write(script_for_tag)
            else:
                print("=========SCRIPT=========")
                print(script_for_tag)
                print("you can use flag [--tag-config-rules-script <file path> ] to output the script")

    def create_region_set(self):
        self.args = get_create_region_set_parser().parse_args(self.args.command_args, self.args)
        output_file = self.args.output_file
        output_dict = {
            "default": ["us-east-1", "us-west-1", "eu-north-1", "ap-southeast-1"],
            "aws-cn-region-set": ["cn-north-1", "cn-northwest-1"],
        }
        with open(f"{output_file}.yaml", "w+") as file:
            yaml.dump(output_dict, file, default_flow_style=False)

    def __parse_rule_args(self, is_required):
        self.args = get_rule_parser(is_required, self.args.command).parse_args(self.args.command_args, self.args)

        if self.args.rulename:
            if len(self.args.rulename) > 128:
                print("Rule names must be 128 characters or fewer.")
                sys.exit(1)

        resource_type_error = ""
        if self.args.resource_types:
            for resource_type in self.args.resource_types.split(","):
                if resource_type not in ACCEPTED_RESOURCE_TYPES:
                    resource_type_error = (
                        resource_type_error + ' "' + resource_type + '" not found in list of accepted resource types.'
                    )
            if resource_type_error:
                print(resource_type_error)
                if not self.args.skip_supported_resource_check:
                    sys.exit(1)
                else:
                    print(
                        "Skip-Supported-Resource-Check Flag set (--skip-supported-resource-check), ignoring missing resource type error."
                    )

        if is_required and not self.args.resource_types and not self.args.maximum_frequency:
            print("You must specify either a resource type trigger or a maximum frequency.")
            sys.exit(1)

        if self.args.input_parameters:
            try:
                input_params_dict = json.loads(self.args.input_parameters, strict=False)
            except Exception as e:
                print("Failed to parse input parameters.")
                sys.exit(1)

        if self.args.optional_parameters:
            try:
                optional_params_dict = json.loads(self.args.optional_parameters, strict=False)
            except Exception as e:
                print("Failed to parse optional parameters.")
                sys.exit(1)

        if self.args.rulesets:
            self.args.rulesets = self.args.rulesets.split(",")

    def __parse_test_args(self):
        self.args = get_test_parser(self.args.command).parse_args(self.args.command_args, self.args)

        if self.args.all and self.args.rulename:
            print("You may specify either specific rules or --all, but not both.")
            return 1

        if self.args.rulesets:
            self.args.rulesets = self.args.rulesets.split(",")

        return self.args

    def __parse_deploy_args(self, ForceArgument=False):

        self.args = get_deployment_parser(ForceArgument).parse_args(self.args.command_args, self.args)

        ### Validate inputs ###
        if self.args.stack_name and not self.args.functions_only:
            print("--stack-name can only be specified when using the --functions-only feature.")
            sys.exit(1)

        # Make sure we're not exceeding Layer limits
        if self.args.lambda_layers:
            layer_count = len(self.args.lambda_layers.split(","))
            if layer_count > 5:
                print("You may only specify 5 Lambda Layers.")
                sys.exit(1)
            if self.args.rdklib_layer_arn or self.args.generated_lambda_layer and layer_count > 4:
                print("Because you have selected a 'lib' runtime You may only specify 4 additional Lambda Layers.")
                sys.exit(1)

        # RDKLib version and RDKLib Layer ARN/Generated RDKLib Layer are mutually exclusive.
        if "rdk_lib_version" in self.args and (self.args.rdklib_layer_arn or self.args.generated_lambda_layer):
            print(
                "Specify EITHER an RDK Lib version to use the official release OR a specific Layer ARN to use a custom implementation."
            )
            sys.exit(1)

        # RDKLib version and RDKLib Layer ARN/Generated RDKLib Layer are mutually exclusive.
        if self.args.rdklib_layer_arn and self.args.generated_lambda_layer:
            print("Specify EITHER an RDK Lib Layer ARN OR the generated lambda layer flag.")
            sys.exit(1)

        # Check rule names to make sure none are too long.  This is needed to catch Rules created before length constraint was added.
        if self.args.rulename:
            for name in self.args.rulename:
                if len(name) > 128:
                    print(
                        "Error: Found Rule with name over 128 characters: {} \n Recreate the Rule with a shorter name.".format(
                            name
                        )
                    )
                    sys.exit(1)

        if self.args.functions_only and not self.args.stack_name:
            self.args.stack_name = "RDK-Config-Rule-Functions"

        if self.args.rulesets:
            self.args.rulesets = self.args.rulesets.split(",")

    def __parse_deploy_organization_args(self, ForceArgument=False):

        self.args = get_deployment_organization_parser(ForceArgument).parse_args(self.args.command_args, self.args)

        ### Validate inputs ###
        if self.args.stack_name and not self.args.functions_only:
            print("--stack-name can only be specified when using the --functions-only feature.")
            sys.exit(1)

        # Make sure we're not exceeding Layer limits
        if self.args.lambda_layers:
            layer_count = len(self.args.lambda_layers.split(","))
            if layer_count > 5:
                print("You may only specify 5 Lambda Layers.")
                sys.exit(1)
            if self.args.rdklib_layer_arn and layer_count > 4:
                print("Because you have selected a 'lib' runtime You may only specify 4 additional Lambda Layers.")
                sys.exit(1)

        # RDKLib version and RDKLib Layer ARN are mutually exclusive.
        if "rdk_lib_version" in self.args and "rdklib_layer_arn" in self.args:
            print(
                "Specify EITHER an RDK Lib version to use the official release OR a specific Layer ARN to use a custom implementation."
            )
            sys.exit(1)

        # Check rule names to make sure none are too long.  This is needed to catch Rules created before length constraint was added.
        if self.args.rulename:
            for name in self.args.rulename:
                if len(name) > 128:
                    print(
                        "Error: Found Rule with name over 128 characters: {} \n Recreate the Rule with a shorter name.".format(
                            name
                        )
                    )
                    sys.exit(1)

        if self.args.functions_only and not self.args.stack_name:
            self.args.stack_name = "RDK-Config-Rule-Functions"

        if self.args.rulesets:
            self.args.rulesets = self.args.rulesets.split(",")

    def __parse_export_args(self, ForceArgument=False):

        self.args = get_export_parser(ForceArgument).parse_args(self.args.command_args, self.args)

        # Check rule names to make sure none are too long.  This is needed to catch Rules created before length constraint was added.
        if self.args.rulename:
            for name in self.args.rulename:
                if len(name) > 128:
                    print(
                        "Error: Found Rule with name over 128 characters: {} \n Recreate the Rule with a shorter name.".format(
                            name
                        )
                    )
                    sys.exit(1)
