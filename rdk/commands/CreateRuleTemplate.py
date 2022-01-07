import argparse
import json

from rdk import (
    CONFIG_BUCKET_PREFIX,
    CONFIG_ROLE_ASSUME_ROLE_POLICY_DOCUMENT,
    CONFIG_ROLE_NAME,
    CONFIG_ROLE_POLICY_DOCUMENT,
)
from rdk.Util import Util


def create_rule_template(args):
    args = get_create_rule_template_parser().parse_args(args.command_args, args)

    if args.rulesets:
        args.rulesets = args.rulesets.split(",")

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

    if not args.rules_only:
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
        if args.config_role_arn:
            resources["ConfigurationRecorder"]["Properties"]["RoleARN"] = args.config_role_arn

        resources["DeliveryChannel"] = {
            "Type": "AWS::Config::DeliveryChannel",
            "Properties": {
                "Name": "default",
                "S3BucketName": {"Ref": "ConfigBucket"},
                "ConfigSnapshotDeliveryProperties": {"DeliveryFrequency": "One_Hour"},
            },
        }

    # Next, go through each rule in our rule list and add the CFN to deploy it.
    rule_names = Util.get_rule_list_for_command(args)
    for rule_name in rule_names:
        params, tags = Util.get_rule_parameters(rule_name)
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

            param_name = Util.get_alphanumeric_rule_name(rule_name) + input_param
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

                param_name = Util.get_alphanumeric_rule_name(rule_name) + optional_param

                parameters[param_name] = cfn_param
                optional_parameter_group["Parameters"].append(param_name)

                conditions[param_name] = {"Fn::Not": [{"Fn::Equals": ["", {"Ref": param_name}]}]}

        config_rule = {}
        config_rule["Type"] = "AWS::Config::ConfigRule"
        if not args.rules_only:
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
                + Util.get_lambda_name(rule_name, params)
            }

        properties["Source"] = source

        properties["InputParameters"] = {}

        if "InputParameters" in params:
            for required_param in json.loads(params["InputParameters"]):
                cfn_param_name = Util.get_alphanumeric_rule_name(rule_name) + required_param
                properties["InputParameters"][required_param] = {"Ref": cfn_param_name}

        if "OptionalParameters" in params:
            for optional_param in json.loads(params["OptionalParameters"]):
                cfn_param_name = Util.get_alphanumeric_rule_name(rule_name) + optional_param
                properties["InputParameters"][optional_param] = {
                    "Fn::If": [
                        cfn_param_name,
                        {"Ref": cfn_param_name},
                        {"Ref": "AWS::NoValue"},
                    ]
                }

        config_rule["Properties"] = properties
        config_rule_resource_name = Util.get_alphanumeric_rule_name(rule_name) + "ConfigRule"
        resources[config_rule_resource_name] = config_rule

        # If Remediation create the remediation section with potential links to the SSM Details
        if "Remediation" in params:
            remediation = Util.create_remediation_cloudformation_block(params["Remediation"])
            remediation["DependsOn"] = [config_rule_resource_name]
            if not args.rules_only:
                remediation["DependsOn"].append("ConfigRole")

            if "SSMAutomation" in params:
                ssm_automation = Util.create_automation_cloudformation_block(params["SSMAutomation"], rule_name)
                # AWS needs to build the SSM before the Config Rule
                remediation["DependsOn"].append(Util.get_alphanumeric_rule_name(rule_name + "RemediationAction"))
                # Add JSON Reference to SSM Document { "Ref" : "MyEC2Instance" }
                remediation["Properties"]["TargetId"] = {
                    "Ref": Util.get_alphanumeric_rule_name(rule_name) + "RemediationAction"
                }

                if "IAM" in params["SSMAutomation"]:
                    print("Lets Build IAM Role and Policy For the SSM Document")
                    (
                        ssm_iam_role,
                        ssm_iam_policy,
                    ) = Util.create_automation_iam_cloudformation_block(params["SSMAutomation"], rule_name)
                    resources[Util.get_alphanumeric_rule_name(rule_name + "Role")] = ssm_iam_role
                    resources[Util.get_alphanumeric_rule_name(rule_name + "Policy")] = ssm_iam_policy
                    remediation["Properties"]["Parameters"]["AutomationAssumeRole"]["StaticValue"]["Values"] = [
                        {
                            "Fn::GetAtt": [
                                Util.get_alphanumeric_rule_name(rule_name + "Role"),
                                "Arn",
                            ]
                        }
                    ]
                    # Override the placeholder to associate the SSM Document Role with newly crafted role
                    resources[Util.get_alphanumeric_rule_name(rule_name + "RemediationAction")] = ssm_automation
            resources[Util.get_alphanumeric_rule_name(rule_name) + "Remediation"] = remediation

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

    output_file = open(args.output_file, "w")
    output_file.write(json.dumps(template, indent=2))
    print("CloudFormation template written to " + args.output_file)

    if script_for_tag:
        print("Found tags on config rules. Cloudformation do not support tagging config rule at the moment")
        print("Generating script for config rules tags")
        script_for_tag = "#! /bin/bash \n" + script_for_tag
        if args.tag_config_rules_script:
            with open(args.tag_config_rules_script, "w") as rsh:
                rsh.write(script_for_tag)
        else:
            print("=========SCRIPT=========")
            print(script_for_tag)
            print("you can use flag [--tag-config-rules-script <file path> ] to output the script")


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
