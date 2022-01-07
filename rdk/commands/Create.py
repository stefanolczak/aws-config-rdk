import argparse
import fileinput
import json
import os
import sys
from os import path

import shutil
from rdk import ACCEPTED_RESOURCE_TYPES, ROOT_DIR, RULE_HANDLER, RULES_DIR, UTIL_FILENAME
from rdk.Util import Util


def create(args):
    args = get_create_parser().parse_args(args.command_args, args)

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
                    "Skip-Supported-Resource-Check Flag set (--skip-supported-resource-check), ignoring missing resource type error."
                )

    if not args.resource_types and not args.maximum_frequency:
        print("You must specify either a resource type trigger or a maximum frequency.")
        sys.exit(1)

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

    print("Running create!")

    if not args.source_identifier:
        if not args.runtime:
            print("Runtime is required for 'create' command.")
            return 1

        extension_mapping = {
            "java8": ".java",
            "python3.6": ".py",
            "python3.6-managed": ".py",
            "python3.6-lib": ".py",
            "python3.7": ".py",
            "python3.7-lib": ".py",
            "python3.8": ".py",
            "python3.8-lib": ".py",
            "python3.9": ".py",
            "python3.9-lib": ".py",
            "nodejs4.3": ".js",
            "dotnetcore1.0": "cs",
            "dotnetcore2.0": "cs",
        }
        if args.runtime not in extension_mapping:
            print("rdk does not support that runtime yet.")

    # if not args.maximum_frequency:
    #    args.maximum_frequency = "TwentyFour_Hours"
    #    print("Defaulting to TwentyFour_Hours Maximum Frequency.")

    # create rule directory.
    rule_path = os.path.join(os.getcwd(), RULES_DIR, args.rulename)
    if os.path.exists(rule_path):
        print("Local Rule directory already exists.")
        return 1

    try:
        os.makedirs(os.path.join(os.getcwd(), RULES_DIR, args.rulename))

        if not args.source_identifier:
            # copy rule template into rule directory
            if args.runtime == "java8":
                Util.create_java_rule(args)
            elif args.runtime in ["dotnetcore1.0", "dotnetcore2.0"]:
                Util.create_dotnet_rule(args)
            else:
                src = os.path.join(
                    path.dirname(ROOT_DIR),
                    "template",
                    "runtime",
                    args.runtime,
                    RULE_HANDLER + extension_mapping[args.runtime],
                )
                dst = os.path.join(
                    os.getcwd(),
                    RULES_DIR,
                    args.rulename,
                    args.rulename + extension_mapping[args.runtime],
                )
                shutil.copyfile(src, dst)
                f = fileinput.input(files=dst, inplace=True)
                for line in f:
                    if args.runtime in [
                        "python3.6-lib",
                        "python3.7-lib",
                        "python3.8-lib",
                        "python3.9-lib",
                    ]:
                        if args.resource_types:
                            applicable_resource_list = ""
                            for resource_type in args.resource_types.split(","):
                                applicable_resource_list += "'" + resource_type + "', "
                            print(
                                line.replace("<%RuleName%>", args.rulename)
                                .replace(
                                    "<%ApplicableResources1%>",
                                    "\nAPPLICABLE_RESOURCES = [" + applicable_resource_list[:-2] + "]\n",
                                )
                                .replace(
                                    "<%ApplicableResources2%>",
                                    ", APPLICABLE_RESOURCES",
                                ),
                                end="",
                            )
                        else:
                            print(
                                line.replace("<%RuleName%>", args.rulename)
                                .replace("<%ApplicableResources1%>", "")
                                .replace("<%ApplicableResources2%>", ""),
                                end="",
                            )
                    else:
                        print(line.replace("<%RuleName%>", args.rulename), end="")
                f.close()

                src = os.path.join(
                    path.dirname(ROOT_DIR),
                    "template",
                    "runtime",
                    args.runtime,
                    "rule_test" + extension_mapping[args.runtime],
                )
                if os.path.exists(src):
                    dst = os.path.join(
                        os.getcwd(),
                        RULES_DIR,
                        args.rulename,
                        args.rulename + "_test" + extension_mapping[args.runtime],
                    )
                    shutil.copyfile(src, dst)
                    f = fileinput.input(files=dst, inplace=True)
                    for line in f:
                        print(line.replace("<%RuleName%>", args.rulename), end="")
                    f.close()

                src = os.path.join(
                    path.dirname(ROOT_DIR),
                    "template",
                    "runtime",
                    args.runtime,
                    UTIL_FILENAME + extension_mapping[args.runtime],
                )
                if os.path.exists(src):
                    dst = os.path.join(
                        os.getcwd(),
                        RULES_DIR,
                        args.rulename,
                        UTIL_FILENAME + extension_mapping[args.runtime],
                    )
                    shutil.copyfile(src, dst)

        # Write the parameters to a file in the rule directory.
        Util.populate_params(args)

        print("Local Rule files created.")
    except Exception as e:
        print("Error during create: " + str(e))
        print("Rolling back...")

        shutil.rmtree(rule_path)

        raise e
    return 0


def get_create_parser():

    usage_string = (
        "[ --resource-types <resource types> | --maximum-frequency <max execution frequency> ]"
        " [optional configuration flags] [--runtime <runtime>] [--rulesets <RuleSet tags>]"
    )

    parser = argparse.ArgumentParser(
        prog="rdk create",
        usage="rdk create" + " <rulename> " + usage_string,
        description=(
            "Rules are stored in their own directory along with their metadata.  This command is used to create"
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
