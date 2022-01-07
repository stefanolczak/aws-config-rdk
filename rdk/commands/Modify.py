import argparse
import json
import sys

from rdk import ACCEPTED_RESOURCE_TYPES
from rdk.Util import Util


def modify(args):
    args = get_rule_parser(args.command).parse_args(args.command_args, args)

    if args.rulename:
        if len(args.rulename) > 128:
            print("Rule names must be 128 characters or fewer.")
            sys.exit(1)

    resource_type_error = ""
    if args.resource_types:
        for resource_type in args.resource_types.split(","):
            if resource_type not in ACCEPTED_RESOURCE_TYPES:
                resource_type_error = (
                    resource_type_error + ' "' + resource_type + '" not found in list of accepted resource types.'
                )
        if resource_type_error:
            print(resource_type_error)
            if not args.skip_supported_resource_check:
                sys.exit(1)
            else:
                print(
                    "Skip-Supported-Resource-Check Flag set (--skip-supported-resource-check), ignoring missing"
                    " resource type error."
                )

    if args.input_parameters:
        try:
            json.loads(args.input_parameters, strict=False)
        except Exception:
            print("Failed to parse input parameters.")
            sys.exit(1)

    if args.optional_parameters:
        try:
            json.loads(args.optional_parameters, strict=False)
        except Exception:
            print("Failed to parse optional parameters.")
            sys.exit(1)

    if args.rulesets:
        args.rulesets = args.rulesets.split(",")

    print("Running modify!")

    args.rulename = Util.clean_rule_name(args.rulename)

    # Get existing parameters
    old_params, tags = Util.get_rule_parameters(args.rulename)

    if not args.custom_lambda_name and "CustomLambdaName" in old_params:
        args.custom_lambda_name = old_params["CustomLambdaName"]

    if not args.resource_types and "SourceEvents" in old_params:
        args.resource_types = old_params["SourceEvents"]

    if not args.maximum_frequency and "SourcePeriodic" in old_params:
        args.maximum_frequency = old_params["SourcePeriodic"]

    if not args.runtime and old_params["SourceRuntime"]:
        args.runtime = old_params["SourceRuntime"]

    if not args.input_parameters and "InputParameters" in old_params:
        args.input_parameters = old_params["InputParameters"]

    if not args.optional_parameters and "OptionalParameters" in old_params:
        args.optional_parameters = old_params["OptionalParameters"]

    if not args.source_identifier and "SourceIdentifier" in old_params:
        args.source_identifier = old_params["SourceIdentifier"]

    if not args.tags and tags:
        args.tags = tags

    if not args.remediation_action and "Remediation" in old_params:
        params = old_params["Remediation"]
        args.auto_remediate = params.get("Automatic", "")
        execution_controls = params.get("ExecutionControls", "")
        if execution_controls:
            ssm_controls = execution_controls["SsmControls"]
            args.remediation_concurrent_execution_percent = ssm_controls.get("ConcurrentExecutionRatePercentage", "")
            args.remediation_error_rate_percent = ssm_controls.get("ErrorPercentage", "")
        args.remediation_parameters = json.dumps(params["Parameters"]) if params.get("Parameters") else None
        args.auto_remediation_retry_attempts = params.get("MaximumAutomaticAttempts", "")
        args.auto_remediation_retry_time = params.get("RetryAttemptSeconds", "")
        args.remediation_action = params.get("TargetId", "")
        args.remediation_action_version = params.get("TargetVersion", "")

    if "RuleSets" in old_params:
        if not args.rulesets:
            args.rulesets = old_params["RuleSets"]

    # Write the parameters to a file in the rule directory.
    Util.populate_params()

    print("Modified Rule '" + args.rulename + "'.  Use the `deploy` command to push your changes to AWS.")


def get_rule_parser():
    usage_string = (
        "[--runtime <runtime>] [--resource-types <resource types>] [--maximum-frequency <max execution frequency>]"
        " [--input-parameters <parameter JSON>] [--tags <tags JSON>] [--rulesets <RuleSet tags>]"
    )
    parser = argparse.ArgumentParser(
        prog="rdk modify",
        usage="rdk modify <rulename> " + usage_string,
        description=(
            "Rules are stored in their own directory along with their metadata.  This command is used to modify"
            " the Rule and metadata."
        ),
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
