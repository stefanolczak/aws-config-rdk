import argparse
import sys

from botocore.exceptions import ClientError
from rdk import CODE_BUCKET_PREFIX, CONFIG_ROLE_NAME
from rdk.Util import Util


def clean(args):
    args = get_clean_parser().parse_args(args.command_args, args)

    if not args.force:
        confirmation = False
        while not confirmation:
            my_input = input("Delete all Rules and remove Config setup?! (y/N): ")
            if my_input.lower() == "y":
                confirmation = True
            if my_input.lower() == "n" or my_input == "":
                sys.exit(0)

    print("Running clean!")

    # create custom session based on whatever credentials are available to us
    my_session = Util.get_boto_session(args)

    # Create our ConfigService client
    my_config = my_session.client("config")

    # Create an IAM client!  Create all the clients!
    iam_client = my_session.client("iam")
    cfn_client = my_session.client("cloudformation")

    # get accountID
    identity_details = Util.get_caller_identity_details(my_session)
    account_id = identity_details["account_id"]
    config_bucket_name = ""

    recorders = my_config.describe_configuration_recorders()
    if len(recorders["ConfigurationRecorders"]) > 0:
        try:
            # First delete the Config Recorder itself.  Do we need to stop it first?  Let's stop it just to be safe.
            my_config.stop_configuration_recorder(
                ConfigurationRecorderName=recorders["ConfigurationRecorders"][0]["name"]
            )
            my_config.delete_configuration_recorder(
                ConfigurationRecorderName=recorders["ConfigurationRecorders"][0]["name"]
            )
        except Exception as e:
            print("Error encountered removing Configuration Recorder: " + str(e))

    # Once the config recorder has been deleted there should be no dependencies on the Config Role anymore.

    try:
        response = iam_client.get_role(RoleName=CONFIG_ROLE_NAME)
        try:
            role_policy_results = iam_client.list_role_policies(RoleName=CONFIG_ROLE_NAME)
            for policy_name in role_policy_results["PolicyNames"]:
                iam_client.delete_role_policy(RoleName=CONFIG_ROLE_NAME, PolicyName=policy_name)

            role_policy_results = iam_client.list_attached_role_policies(RoleName=CONFIG_ROLE_NAME)
            for policy in role_policy_results["AttachedPolicies"]:
                iam_client.detach_role_policy(RoleName=CONFIG_ROLE_NAME, PolicyArn=policy["PolicyArn"])

            # Once all policies are detached we should be able to delete the Role.
            iam_client.delete_role(RoleName=CONFIG_ROLE_NAME)
        except Exception as e:
            print("Error encountered removing Config Role: " + str(e))
    except Exception as e2:
        print("Error encountered finding Config Role to remove: " + str(e2))

    config_bucket_names = []
    delivery_channels = my_config.describe_delivery_channels()
    if len(delivery_channels["DeliveryChannels"]) > 0:
        for delivery_channel in delivery_channels["DeliveryChannels"]:
            config_bucket_names.append(delivery_channels["DeliveryChannels"][0]["s3BucketName"])
            try:
                my_config.delete_delivery_channel(DeliveryChannelName=delivery_channel["name"])
            except Exception as e:
                print("Error encountered trying to delete Delivery Channel: " + str(e))

    if config_bucket_names:
        # empty and then delete the config bucket.
        for config_bucket_name in config_bucket_names:
            try:
                config_bucket = my_session.resource("s3").Bucket(config_bucket_name)
                config_bucket.objects.all().delete()
                config_bucket.delete()
            except Exception as e:
                print("Error encountered trying to delete config bucket: " + str(e))

    # Delete any of the Rules deployed the traditional way.
    args.all = True
    rule_names = Util.get_rule_list_for_command(args)
    for rule_name in rule_names:
        my_stack_name = Util.get_stack_name_from_rule_name(rule_name)
        try:
            cfn_client.delete_stack(StackName=my_stack_name)
        except Exception as e:
            print("Error encountered deleting Rule stack: " + str(e))

    # Delete the Functions stack, if one exists.
    try:
        response = cfn_client.describe_stacks(StackName="RDK-Config-Rule-Functions")
        if response["Stacks"]:
            cfn_client.delete_stack(StackName="RDK-Config-Rule-Functions")
    except ClientError as ce:
        if ce.response["Error"]["Code"] == "ValidationError":
            print("No Functions stack found.")
    except Exception as e:
        print("Error encountered deleting Functions stack: " + str(e))

    # Delete the code bucket, if one exists.
    code_bucket_name = CODE_BUCKET_PREFIX + account_id + "-" + my_session.region_name
    try:
        code_bucket = my_session.resource("s3").Bucket(code_bucket_name)
        code_bucket.objects.all().delete()
        code_bucket.delete()
    except ClientError as ce:
        if ce.response["Error"]["Code"] == "NoSuchBucket":
            print("No code bucket found.")
    except Exception as e:
        print("Error encountered trying to delete code bucket: " + str(e))

    # Done!
    print("Config has been removed.")


def get_clean_parser():
    parser = argparse.ArgumentParser(
        prog="rdk clean",
        description=(
            "Removes AWS Config from the account.  This will disable all Config rules and no configuration"
            "changes will be recorded!"
        ),
    )
    parser.add_argument(
        "--force",
        required=False,
        action="store_true",
        help="[optional] Clean account without prompting for confirmation.",
    )

    return parser
