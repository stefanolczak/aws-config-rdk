import argparse
import json
import os
import sys
from os import path

from botocore.exceptions import ClientError
from rdk import CODE_BUCKET_PREFIX, ROOT_DIR
from rdk.Util import Util


def deploy_organization(args):
    args = get_deployment_organization_parser().parse_args(args.command_args, args)

    # Validate inputs
    if args.stack_name and not args.functions_only:
        print("--stack-name can only be specified when using the --functions-only feature.")
        sys.exit(1)

    # Make sure we're not exceeding Layer limits
    if args.lambda_layers:
        layer_count = len(args.lambda_layers.split(","))
        if layer_count > 5:
            print("You may only specify 5 Lambda Layers.")
            sys.exit(1)
        if args.rdklib_layer_arn and layer_count > 4:
            print("Because you have selected a 'lib' runtime You may only specify 4 additional Lambda Layers.")
            sys.exit(1)

    # RDKLib version and RDKLib Layer ARN are mutually exclusive.
    if "rdk_lib_version" in args and "rdklib_layer_arn" in args:
        print(
            "Specify EITHER an RDK Lib version to use the official"
            " release OR a specific Layer ARN to use a custom implementation."
        )
        sys.exit(1)

    # Check rule names to make sure none are too long.  This is needed to catch Rules created before length
    # constraint was added.
    if args.rulename:
        for name in args.rulename:
            if len(name) > 128:
                print(
                    "Error: Found Rule with name over 128 characters:"
                    " {} \n Recreate the Rule with a shorter name.".format(name)
                )
                sys.exit(1)

    if args.functions_only and not args.stack_name:
        args.stack_name = "RDK-Config-Rule-Functions"

    if args.rulesets:
        args.rulesets = args.rulesets.split(",")

    # get the rule names
    rule_names = Util.get_rule_list_for_command()

    # run the deploy code
    print("Running Organization deploy!")

    # create custom session based on whatever credentials are available to us
    my_session = Util.get_boto_session()

    # get accountID
    identity_details = Util.get_caller_identity_details(my_session)
    account_id = identity_details["account_id"]
    partition = identity_details["partition"]

    if args.custom_code_bucket:
        code_bucket_name = args.custom_code_bucket
    else:
        code_bucket_name = CODE_BUCKET_PREFIX + account_id + "-" + my_session.region_name

    # If we're only deploying the Lambda functions (and role + permissions), branch here.
    # Someday the "main" execution path should use the same generated CFN templates for single-account deployment.
    if args.functions_only:
        print("We don't handle Function Only deployment for Organizations")
        sys.exit(1)

    # If we're deploying both the functions and the Config rules, run the following process:
    for rule_name in rule_names:
        rule_params, cfn_tags = Util.get_rule_parameters(rule_name)

        # create CFN Parameters common for Managed and Custom
        source_events = "NONE"
        if "Remediation" in rule_params:
            print(
                f"WARNING: Organization Rules with Remediation is not supported at the moment. {rule_name}"
                " will be deployed without auto-remediation."
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
                path.dirname(ROOT_DIR),
                "template",
                "configManagedRuleOrganization.json",
            )

            try:
                my_stack_name = Util.get_stack_name_from_rule_name(rule_name)
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
            except ClientError:
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
            Util.wait_for_cfn_stack(my_cfn, my_stack_name)

            # Cloudformation is not supporting tagging config rule currently.
            if cfn_tags is not None and len(cfn_tags) > 0:
                print(
                    "WARNING: Tagging is not supported for organization config rules."
                    " Only the cloudformation template will be tagged."
                )

            continue

        print("Found Custom Rule.")

        s3_dst = Util.upload_function_code(rule_name, rule_params, account_id, my_session, code_bucket_name)

        # create CFN Parameters for Custom Rules
        lambdaRoleArn = ""
        if args.lambda_role_arn:
            print("Existing IAM Role provided: " + args.lambda_role_arn)
            lambdaRoleArn = args.lambda_role_arn
        elif args.lambda_role_name:
            print(f"[{my_session.region_name}]: Finding IAM Role: " + args.lambda_role_name)
            arn = f"arn:{partition}:iam::{account_id}:role/Rdk-Lambda-Role"
            lambdaRoleArn = arn

        if args.boundary_policy_arn:
            print("Boundary Policy provided: " + args.boundary_policy_arn)
            boundaryPolicyArn = args.boundary_policy_arn
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
                "ParameterValue": Util.get_lambda_name(rule_name, rule_params),
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
                "ParameterValue": Util.get_runtime_string(rule_params),
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
                "ParameterValue": Util.get_handler(rule_name, rule_params),
            },
            {
                "ParameterKey": "Timeout",
                "ParameterValue": str(args.lambda_timeout),
            },
        ]
        layers = Util.get_lambda_layers(my_session, args, rule_params)

        if args.lambda_layers:
            additional_layers = args.lambda_layers.split(",")
            layers.extend(additional_layers)

        if layers:
            my_params.append({"ParameterKey": "Layers", "ParameterValue": ",".join(layers)})

        if args.lambda_security_groups and args.lambda_subnets:
            my_params.append(
                {
                    "ParameterKey": "SecurityGroupIds",
                    "ParameterValue": args.lambda_security_groups,
                }
            )
            my_params.append(
                {
                    "ParameterKey": "SubnetIds",
                    "ParameterValue": args.lambda_subnets,
                }
            )

        # create json of CFN template
        cfn_body = os.path.join(path.dirname(ROOT_DIR), "template", "configRuleOrganization.json")
        template_body = open(cfn_body, "r").read()
        json_body = json.loads(template_body)

        # debugging
        # print(json.dumps(json_body, indent=2))

        # deploy config rule
        my_cfn = my_session.client("cloudformation")
        try:
            my_stack_name = Util.get_stack_name_from_rule_name(rule_name)
            my_cfn.describe_stacks(StackName=my_stack_name)
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

            my_lambda_arn = Util.get_lambda_arn_for_stack(my_stack_name)

            print("Publishing Lambda code...")
            my_lambda_client = my_session.client("lambda")
            my_lambda_client.update_function_code(
                FunctionName=my_lambda_arn,
                S3Bucket=code_bucket_name,
                S3Key=s3_dst,
                Publish=True,
            )
            print("Lambda code updated.")
        except ClientError:
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

            my_cfn.create_stack(**cfn_args)

        # wait for changes to propagate.
        Util.wait_for_cfn_stack(my_cfn, my_stack_name)

        # Cloudformation is not supporting tagging config rule currently.
        if cfn_tags is not None and len(cfn_tags) > 0:
            print(
                "WARNING: Tagging is not supported for organization config rules."
                " Only the cloudformation template will be tagged."
            )

    print("Config deploy complete.")

    return 0


