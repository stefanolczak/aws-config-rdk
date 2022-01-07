import argparse
import sys
import json
import os
from os import path
import shutil
from rdk import RULES_DIR, ROOT_DIR
from rdk.datatypes.util import util


class Export:
    def __init__(self, args):
        self.args = args

    def run(self):

        self.args = Export.get_export_parser().parse_args(self.args.command_args, self.args)

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

        # get the rule names
        rule_names = util.get_rule_list_for_command(self.args, "export")

        # run the export code
        print("Running export")

        for rule_name in rule_names:
            rule_params, cfn_tags = util.get_rule_parameters(rule_name)

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
            my_session = util.get_boto_session(self.args)
            s3_src = ""
            s3_dst = util.package_function_code(rule_name, rule_params, my_session)

            layers = []
            rdk_lib_version = "0"

            layers = util.get_lambda_layers(my_session, self.args, rule_params)

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
                "rule_lambda_name": util.get_lambda_name(rule_name, rule_params),
                "source_runtime": util.get_runtime_string(rule_params),
                "source_events": source_events,
                "source_periodic": source_periodic,
                "source_input_parameters": json.dumps(combined_input_parameters),
                "source_handler": util.get_handler(rule_name, rule_params),
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
                path.dirname(ROOT_DIR),
                "template",
                self.args.format,
                self.args.version,
                "config_rule.tf",
            )
            tf_file_path = os.path.join(os.getcwd(), RULES_DIR, rule_name, rule_name.lower() + "_rule.tf")
            shutil.copy(tf_file_body, tf_file_path)

            variables_file_body = os.path.join(
                path.dirname(ROOT_DIR),
                "template",
                self.args.format,
                self.args.version,
                "variables.tf",
            )
            variables_file_path = os.path.join(os.getcwd(), RULES_DIR, rule_name, rule_name.lower() + "_variables.tf")
            shutil.copy(variables_file_body, variables_file_path)
            print("Export completed.This will generate three .tf files.")

    @staticmethod
    def get_export_parser(Command="export"):
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
