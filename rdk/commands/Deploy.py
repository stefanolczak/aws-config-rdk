import argparse
import sys
import os
from os import path
import shutil
from rdk import CODE_BUCKET_PREFIX, ROOT_DIR
from rdk.datatypes.util import util
import botocore
import json
import boto3
from botocore.exceptions import ClientError


class Deploy:
    def __init__(self, args):
        self.args = args

    def run(self):
        self.args = Deploy.get_deployment_parser().parse_args(self.args.command_args, self.args)

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

        # get the rule names
        rule_names = util.get_rule_list_for_command(self.args)

        # run the deploy code
        print(f"[{self.args.region}]: Running deploy!")

        # create custom session based on whatever credentials are available to us
        my_session = util.get_boto_session(self.args)

        # get accountID
        identity_details = util.get_caller_identity_details(my_session)
        account_id = identity_details["account_id"]
        partition = identity_details["partition"]

        if self.args.custom_code_bucket:
            code_bucket_name = self.args.custom_code_bucket
        else:
            code_bucket_name = CODE_BUCKET_PREFIX + account_id + "-" + my_session.region_name

        # If we're only deploying the Lambda functions (and role + permissions), branch here.  Someday the "main" execution path should use the same generated CFN templates for single-account deployment.
        if self.args.functions_only:
            # Generate the template
            function_template = util.create_function_cloudformation_template(self.args)

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
                rule_params, cfn_tags = util.get_rule_parameters(rule_name)
                if "SourceIdentifier" in rule_params:
                    print(f"[{my_session.region_name}]: Skipping code packaging for Managed Rule.")
                else:
                    s3_dst = util.upload_function_code(rule_name, rule_params, account_id, my_session, code_bucket_name)
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
                    util.wait_for_cfn_stack(my_cfn, self.args.stack_name, self.args)
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
                    rule_params, cfn_tags = util.get_rule_parameters(rule_name)
                    my_lambda_arn = util.get_lambda_arn_for_rule(
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
                util.wait_for_cfn_stack(my_cfn, self.args.stack_name, self.args)

            # We're done!  Return with great success.
            sys.exit(0)

        # If we're deploying both the functions and the Config rules, run the following process:
        for rule_name in rule_names:
            rule_params, cfn_tags = util.get_rule_parameters(rule_name)

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
                        path.dirname(ROOT_DIR),
                        "template",
                        "configManagedRuleWithRemediation.json",
                    )
                    template_body = open(cfn_body, "r").read()
                    json_body = json.loads(template_body)
                    remediation = util.create_remediation_cloudformation_block(rule_params["Remediation"])
                    json_body["Resources"]["Remediation"] = remediation

                    if "SSMAutomation" in rule_params:
                        # Reference the SSM Automation Role Created, if IAM is created
                        print(f"[{my_session.region_name}]: Building SSM Automation Section")
                        ssm_automation = util.create_automation_cloudformation_block(
                            rule_params["SSMAutomation"],
                            util.get_alphanumeric_rule_name(rule_name),
                        )
                        json_body["Resources"][
                            util.get_alphanumeric_rule_name(rule_name + "RemediationAction")
                        ] = ssm_automation
                        if "IAM" in rule_params["SSMAutomation"]:
                            print(f"[{my_session.region_name}]: Lets Build IAM Role and Policy")
                            # TODO Check For IAM Settings
                            json_body["Resources"]["Remediation"]["Properties"]["Parameters"]["AutomationAssumeRole"][
                                "StaticValue"
                            ]["Values"] = [
                                {
                                    "Fn::GetAtt": [
                                        util.get_alphanumeric_rule_name(rule_name + "Role"),
                                        "Arn",
                                    ]
                                }
                            ]

                            (ssm_iam_role, ssm_iam_policy,) = util.create_automation_iam_cloudformation_block(
                                rule_params["SSMAutomation"],
                                util.get_alphanumeric_rule_name(rule_name),
                            )
                            json_body["Resources"][util.get_alphanumeric_rule_name(rule_name + "Role")] = ssm_iam_role
                            json_body["Resources"][
                                util.get_alphanumeric_rule_name(rule_name + "Policy")
                            ] = ssm_iam_policy

                            print(f"[{my_session.region_name}]: Build Supporting SSM Resources")
                            resource_depends_on = [
                                "rdkConfigRule",
                                util.get_alphanumeric_rule_name(rule_name + "RemediationAction"),
                            ]
                            # Builds SSM Document Before Config RUle
                            json_body["Resources"]["Remediation"]["DependsOn"] = resource_depends_on
                            json_body["Resources"]["Remediation"]["Properties"]["TargetId"] = {
                                "Ref": util.get_alphanumeric_rule_name(rule_name + "RemediationAction")
                            }

                    try:
                        my_stack_name = util.get_stack_name_from_rule_name(rule_name)
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
                    util.wait_for_cfn_stack(my_cfn, my_stack_name, self.args)
                    continue

                else:
                    # deploy config rule
                    cfn_body = os.path.join(path.dirname(ROOT_DIR), "template", "configManagedRule.json")

                    try:
                        my_stack_name = util.get_stack_name_from_rule_name(rule_name)
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
                    util.wait_for_cfn_stack(my_cfn, my_stack_name, self.args)

                # Cloudformation is not supporting tagging config rule currently.
                if cfn_tags is not None and len(cfn_tags) > 0:
                    util.tag_config_rule(rule_name, cfn_tags, my_session, self.args)

                continue

            print(f"[{my_session.region_name}]: Found Custom Rule.")

            s3_src = ""
            s3_dst = util.upload_function_code(rule_name, rule_params, account_id, my_session, code_bucket_name)

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
                    "ParameterValue": util.get_lambda_name(rule_name, rule_params),
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
                    "ParameterValue": util.get_runtime_string(rule_params),
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
                    "ParameterValue": util.get_handler(rule_name, rule_params),
                },
                {
                    "ParameterKey": "Timeout",
                    "ParameterValue": str(self.args.lambda_timeout),
                },
            ]
            layers = util.get_lambda_layers(my_session, self.args, rule_params)

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
            cfn_body = path.join(path.dirname(ROOT_DIR), "template", "configRule.json")
            template_body = open(cfn_body, "r").read()
            json_body = json.loads(template_body)

            remediation = ""
            if "Remediation" in rule_params:
                remediation = util.create_remediation_cloudformation_block(rule_params["Remediation"])
                json_body["Resources"]["Remediation"] = remediation

                if "SSMAutomation" in rule_params:
                    # AWS needs to build the SSM before the Config Rule
                    resource_depends_on = [
                        "rdkConfigRule",
                        util.get_alphanumeric_rule_name(rule_name + "RemediationAction"),
                    ]
                    remediation["DependsOn"] = resource_depends_on
                    # Add JSON Reference to SSM Document { "Ref" : "MyEC2Instance" }
                    remediation["Properties"]["TargetId"] = {
                        "Ref": util.get_alphanumeric_rule_name(rule_name + "RemediationAction")
                    }

            if "SSMAutomation" in rule_params:
                print(f"[{my_session.region_name}]: Building SSM Automation Section")

                ssm_automation = util.create_automation_cloudformation_block(rule_params["SSMAutomation"], rule_name)
                json_body["Resources"][
                    util.get_alphanumeric_rule_name(rule_name + "RemediationAction")
                ] = ssm_automation
                if "IAM" in rule_params["SSMAutomation"]:
                    print("Lets Build IAM Role and Policy")
                    # TODO Check For IAM Settings
                    json_body["Resources"]["Remediation"]["Properties"]["Parameters"]["AutomationAssumeRole"][
                        "StaticValue"
                    ]["Values"] = [
                        {
                            "Fn::GetAtt": [
                                util.get_alphanumeric_rule_name(rule_name + "Role"),
                                "Arn",
                            ]
                        }
                    ]

                    (
                        ssm_iam_role,
                        ssm_iam_policy,
                    ) = util.create_automation_iam_cloudformation_block(rule_params["SSMAutomation"], rule_name)
                    json_body["Resources"][util.get_alphanumeric_rule_name(rule_name + "Role")] = ssm_iam_role
                    json_body["Resources"][util.get_alphanumeric_rule_name(rule_name + "Policy")] = ssm_iam_policy

            # debugging
            # print(json.dumps(json_body, indent=2))

            # deploy config rule
            my_cfn = my_session.client("cloudformation")
            try:
                my_stack_name = util.get_stack_name_from_rule_name(rule_name)
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

                my_lambda_arn = util.get_lambda_arn_for_stack(my_stack_name, self.args)

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
                print(f"[{my_session.region_name}]: Creating CloudFormation Stack for " + rule_name)
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
            util.wait_for_cfn_stack(my_cfn, my_stack_name, self.args)

            # Cloudformation is not supporting tagging config rule currently.
            if cfn_tags is not None and len(cfn_tags) > 0:
                util.tag_config_rule(rule_name, cfn_tags, my_session, self.args)

        print(f"[{my_session.region_name}]: Config deploy complete.")

        return 0

    @staticmethod
    def get_deployment_parser(Command="deploy"):
        direction = "to"

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
        return parser
