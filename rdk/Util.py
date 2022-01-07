import fnmatch
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from os import path

import boto3
from boto3 import session
from botocore.exceptions import ClientError, EndpointConnectionError

from rdk import (
    PARAMETER_FILE_NAME,
    RULES_DIR,
    TEST_CI_FILENAME,
    RDKLIB_LAYER_VERSION,
    RDKLIB_ARN_STRING,
    RDKLIB_LAYER_SAR_ID,
    EXAMPLE_CI_DIR,
)


class Util:
    @staticmethod
    def generate_terraform_shell(args):
        return ""

    @staticmethod
    def generate_rule_terraform(rule_name):
        return ""

    @staticmethod
    def generate_rule_terraform_params(rule_name):
        return ""

    @staticmethod
    def remove_ruleset_rule(ruleset, rulename, args):
        params, tags = Util.get_rule_parameters(rulename)
        if "RuleSets" in params:
            if args.ruleset in params["RuleSets"]:
                params["RuleSets"].remove(args.ruleset)
            else:
                print("Rule " + rulename + " is not in RuleSet " + ruleset)
        else:
            print("Rule " + rulename + " is not in any RuleSets")

        Util.write_params_file(rulename, params, tags)

        print(rulename + " removed from RuleSet " + ruleset)

    @staticmethod
    def add_ruleset_rule(ruleset, rulename, args):
        params, tags = Util.get_rule_parameters(rulename)
        if "RuleSets" in params:
            if args.ruleset in params["RuleSets"]:
                print("Rule is already in the specified RuleSet.")
            else:
                params["RuleSets"].append(args.ruleset)
        else:
            rulesets = [args.ruleset]
            params["RuleSets"] = rulesets

        Util.write_params_file(rulename, params, tags)

        print(rulename + " added to RuleSet " + ruleset)

    @staticmethod
    def list_rulesets(args):
        rulesets = []
        rules = []

        for obj_name in os.listdir("."):
            # print(obj_name)
            params_file_path = os.path.join(".", obj_name, PARAMETER_FILE_NAME)
            if os.path.isfile(params_file_path):
                parameters_file = open(params_file_path, "r")
                my_params = json.load(parameters_file)
                parameters_file.close()
                if "RuleSets" in my_params["Parameters"]:
                    rulesets.extend(my_params["Parameters"]["RuleSets"])

                    if args.ruleset in my_params["Parameters"]["RuleSets"]:
                        # print("Found rule! " + obj_name)
                        rules.append(obj_name)

        if args.ruleset:
            rules.sort()
            print("Rules in", args.ruleset, ": ")
            print(*rules, sep="\n")
        else:
            deduped = list(set(rulesets))
            deduped.sort()
            print("RuleSets: ", *deduped)

    @staticmethod
    def get_template_dir():
        return os.path.join(path.dirname(__file__), "template")

    @staticmethod
    def create_test_suite(test_dir):
        tests = []
        for (top, dirs, filenames) in os.walk(test_dir):
            for filename in fnmatch.filter(filenames, "*_test.py"):
                print(filename)
                sys.path.append(top)
                tests.append(filename[:-3])

        suites = [unittest.defaultTestLoader.loadTestsFromName(test) for test in tests]
        for suite in suites:
            print("Debug!")
            print(suite)

        return unittest.TestSuite(suites)

    @staticmethod
    def clean_rule_name(rule_name):
        output = rule_name
        if output[-1:] == "/":
            print("Removing trailing '/'")
            output = output.rstrip("/")

        return output

    @staticmethod
    def create_java_rule(args):
        src = os.path.join(path.dirname(__file__), "template", "runtime", "java8", "src")
        dst = os.path.join(os.getcwd(), RULES_DIR, args.rulename, "src")
        shutil.copytree(src, dst)

        src = os.path.join(path.dirname(__file__), "template", "runtime", "java8", "jars")
        dst = os.path.join(os.getcwd(), RULES_DIR, args.rulename, "jars")
        shutil.copytree(src, dst)

        src = os.path.join(path.dirname(__file__), "template", "runtime", "java8", "build.gradle")
        dst = os.path.join(os.getcwd(), RULES_DIR, args.rulename, "build.gradle")
        shutil.copyfile(src, dst)

    @staticmethod
    def create_dotnet_rule(args):
        runtime_path = os.path.join(path.dirname(__file__), "template", "runtime", args.runtime)
        dst_path = os.path.join(os.getcwd(), RULES_DIR, args.rulename)
        for obj in os.listdir(runtime_path):
            src = os.path.join(runtime_path, obj)
            dst = os.path.join(dst_path, obj)
            if os.path.isfile(src):
                shutil.copyfile(src, dst)
            else:
                shutil.copytree(src, dst)

    @staticmethod
    def print_log_event(event):
        time_string = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(event["timestamp"] / 1000))

        rows = 24
        columns = 80
        try:
            rows, columns = os.popen("stty size", "r").read().split()
        except ValueError:
            # This was probably being run in a headless test environment which had no stty.
            print("Using default terminal rows and columns.")

        line_wrap = int(columns) - 22
        message_lines = str(event["message"]).splitlines()
        formatted_lines = []

        for line in message_lines:
            line = line.replace("\t", "    ")
            formatted_lines.append("\n".join(line[i : i + line_wrap] for i in range(0, len(line), line_wrap)))

        message_string = "\n".join(formatted_lines)
        message_string = message_string.replace("\n", "\n                      ")

        print(time_string + " - " + message_string)

    @staticmethod
    def get_log_events(my_client, log_streams, number_of_events):
        event_count = 0
        log_events = []
        for stream in log_streams["logStreams"]:
            # Retrieve the logs for this stream.
            events = my_client.get_log_events(
                logGroupName=Util.get_log_group_name(),
                logStreamName=stream["logStreamName"],
                limit=int(number_of_events),
            )
            # Go through the logs and add events to my output array.
            for event in events["events"]:
                log_events.append(event)
                event_count = event_count + 1

                # Once we have enough events, stop.
                if event_count >= number_of_events:
                    return log_events

        # If more records were requested than exist, return as many as we found.
        return log_events

    @staticmethod
    def get_log_group_name(args):
        params, cfn_tags = Util.get_rule_parameters(args.rulename)

        return "/aws/lambda/" + Util.get_lambda_name(args.rulename, params)

    @staticmethod
    def get_boto_session(args):
        session_args = {}

        if args.region:
            session_args["region_name"] = args.region

        if args.profile:
            session_args["profile_name"] = args.profile
        elif args.access_key_id and args.secret_access_key:
            session_args["aws_access_key_id"] = args.access_key_id
            session_args["aws_secret_access_key"] = args.secret_access_key

        return boto3.session.Session(**session_args)

    @staticmethod
    def get_caller_identity_details(session):
        my_sts = session.client("sts")
        response = my_sts.get_caller_identity()
        arn_split = response["Arn"].split(":")

        return {
            "account_id": response["Account"],
            "partition": arn_split[1],
            "region": arn_split[3],
        }

    @staticmethod
    def get_stack_name_from_rule_name(rule_name):
        output = rule_name.replace("_", "")

        return output

    @staticmethod
    def get_alphanumeric_rule_name(rule_name):
        output = rule_name.replace("_", "").replace("-", "")

        return output

    @staticmethod
    def get_rule_list_for_command(args, Command="deploy"):
        rule_names = []
        if args.all:
            for obj_name in os.listdir("."):
                obj_path = os.path.join(".", obj_name)
                if os.path.isdir(obj_path) and not obj_name == "rdk":
                    for file_name in os.listdir(obj_path):
                        if obj_name not in rule_names:
                            if os.path.exists(os.path.join(obj_path, "parameters.json")):
                                rule_names.append(obj_name)
                            else:
                                if file_name.split(".")[0] == obj_name:
                                    rule_names.append(obj_name)
                                if os.path.exists(
                                    os.path.join(
                                        obj_path,
                                        "src",
                                        "main",
                                        "java",
                                        "com",
                                        "rdk",
                                        "RuleCode.java",
                                    )
                                ):
                                    rule_names.append(obj_name)
                                if os.path.exists(os.path.join(obj_path, "RuleCode.cs")):
                                    rule_names.append(obj_name)
        elif args.rulesets:
            for obj_name in os.listdir("."):
                params_file_path = os.path.join(".", obj_name, PARAMETER_FILE_NAME)
                if os.path.isfile(params_file_path):
                    parameters_file = open(params_file_path, "r")
                    my_params = json.load(parameters_file)
                    parameters_file.close()
                    if "RuleSets" in my_params["Parameters"]:
                        s_input = set(args.rulesets)
                        s_params = set(my_params["Parameters"]["RuleSets"])
                        if s_input.intersection(s_params):
                            rule_names.append(obj_name)
        elif args.rulename:
            for rule_name in args.rulename:
                cleaned_rule_name = Util.clean_rule_name(rule_name)
                if os.path.isdir(cleaned_rule_name):
                    rule_names.append(cleaned_rule_name)
        else:
            print('Invalid Option: Specify Rule Name or RuleSet. Run "rdk %s -h" for more info.' % (Command))
            sys.exit(1)

        if len(rule_names) == 0:
            print("No matching rule directories found.")
            sys.exit(1)

        # Check rule names to make sure none are too long.  This is needed to catch Rules created before length constraint was added.
        for name in rule_names:
            if len(name) > 128:
                print(
                    "Error: Found Rule with name over 128 characters: {} \n Recreate the Rule with a shorter name.".format(
                        name
                    )
                )
                sys.exit(1)

        return rule_names

    @staticmethod
    def get_rule_parameters(rule_name):
        params_file_path = os.path.join(os.getcwd(), RULES_DIR, rule_name, PARAMETER_FILE_NAME)

        try:
            parameters_file = open(params_file_path, "r")
        except IOError as e:
            print("Failed to open parameters file for rule '{}'".format(rule_name))
            print(e.message)
            sys.exit(1)

        my_json = {}

        try:
            my_json = json.load(parameters_file)
        except ValueError as ve:  # includes simplejson.decoder.JSONDecodeError
            print("Failed to decode JSON in parameters file for Rule {}".format(rule_name))
            print(ve.message)
            parameters_file.close()
            sys.exit(1)
        except Exception as e:
            print("Error loading parameters file for Rule {}".format(rule_name))
            print(e.message)
            parameters_file.close()
            sys.exit(1)

        parameters_file.close()

        my_tags = my_json.get("Tags", None)

        # Needed for backwards compatibility with earlier versions of parameters file
        if my_tags is None:
            my_tags = "[]"
            my_json["Parameters"]["Tags"] = my_tags

        # as my_tags was returned as a string in earlier versions, convert it back to a list
        if isinstance(my_tags, str):
            my_tags = json.loads(my_tags)

        return my_json["Parameters"], my_tags

    @staticmethod
    def package_function_code(rule_name, params, my_session):
        if params["SourceRuntime"] == "java8":
            # Do java build and package.
            print("Running Gradle Build for " + rule_name)
            working_dir = os.path.join(os.getcwd(), RULES_DIR, rule_name)
            command = ["gradle", "build"]
            subprocess.call(command, cwd=working_dir)

            # set source as distribution zip
            s3_src = os.path.join(
                os.getcwd(),
                RULES_DIR,
                rule_name,
                "build",
                "distributions",
                rule_name + ".zip",
            )
        elif params["SourceRuntime"] in ["dotnetcore1.0", "dotnetcore2.0"]:
            print("Packaging " + rule_name)
            working_dir = os.path.join(os.getcwd(), RULES_DIR, rule_name)
            commands = [["dotnet", "restore"]]

            app_runtime = "netcoreapp1.0"
            if params["SourceRuntime"] == "dotnetcore2.0":
                app_runtime = "netcoreapp2.0"

            commands.append(["dotnet", "lambda", "package", "-c", "Release", "-f", app_runtime])

            for command in commands:
                subprocess.call(command, cwd=working_dir)

            # Remove old zip file if it already exists
            package_file_dst = os.path.join(rule_name, rule_name + ".zip")
            Util.delete_package_file(package_file_dst)

            # Create new package in temp directory, copy to rule directory
            # This copy avoids the archiver trying to include the output zip in itself
            s3_src_dir = os.path.join(
                os.getcwd(),
                RULES_DIR,
                rule_name,
                "bin",
                "Release",
                app_runtime,
                "publish",
            )
            tmp_src = shutil.make_archive(
                os.path.join(tempfile.gettempdir(), rule_name + my_session.region_name),
                "zip",
                s3_src_dir,
            )
            if not (os.path.exists(package_file_dst)):
                shutil.copy(tmp_src, package_file_dst)
            s3_src = os.path.abspath(package_file_dst)
            Util.delete_package_file(tmp_src)

        else:
            print("Zipping " + rule_name)
            # Remove old zip file if it already exists
            package_file_dst = os.path.join(rule_name, rule_name + ".zip")
            Util.delete_package_file(package_file_dst)

            # zip rule code files and upload to s3 bucket
            s3_src_dir = os.path.join(os.getcwd(), RULES_DIR, rule_name)
            tmp_src = shutil.make_archive(
                os.path.join(tempfile.gettempdir(), rule_name + my_session.region_name),
                "zip",
                s3_src_dir,
            )
            if not (os.path.exists(package_file_dst)):
                shutil.copy(tmp_src, package_file_dst)
            s3_src = os.path.abspath(package_file_dst)
            Util.delete_package_file(tmp_src)

        s3_dst = "/".join((rule_name, rule_name + ".zip"))

        print("Zipping complete.")

        return s3_dst

    @staticmethod
    def populate_params(args):
        my_session = Util.get_boto_session(args)

        my_input_params = {}

        if args.input_parameters:
            # Parse the input parameters to make sure it's valid json.  Be tolerant of quote usage in the input string.
            try:
                my_input_params = json.loads(args.input_parameters, strict=False)
            except Exception as e:
                print(
                    "Error parsing input parameter JSON.  Make sure your JSON keys and values are enclosed in properly-escaped double quotes and your input-parameters string is enclosed in single quotes."
                )
                raise e

        my_optional_params = {}

        if args.optional_parameters:
            # As above, but with the optional input parameters.
            try:
                my_optional_params = json.loads(args.optional_parameters, strict=False)
            except Exception:
                print(
                    "Error parsing optional input parameter JSON.  Make sure your JSON keys and values are enclosed in properly escaped double quotes and your optional-parameters string is enclosed in single quotes."
                )

        my_tags = []

        if args.tags:
            # As above, but with the optional tag key value pairs.
            try:
                my_tags = json.loads(args.tags, strict=False)
            except Exception as e:
                print(
                    "Error parsing optional tags JSON.  Make sure your JSON keys and values are enclosed in properly escaped double quotes and tags string is enclosed in single quotes."
                )

        my_remediation = {}
        if (
            any(
                getattr(args, arg) is not None
                for arg in [
                    "auto_remediation_retry_attempts",
                    "auto_remediation_retry_time",
                    "remediation_action_version",
                    "remediation_concurrent_execution_percent",
                    "remediation_error_rate_percent",
                    "remediation_parameters",
                ]
            )
            and not args.remediation_action
        ):
            print("Remediation Flags detected but no remediation action (--remediation-action) set")

        if args.remediation_action:
            try:
                my_remediation = Util.generate_remediation_params()
            except Exception as e:
                print("Error parsing remediation configuration.")

        # create config file and place in rule directory
        parameters = {
            "RuleName": args.rulename,
            "Description": args.rulename,
            "SourceRuntime": args.runtime,
            #'CodeBucket': code_bucket_prefix + account_id,
            "CodeKey": args.rulename + my_session.region_name + ".zip",
            "InputParameters": json.dumps(my_input_params),
            "OptionalParameters": json.dumps(my_optional_params),
        }

        if args.custom_lambda_name:
            parameters["CustomLambdaName"] = args.custom_lambda_name

        tags = json.dumps(my_tags)

        if args.resource_types:
            parameters["SourceEvents"] = args.resource_types

        if args.maximum_frequency:
            parameters["SourcePeriodic"] = args.maximum_frequency

        if args.rulesets:
            parameters["RuleSets"] = args.rulesets

        if args.source_identifier:
            parameters["SourceIdentifier"] = args.source_identifier
            parameters["CodeKey"] = None
            parameters["SourceRuntime"] = None

        if my_remediation:
            parameters["Remediation"] = my_remediation

        Util.write_params_file(args.rulename, parameters, tags)

    @staticmethod
    def generate_remediation_params(args):
        params = {}
        if args.auto_remediate:
            params["Automatic"] = args.auto_remediate

        params["ConfigRuleName"] = args.rulename

        ssm_controls = {}
        if args.remediation_concurrent_execution_percent:
            ssm_controls["ConcurrentExecutionRatePercentage"] = args.remediation_concurrent_execution_percent

        if args.remediation_error_rate_percent:
            ssm_controls["ErrorPercentage"] = args.remediation_error_rate_percent

        if ssm_controls:
            params["ExecutionControls"] = {"SsmControls": ssm_controls}

        if args.auto_remediation_retry_attempts:
            params["MaximumAutomaticAttempts"] = args.auto_remediation_retry_attempts

        if args.remediation_parameters:
            params["Parameters"] = json.loads(args.remediation_parameters)

        if args.resource_types and len(args.resource_types.split(",")) == 1:
            params["ResourceType"] = args.resource_types

        if args.auto_remediation_retry_time:
            params["RetryAttemptSeconds"] = args.auto_remediation_retry_time

        params["TargetId"] = args.remediation_action
        params["TargetType"] = "SSM_DOCUMENT"

        if args.remediation_action_version:
            params["TargetVersion"] = args.remediation_action_version

        return params

    @staticmethod
    def write_params_file(rulename, parameters, tags):
        my_params = {"Version": "1.0", "Parameters": parameters, "Tags": tags}
        params_file_path = os.path.join(os.getcwd(), RULES_DIR, rulename, PARAMETER_FILE_NAME)
        parameters_file = open(params_file_path, "w")
        json.dump(my_params, parameters_file, indent=2)
        parameters_file.close()

    @staticmethod
    def wait_for_cfn_stack(cfn_client, stackname, args):
        my_session = Util.get_boto_session(args)
        in_progress = True
        while in_progress:
            my_stacks = []
            response = cfn_client.list_stacks()

            for stack in response["StackSummaries"]:
                if stack["StackName"] == stackname:
                    my_stacks.append(stack)

            # Find the stack (if any) that hasn't already been deleted.
            all_deleted = True
            active_stack = None
            for stack in my_stacks:
                if stack["StackStatus"] != "DELETE_COMPLETE":
                    active_stack = stack
                    all_deleted = False

            # If all stacks have been deleted, clearly we're done!
            if all_deleted:
                in_progress = False
                print(f"[{my_session.region_name}]: CloudFormation stack operation complete.")
                continue
            else:
                if "FAILED" in active_stack["StackStatus"]:
                    in_progress = False
                    print(f"[{my_session.region_name}]: CloudFormation stack operation Failed for " + stackname + ".")
                    if "StackStatusReason" in active_stack:
                        print(f"[{my_session.region_name}]: Reason: " + active_stack["StackStatusReason"])
                elif active_stack["StackStatus"] == "ROLLBACK_COMPLETE":
                    in_progress = False
                    print(
                        f"[{my_session.region_name}]: CloudFormation stack operation Rolled Back for " + stackname + "."
                    )
                    if "StackStatusReason" in active_stack:
                        print(f"[{my_session.region_name}]: Reason: " + active_stack["StackStatusReason"])
                elif "COMPLETE" in active_stack["StackStatus"]:
                    in_progress = False
                    print(f"[{my_session.region_name}]: CloudFormation stack operation complete.")
                else:
                    print(f"[{my_session.region_name}]: Waiting for CloudFormation stack operation to complete...")
                    time.sleep(5)

    @staticmethod
    def get_handler(rule_name, params):
        if "SourceHandler" in params:
            return params["SourceHandler"]
        if params["SourceRuntime"] in [
            "python3.6",
            "python3.6-lib",
            "python3.7",
            "python3.7-lib",
            "python3.8",
            "python3.8-lib",
            "python3.9",
            "python3.9-lib",
            "nodejs4.3",
            "nodejs6.10",
            "nodejs8.10",
        ]:
            return rule_name + ".lambda_handler"
        elif params["SourceRuntime"] in ["java8"]:
            return "com.rdk.RuleUtil::handler"
        elif params["SourceRuntime"] in ["dotnetcore1.0", "dotnetcore2.0"]:
            return "csharp7.0::Rdk.CustomConfigHandler::FunctionHandler"

    @staticmethod
    def get_runtime_string(params):
        if params["SourceRuntime"] in [
            "python3.6-lib",
            "python3.6-managed",
            "python3.7-lib",
            "python3.8-lib",
            "python3.9-lib",
        ]:
            runtime = params["SourceRuntime"].split("-")
            return runtime[0]

        return params["SourceRuntime"]

    @staticmethod
    def get_test_CIs(rulename, args):
        test_ci_list = []
        if args.test_ci_types:
            print("\tTesting with generic CI for supplied Resource Type(s)")
            ci_types = args.test_ci_types.split(",")
            for ci_type in ci_types:
                my_test_ci = TestCI(ci_type)
                test_ci_list.append(my_test_ci.get_json())
        else:
            # Check to see if there is a test_ci.json file in the Rule directory
            tests_path = os.path.join(os.getcwd(), RULES_DIR, rulename, TEST_CI_FILENAME)
            if os.path.exists(tests_path):
                print("\tTesting with CI's provided in test_ci.json file. NOT YET IMPLEMENTED")  # TODO
            #    test_ci_list _load_cis_from_file(tests_path)
            else:
                print("\tTesting with generic CI for configured Resource Type(s)")
                my_rule_params, my_rule_tags = Util.get_rule_parameters(rulename)
                ci_types = str(my_rule_params["SourceEvents"]).split(",")
                for ci_type in ci_types:
                    my_test_ci = TestCI(ci_type)
                    test_ci_list.append(my_test_ci.get_json())

        return test_ci_list

    @staticmethod
    def get_lambda_arn_for_stack(stack_name, args):
        # create custom session based on whatever credentials are available to us
        my_session = Util.get_boto_session(args)

        my_cfn = my_session.client("cloudformation")

        # Since CFN won't detect changes to the lambda code stored in S3 as a reason to update the stack, we need to manually update the code reference in Lambda once the CFN has run.
        Util.wait_for_cfn_stack(my_cfn, stack_name)

        # Lambda function is an output of the stack.
        my_updated_stack = my_cfn.describe_stacks(StackName=stack_name)
        cfn_outputs = my_updated_stack["Stacks"][0]["Outputs"]
        my_lambda_arn = "NOTFOUND"
        for output in cfn_outputs:
            if output["OutputKey"] == "RuleCodeLambda":
                my_lambda_arn = output["OutputValue"]

        if my_lambda_arn == "NOTFOUND":
            print(f"[{my_session.region_name}]: Could not read CloudFormation stack output to find Lambda function.")
            sys.exit(1)

        return my_lambda_arn

    @staticmethod
    def get_lambda_name(rule_name, params):
        if "CustomLambdaName" in params:
            lambda_name = params["CustomLambdaName"]
            if len(lambda_name) > 64:
                print(
                    "Error: Found Rule's Lambda function with name over 64 characters: {} \n Recreate the lambda name with a shorter name.".format(
                        lambda_name
                    )
                )
                sys.exit(1)
            return lambda_name
        else:
            lambda_name = "RDK-Rule-Function-" + Util.get_stack_name_from_rule_name(rule_name)
            if len(lambda_name) > 64:
                print(
                    "Error: Found Rule's Lambda function with name over 64 characters: {} \n Recreate the rule with a shorter name or with CustomLambdaName attribute in parameter.json. If you are using 'rdk create', you can add '--custom-lambda-name <your lambda name>' to create your RDK rules".format(
                        lambda_name
                    )
                )
                sys.exit(1)
            return lambda_name

    @staticmethod
    def get_lambda_arn_for_rule(rule_name, partition, region, account, params):
        return "arn:{}:lambda:{}:{}:function:{}".format(
            partition, region, account, Util.get_lambda_name(rule_name, params)
        )

    @staticmethod
    def delete_package_file(file):
        try:
            os.remove(file)
        except OSError:
            pass

    @staticmethod
    def upload_function_code(rule_name, params, account_id, session, code_bucket_name):
        if params["SourceRuntime"] == "java8":
            # Do java build and package.
            print(f"[{session.region_name}]: Running Gradle Build for " + rule_name)
            working_dir = os.path.join(os.getcwd(), RULES_DIR, rule_name)
            command = ["gradle", "build"]
            subprocess.call(command, cwd=working_dir)

            # set source as distribution zip
            s3_src = os.path.join(
                os.getcwd(),
                RULES_DIR,
                rule_name,
                "build",
                "distributions",
                rule_name + session.region_name + ".zip",
            )
            s3_dst = "/".join((rule_name, rule_name + ".zip"))

            my_s3 = session.resource("s3")

            print(f"[{session.region_name}]: Uploading " + rule_name)
            my_s3.meta.client.upload_file(s3_src, code_bucket_name, s3_dst)
            print(f"[{session.region_name}]: Upload complete.")

        elif params["SourceRuntime"] in ["dotnetcore1.0", "dotnetcore2.0"]:
            print("Packaging " + rule_name)
            working_dir = os.path.join(os.getcwd(), RULES_DIR, rule_name)
            commands = [["dotnet", "restore"]]

            app_runtime = "netcoreapp1.0"
            if params["SourceRuntime"] == "dotnetcore2.0":
                app_runtime = "netcoreapp2.0"

            commands.append(["dotnet", "lambda", "package", "-c", "Release", "-f", app_runtime])

            for command in commands:
                subprocess.call(command, cwd=working_dir)

            # Remove old zip file if it already exists
            package_file_dst = os.path.join(rule_name, rule_name + ".zip")
            Util.delete_package_file(package_file_dst)

            # Create new package in temp directory, copy to rule directory
            # This copy avoids the archiver trying to include the output zip in itself
            s3_src_dir = os.path.join(
                os.getcwd(),
                RULES_DIR,
                rule_name,
                "bin",
                "Release",
                app_runtime,
                "publish",
            )
            tmp_src = shutil.make_archive(
                os.path.join(tempfile.gettempdir(), rule_name + session.region_name),
                "zip",
                s3_src_dir,
            )
            s3_dst = "/".join((rule_name, rule_name + ".zip"))

            my_s3 = session.resource("s3")

            print(f"[{session.region_name}]: Uploading " + rule_name)
            my_s3.meta.client.upload_file(tmp_src, code_bucket_name, s3_dst)
            print(f"[{session.region_name}]: Upload complete.")
            if not (os.path.exists(package_file_dst)):
                shutil.copy(tmp_src, package_file_dst)
            Util.delete_package_file(tmp_src)

        else:
            print(f"[{session.region_name}]: Zipping " + rule_name)
            # Remove old zip file if it already exists
            package_file_dst = os.path.join(rule_name, rule_name + ".zip")
            Util.delete_package_file(package_file_dst)

            # zip rule code files and upload to s3 bucket
            s3_src_dir = os.path.join(os.getcwd(), RULES_DIR, rule_name)

            tmp_src = shutil.make_archive(
                os.path.join(tempfile.gettempdir(), rule_name + session.region_name),
                "zip",
                s3_src_dir,
            )

            s3_dst = "/".join((rule_name, rule_name + ".zip"))

            my_s3 = session.resource("s3")

            print(f"[{session.region_name}]: Uploading " + rule_name)
            my_s3.meta.client.upload_file(tmp_src, code_bucket_name, s3_dst)
            print(f"[{session.region_name}]: Upload complete.")
            if not (os.path.exists(package_file_dst)):
                shutil.copy(tmp_src, package_file_dst)
            Util.delete_package_file(tmp_src)

        return s3_dst

    @staticmethod
    def create_remediation_cloudformation_block(remediation_config):
        remediation = {
            "Type": "AWS::Config::RemediationConfiguration",
            "DependsOn": "rdkConfigRule",
            "Properties": remediation_config,
        }

        return remediation

    @staticmethod
    def create_automation_cloudformation_block(ssm_automation, rule_name):
        print("Generate SSM Resources")
        ssm_json_dir = os.path.join(os.getcwd(), ssm_automation["Document"])
        print("Reading SSM JSON From -> " + ssm_json_dir)
        # params_file_path = os.path.join(os.getcwd(), rules_dir, rulename, parameter_file_name)
        ssm_automation_content = open(ssm_json_dir, "r").read()
        ssm_automation_json = json.loads(ssm_automation_content)
        ssm_automation_config = {
            "Type": "AWS::SSM::Document",
            "Properties": {
                "DocumentType": "Automation",
                "Content": ssm_automation_json,
            },
        }

        return ssm_automation_config

    @staticmethod
    def create_automation_iam_cloudformation_block(ssm_automation, rule_name):

        print(
            "Generate IAM Role for SSM Document with these actions",
            str(ssm_automation["IAM"]),
        )

        assume_role_template = {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "ssm.amazonaws.com"},
                    "Action": "sts:AssumeRole",
                }
            ],
        }

        # params_file_path = os.path.join(os.getcwd(), rules_dir, rulename, parameter_file_name)
        ssm_automation_iam_role = {
            "Type": "AWS::IAM::Role",
            "Properties": {
                "Description": "IAM Role to Support Config Remediation for " + rule_name,
                "Path": "/rdk-remediation-role/",
                # "RoleName": {"Fn::Sub": "" + rule_name + "-Remediation-Role-${AWS::Region}"},
                "AssumeRolePolicyDocument": assume_role_template,
            },
        }

        ssm_automation_iam_policy = {
            "Type": "AWS::IAM::Policy",
            "Properties": {
                "PolicyDocument": {
                    "Statement": [
                        {
                            "Action": ssm_automation["IAM"],
                            "Effect": "Allow",
                            "Resource": "*",
                        }
                    ],
                    "Version": "2012-10-17",
                },
                "PolicyName": {"Fn::Sub": "" + rule_name + "-Remediation-Policy-${AWS::Region}"},
                "Roles": [{"Ref": Util.get_alphanumeric_rule_name(rule_name + "Role")}],
            },
        }

        return (ssm_automation_iam_role, ssm_automation_iam_policy)

    @staticmethod
    def create_function_cloudformation_template(args):
        my_session = Util.get_boto_session(args)
        print("Generating CloudFormation template for Lambda Functions!")

        # First add the common elements - description, parameters, and resource section header
        template = {}
        template["AWSTemplateFormatVersion"] = "2010-09-09"
        template[
            "Description"
        ] = "AWS CloudFormation template to create Lambda functions for backing custom AWS Config rules. You will be billed for the AWS resources used if you create a stack from this template."

        parameters = {}
        parameters["SourceBucket"] = {}
        parameters["SourceBucket"]["Description"] = "Name of the S3 bucket that you have stored the rule zip files in."
        parameters["SourceBucket"]["Type"] = "String"
        parameters["SourceBucket"]["MinLength"] = "1"
        parameters["SourceBucket"]["MaxLength"] = "255"

        template["Parameters"] = parameters

        resources = {}

        if args.lambda_role_arn or args.lambda_role_name:
            print("Existing IAM role provided: " + args.lambda_role_arn)
        else:
            print("No IAM role provided, creating a new IAM role for lambda function")
            lambda_role = {}
            lambda_role["Type"] = "AWS::IAM::Role"
            lambda_role["Properties"] = {}
            lambda_role["Properties"]["Path"] = "/rdk/"
            lambda_role["Properties"]["AssumeRolePolicyDocument"] = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Sid": "AllowLambdaAssumeRole",
                        "Effect": "Allow",
                        "Principal": {"Service": "lambda.amazonaws.com"},
                        "Action": "sts:AssumeRole",
                    }
                ],
            }
            lambda_policy_statements = [
                {
                    "Sid": "1",
                    "Action": ["s3:GetObject"],
                    "Effect": "Allow",
                    "Resource": {"Fn::Sub": "arn:${AWS::Partition}:s3:::${SourceBucket}/*"},
                },
                {
                    "Sid": "2",
                    "Action": [
                        "logs:CreateLogGroup",
                        "logs:CreateLogStream",
                        "logs:PutLogEvents",
                        "logs:DescribeLogStreams",
                    ],
                    "Effect": "Allow",
                    "Resource": "*",
                },
                {
                    "Sid": "3",
                    "Action": ["config:PutEvaluations"],
                    "Effect": "Allow",
                    "Resource": "*",
                },
                {
                    "Sid": "4",
                    "Action": ["iam:List*", "iam:Describe*", "iam:Get*"],
                    "Effect": "Allow",
                    "Resource": "*",
                },
                {
                    "Sid": "5",
                    "Action": ["sts:AssumeRole"],
                    "Effect": "Allow",
                    "Resource": "*",
                },
            ]
            if args.lambda_subnets and args.lambda_security_groups:
                vpc_policy = {
                    "Sid": "LambdaVPCAccessExecution",
                    "Action": [
                        "ec2:DescribeNetworkInterfaces",
                        "ec2:DeleteNetworkInterface",
                        "ec2:CreateNetworkInterface",
                    ],
                    "Effect": "Allow",
                    "Resource": "*",
                }
                lambda_policy_statements.append(vpc_policy)
            lambda_role["Properties"]["Policies"] = [
                {
                    "PolicyName": "ConfigRulePolicy",
                    "PolicyDocument": {
                        "Version": "2012-10-17",
                        "Statement": lambda_policy_statements,
                    },
                }
            ]
            lambda_role["Properties"]["ManagedPolicyArns"] = [
                {"Fn::Sub": "arn:${AWS::Partition}:iam::aws:policy/ReadOnlyAccess"}
            ]
            resources["rdkLambdaRole"] = lambda_role

        rule_names = Util.get_rule_list_for_command()
        for rule_name in rule_names:
            alphanum_rule_name = Util.get_alphanumeric_rule_name(rule_name)
            params, tags = Util.get_rule_parameters(rule_name)

            if "SourceIdentifier" in params:
                print("Skipping Managed Rule.")
                continue

            lambda_function = {}
            lambda_function["Type"] = "AWS::Lambda::Function"
            properties = {}
            properties["FunctionName"] = Util.get_lambda_name(rule_name, params)
            properties["Code"] = {
                "S3Bucket": {"Ref": "SourceBucket"},
                "S3Key": rule_name + "/" + rule_name + ".zip",
            }
            properties["Description"] = "Function for AWS Config Rule " + rule_name
            properties["Handler"] = Util.get_handler(rule_name, params)
            properties["MemorySize"] = "256"
            if args.lambda_role_arn or args.lambda_role_name:
                properties["Role"] = args.lambda_role_arn
            else:
                lambda_function["DependsOn"] = "rdkLambdaRole"
                properties["Role"] = {"Fn::GetAtt": ["rdkLambdaRole", "Arn"]}
            properties["Runtime"] = Util.get_runtime_string(params)
            properties["Timeout"] = str(args.lambda_timeout)
            properties["Tags"] = tags
            if args.lambda_subnets and args.lambda_security_groups:
                properties["VpcConfig"] = {
                    "SecurityGroupIds": args.lambda_security_groups.split(","),
                    "SubnetIds": args.lambda_subnets.split(","),
                }
            layers = []
            if args.rdklib_layer_arn:
                layers.append(args.rdklib_layer_arn)
            if args.lambda_layers:
                for layer in args.lambda_layers.split(","):
                    layers.append(layer)
            if layers:
                properties["Layers"] = layers

            lambda_function["Properties"] = properties
            resources[alphanum_rule_name + "LambdaFunction"] = lambda_function

            lambda_permissions = {}
            lambda_permissions["Type"] = "AWS::Lambda::Permission"
            lambda_permissions["DependsOn"] = alphanum_rule_name + "LambdaFunction"
            lambda_permissions["Properties"] = {
                "FunctionName": {"Fn::GetAtt": [alphanum_rule_name + "LambdaFunction", "Arn"]},
                "Action": "lambda:InvokeFunction",
                "Principal": "config.amazonaws.com",
            }
            resources[alphanum_rule_name + "LambdaPermissions"] = lambda_permissions

        template["Resources"] = resources

        return json.dumps(template, indent=2)

    @staticmethod
    def tag_config_rule(rule_name, cfn_tags, args):
        my_session = Util.get_boto_session(args)
        config_client = my_session.client("config")
        config_arn = config_client.describe_config_rules(ConfigRuleNames=[rule_name])["ConfigRules"][0]["ConfigRuleArn"]
        response = config_client.tag_resource(ResourceArn=config_arn, Tags=cfn_tags)
        return response

    @staticmethod
    def get_lambda_layers(session, args, params):
        layers = []
        if "SourceRuntime" in params:
            if params["SourceRuntime"] in [
                "python3.6-lib",
                "python3.7-lib",
                "python3.8-lib",
            ]:
                if args.generated_lambda_layer:
                    lambda_layer_version = Util.get_existing_lambda_layer(session, layer_name=args.custom_layer_name)
                    if not lambda_layer_version:
                        print(
                            f"{session.region_name} --generated-lambda-layer flag received, but rdklib-layer not found in {session.region_name}. Creating one now"
                        )
                        Util.create_new_lambda_layer(session, layer_name=args.custom_layer_name)
                        lambda_layer_version = Util.get_existing_lambda_layer(
                            session, layer_name=args.custom_layer_name
                        )
                    layers.append(lambda_layer_version)
                elif args.rdklib_layer_arn:
                    layers.append(args.rdklib_layer_arn)
                else:
                    rdk_lib_version = RDKLIB_LAYER_VERSION[session.region_name]
                    rdklib_arn = RDKLIB_ARN_STRING.format(region=session.region_name, version=rdk_lib_version)
                    layers.append(rdklib_arn)
        return layers

    @staticmethod
    def get_existing_lambda_layer(session, layer_name="rdklib-layer"):
        region = session.region_name
        lambda_client = session.client("lambda")
        print(f"[{region}]: Checking for Existing RDK Layer")
        response = lambda_client.list_layer_versions(LayerName=layer_name)
        if response["LayerVersions"]:
            return response["LayerVersions"][0]["LayerVersionArn"]
        elif not response["LayerVersions"]:
            return None

    @staticmethod
    def create_new_lambda_layer(session, layer_name="rdklib-layer"):

        successful_return = None
        if layer_name == "rdklib-layer":
            successful_return = Util.create_new_lambda_layer_serverless_repo(session)

        # If that doesn't work, create it locally and upload - SAR doesn't support the custom layer name
        if layer_name != "rdklib-layer" or not successful_return:
            if layer_name == "rdklib-layer":
                print(
                    f"[{session.region_name}]: Serverless Application Repository deployment not supported, attempting manual deployment"
                )
            else:
                print(
                    f"[{session.region_name}]: Custom name layer not supported with Serverless Application Repository deployment, attempting manual deployment"
                )
            Util.create_new_lambda_layer_locally(session, layer_name)

    @staticmethod
    def create_new_lambda_layer_serverless_repo(session):
        try:
            cfn_client = session.client("cloudformation")
            sar_client = session.client("serverlessrepo")
            sar_client.get_application(ApplicationId=RDKLIB_LAYER_SAR_ID)
            # Try to create the stack from scratch
            create_type = "update"
            try:
                cfn_client.describe_stacks(StackName="serverlessrepo-rdklib")
            except ClientError as ce:
                if ce.response["Error"]["Code"] == "ValidationError":
                    create_type = "create"
                else:
                    raise ce
            change_set_arn = sar_client.create_cloud_formation_change_set(
                ApplicationId=RDKLIB_LAYER_SAR_ID, StackName="rdklib"
            )["ChangeSetId"]
            print(f"[{session.region_name}]: Creating change set to deploy rdklib-layer")
            code = Util.check_on_change_set(cfn_client, change_set_arn)
            if code == 1:
                print(
                    f"[{session.region_name}]: Lambda layer up to date with the Serverless Application Repository Version"
                )
                return 1
            if code == -1:
                print(f"[{session.region_name}]: Error creating change set, attempting to use manual deployment")
                raise ClientError()
            print(f"[{session.region_name}]: Executing change set to deploy rdklib-layer")
            cfn_client.execute_change_set(ChangeSetName=change_set_arn)
            waiter = cfn_client.get_waiter(f"stack_{create_type}_complete")
            waiter.wait(StackName="serverlessrepo-rdklib")
            print(f"[{session.region_name}]: Successfully executed change set")
            return 1
        # 2021-10-13 -> aws partition regions where SAR is not supported throw EndpointConnectionError and aws-cn throw ClientError
        except (EndpointConnectionError, ClientError):
            return None

    @staticmethod
    def create_new_lambda_layer_locally(session, layer_name="rdklib-layer"):
        region = session.region_name
        print(f"[{region}]: Creating new {layer_name}")
        folder_name = "lib" + str(uuid.uuid4())
        shell_command = f"pip3 install --target python boto3 botocore rdk rdklib future mock"

        print(f"[{region}]: Installing Packages to {folder_name}/python")
        try:
            os.makedirs(folder_name + "/python")
        except FileExistsError as e:
            print(e)
            sys.exit(1)
        os.chdir(folder_name)
        ret = subprocess.run(shell_command, capture_output=True, shell=True)

        print(f"[{region}]: Creating rdk_lib_layer.zip")
        shutil.make_archive(f"rdk_lib_layer", "zip", ".", "python")
        os.chdir("..")
        s3_client = session.client("s3")
        s3_resource = session.resource("s3")

        print(f"[{region}]: Creating temporary S3 Bucket")
        bucket_name = "rdkliblayertemp" + str(uuid.uuid4())
        if region != "us-east-1":
            s3_client.create_bucket(
                Bucket=bucket_name,
                CreateBucketConfiguration={"LocationConstraint": region},
            )
        if region == "us-east-1":
            s3_client.create_bucket(Bucket=bucket_name)

        print(f"[{region}]: Uploading rdk_lib_layer.zip to S3")
        s3_resource.Bucket(bucket_name).upload_file(f"{folder_name}/rdk_lib_layer.zip", layer_name)

        lambda_client = session.client("lambda")

        print(f"[{region}]: Publishing Lambda Layer")
        lambda_client.publish_layer_version(
            LayerName=layer_name,
            Content={"S3Bucket": bucket_name, "S3Key": layer_name},
            CompatibleRuntimes=["python3.6", "python3.7", "python3.8"],
        )

        print(f"[{region}]: Deleting temporary S3 Bucket")
        try:
            bucket = s3_resource.Bucket(bucket_name)
            bucket.objects.all().delete()
            bucket.delete()
        except Exception as e:
            print(e)

        print(f"[{region}]: Cleaning up temp_folder")
        shutil.rmtree(f"./{folder_name}")

    @staticmethod
    def check_on_change_set(cfn_client, name):
        for i in range(0, 120):
            response = cfn_client.describe_change_set(ChangeSetName=name)
            status = response["Status"]
            reason = response.get("StatusReason", "")
            if status == "FAILED" and reason == "No updates are to be performed.":
                return 1
            if status == "CREATE_COMPLETE":
                return 0
            time.sleep(5)
        return -1