def get_deployment_organization_parser():

    parser = argparse.ArgumentParser(
        prog="rdk deploy-organization",
        description="Used to deploy the Config Rule to the target Organization.",
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
    return parser


def undeploy_organization(args):
    args = get_deployment_organization_parser().parse_args(args.command_args, args)

    # Validate inputs
    if args.stack_name and not args.functions_only:
        print("--stack-name can only be specified when using the --functions-only feature.")
        sys.exit(1)

    # Make sure we're not exceeding Layer limits
    if args.lambda_layers:
        layer_count = len(args.lambda_layers.split(","))
        if layer_count > 5:
            print("You may only specify 5 Lambda Layers.")
            sys.exit(1)
        if args.rdklib_layer_arn and layer_count > 4:
            print("Because you have selected a 'lib' runtime You may only specify 4 additional Lambda Layers.")
            sys.exit(1)

    # RDKLib version and RDKLib Layer ARN are mutually exclusive.
    if "rdk_lib_version" in args and "rdklib_layer_arn" in args:
        print(
            "Specify EITHER an RDK Lib version to use the official release OR a specific Layer ARN to use"
            " a custom implementation."
        )
        sys.exit(1)

    # Check rule names to make sure none are too long.  This is needed to catch Rules created before
    # length constraint was added.
    if args.rulename:
        for name in args.rulename:
            if len(name) > 128:
                print(
                    "Error: Found Rule with name over 128 characters: {} \n Recreate"
                    " the Rule with a shorter name.".format(name)
                )
                sys.exit(1)

    if args.functions_only and not args.stack_name:
        args.stack_name = "RDK-Config-Rule-Functions"

    if args.rulesets:
        args.rulesets = args.rulesets.split(",")

    if not args.force:
        confirmation = False
        while not confirmation:
            my_input = input("Delete specified Rules and Lambda Functions from your Organization? (y/N): ")
            if my_input.lower() == "y":
                confirmation = True
            if my_input.lower() == "n" or my_input == "":
                sys.exit(0)

    # get the rule names
    rule_names = Util.get_rule_list_for_command(args)

    print("Running Organization un-deploy!")

    # create custom session based on whatever credentials are available to us.
    my_session = Util.get_boto_session(args)

    # Collect a list of all of the CloudFormation templates that we delete.
    # We'll need it at the end to make sure everything worked.
    deleted_stacks = []

    cfn_client = my_session.client("cloudformation")

    if args.functions_only:
        try:
            cfn_client.delete_stack(StackName=args.stack_name)
            deleted_stacks.append(args.stack_name)
        except ClientError as ce:
            print("Client Error encountered attempting to delete CloudFormation stack for Lambda Functions: " + str(ce))
        except Exception as e:
            print("Exception encountered attempting to delete CloudFormation stack for Lambda Functions: " + str(e))

        return

    for rule_name in rule_names:
        try:
            cfn_client.delete_stack(StackName=Util.get_stack_name_from_rule_name(rule_name))
            deleted_stacks.append(Util.get_stack_name_from_rule_name(rule_name))
        except ClientError as ce:
            print("Client Error encountered attempting to delete CloudFormation stack for Rule: " + str(ce))
        except Exception as e:
            print("Exception encountered attempting to delete CloudFormation stack for Rule: " + str(e))

    print("Rule removal initiated. Waiting for Stack Deletion to complete.")

    for stack_name in deleted_stacks:
        Util.wait_for_cfn_stack(cfn_client, stack_name, args)

    print("Rule removal complete, but local files have been preserved.")
    print("To re-deploy, use the 'deploy-organization' command.")
