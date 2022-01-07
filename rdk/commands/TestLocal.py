import argparse
import base64
import datetime
import json
import os
from os import path

from rdk import EVENT_TEMPLATE_FILENAME
from rdk.Util import Util


def test_local(args):
    print("Running test_remote!")
    args = get_test_parser(args.command).parse_args(args.command_args, args)

    if args.all and args.rulename:
        print("You may specify either specific rules or --all, but not both.")
        return 1

    if args.rulesets:
        args.rulesets = args.rulesets.split(",")

    # Construct our list of rules to test.
    rule_names = Util.get_rule_list_for_command(args)

    # Create our Lambda client.
    my_session = Util.get_boto_session(args)
    my_lambda_client = my_session.client("lambda")

    for rule_name in rule_names:
        print("Testing " + rule_name)

        # Get CI JSON from either the CLI or one of the stored templates.
        my_cis = self.__get_test_CIs(rule_name)

        my_parameters = {}
        if args.test_parameters:
            my_parameters = json.loads(args.test_parameters)

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

            # If there's an error dump execution logs to the terminal, if not print out the value returned by the
            # lambda function.
            if "FunctionError" in result:
                print(base64.b64decode(str(result["LogResult"])))
            else:
                print("\t\t\t" + result["Payload"].read())
                if args.verbose:
                    print(base64.b64decode(str(result["LogResult"])))
    return 0


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
