import argparse
import json
import os
import time
from os import path

from rdk import (
    ASSUME_ROLE_POLICY_FILE,
    CODE_BUCKET_PREFIX,
    CONFIG_BUCKET_PREFIX,
    CONFIG_ROLE_NAME,
    DELIVERY_PERMISSION_POLICY_FILE,
    ROOT_DIR,
)
from rdk.Util import Util


def init(args):
    my_session = Util.get_boto_session(args)
    args = get_init_parser().parse_args(args.command_args, args)

    print(f"[{my_session.region_name}]: Running init!")

    # Create our ConfigService client
    my_config = my_session.client("config")

    # get accountID, AWS partition (e.g. aws or aws-us-gov), region (us-east-1, us-gov-west-1)
    identity_details = Util.get_caller_identity_details(my_session)
    account_id = identity_details["account_id"]
    partition = identity_details["partition"]

    config_recorder_name = "default"
    config_role_arn = ""
    delivery_channel_exists = False

    config_bucket_exists = False
    if args.config_bucket_exists_in_another_account:
        print(f"[{my_session.region_name}]: Skipping Config Bucket check due to command line args")
        config_bucket_exists = True

    config_bucket_name = CONFIG_BUCKET_PREFIX + "-" + account_id

    control_tower = False
    if args.control_tower:
        print(
            f"[{my_session.region_name}]: This account is part of an AWS Control Tower managed organization. Playing nicely with it"
        )
        control_tower = True

    if args.generate_lambda_layer:
        lambda_layer_version = Util.get_existing_lambda_layer(my_session, layer_name=args.custom_layer_name)
        if lambda_layer_version:
            print(f"[{my_session.region_name}]: Found Version: " + lambda_layer_version)
        if args.generate_lambda_layer:
            print(
                f"[{my_session.region_name}]: --generate-lambda-layer Flag received, forcing update of the Lambda Layer in {my_session.region_name}"
            )
        else:
            print(f"[{my_session.region_name}]: Lambda Layer not found in {my_session.region_name}. Creating one now")
        # Try to generate lambda layer with ServerlessAppRepo, manually generate if impossible
        Util.create_new_lambda_layer(my_session, layer_name=args.custom_layer_name)
        lambda_layer_version = Util.get_existing_lambda_layer(my_session, layer_name=args.custom_layer_name)

    # Check to see if the ConfigRecorder has been created.
    recorders = my_config.describe_configuration_recorders()
    if len(recorders["ConfigurationRecorders"]) > 0:
        config_recorder_name = recorders["ConfigurationRecorders"][0]["name"]
        config_role_arn = recorders["ConfigurationRecorders"][0]["roleARN"]
        print(f"[{my_session.region_name}]: Found Config Recorder: " + config_recorder_name)
        print(f"[{my_session.region_name}]: Found Config Role: " + config_role_arn)

    delivery_channels = my_config.describe_delivery_channels()
    if len(delivery_channels["DeliveryChannels"]) > 0:
        delivery_channel_exists = True
        config_bucket_name = delivery_channels["DeliveryChannels"][0]["s3BucketName"]

    my_s3 = my_session.client("s3")

    if not config_bucket_exists:
        # check whether bucket exists if not create config bucket
        response = my_s3.list_buckets()
        bucket_exists = False
        for bucket in response["Buckets"]:
            if bucket["Name"] == config_bucket_name:
                print(f"[{my_session.region_name}]: Found Bucket: " + config_bucket_name)
                config_bucket_exists = True
                bucket_exists = True

        if not bucket_exists:
            print(f"[{my_session.region_name}]: Creating Config bucket " + config_bucket_name)
            if my_session.region_name == "us-east-1":
                my_s3.create_bucket(Bucket=config_bucket_name)
            else:
                my_s3.create_bucket(
                    Bucket=config_bucket_name,
                    CreateBucketConfiguration={"LocationConstraint": my_session.region_name},
                )

    if not config_role_arn:
        # create config role
        my_iam = my_session.client("iam")
        response = my_iam.list_roles()
        role_exists = False
        for role in response["Roles"]:
            if role["RoleName"] == CONFIG_ROLE_NAME:
                role_exists = True

        if not role_exists:
            print(f"[{my_session.region_name}]: Creating IAM role config-role")
            if partition in ["aws", "aws-us-gov"]:
                partition_url = ".com"
            elif partition == "aws-cn":
                partition_url = ".com.cn"
            assume_role_policy_template = open(
                os.path.join(path.dirname(ROOT_DIR), "template", ASSUME_ROLE_POLICY_FILE),
                "r",
            ).read()
            assume_role_policy = json.loads(assume_role_policy_template.replace("${PARTITIONURL}", partition_url))
            assume_role_policy["Statement"].append(
                {
                    "Effect": "Allow",
                    "Principal": {"AWS": str(account_id)},
                    "Action": "sts:AssumeRole",
                }
            )
            my_iam.create_role(
                RoleName=CONFIG_ROLE_NAME,
                AssumeRolePolicyDocument=json.dumps(assume_role_policy),
                Path="/rdk/",
            )

        # attach role policy
        my_iam.attach_role_policy(
            RoleName=CONFIG_ROLE_NAME,
            PolicyArn="arn:" + partition + ":iam::aws:policy/service-role/AWSConfigRole",
        )
        my_iam.attach_role_policy(
            RoleName=CONFIG_ROLE_NAME,
            PolicyArn="arn:" + partition + ":iam::aws:policy/ReadOnlyAccess",
        )
        policy_template = open(
            os.path.join(path.dirname(ROOT_DIR), "template", DELIVERY_PERMISSION_POLICY_FILE),
            "r",
        ).read()
        delivery_permissions_policy = policy_template.replace("${ACCOUNTID}", account_id).replace(
            "${PARTITION}", partition
        )
        my_iam.put_role_policy(
            RoleName=CONFIG_ROLE_NAME,
            PolicyName="ConfigDeliveryPermissions",
            PolicyDocument=delivery_permissions_policy,
        )

        # wait for changes to propagate.
        print(f"[{my_session.region_name}]: Waiting for IAM role to propagate")
        time.sleep(16)

    # create or update config recorder
    if not config_role_arn:
        config_role_arn = "arn:" + partition + ":iam::" + account_id + ":role/rdk/config-role"

    if not control_tower:
        my_config.put_configuration_recorder(
            ConfigurationRecorder={
                "name": config_recorder_name,
                "roleARN": config_role_arn,
                "recordingGroup": {
                    "allSupported": True,
                    "includeGlobalResourceTypes": True,
                },
            }
        )

        if not delivery_channel_exists:
            # create delivery channel
            print(f"[{my_session.region_name}]: Creating delivery channel to bucket " + config_bucket_name)
            my_config.put_delivery_channel(
                DeliveryChannel={
                    "name": "default",
                    "s3BucketName": config_bucket_name,
                    "configSnapshotDeliveryProperties": {"deliveryFrequency": "Six_Hours"},
                }
            )

        # start config recorder
        my_config.start_configuration_recorder(ConfigurationRecorderName=config_recorder_name)
        print(f"[{my_session.region_name}]: Config Service is ON")
    else:
        print(
            f"[{my_session.region_name}]: Skipped put_configuration_recorder, put_delivery_channel & start_configuration_recorder as this is part of a Control Tower managed Organization"
        )

    print(f"[{my_session.region_name}]: Config setup complete.")

    # create code bucket
    code_bucket_name = CODE_BUCKET_PREFIX + account_id + "-" + my_session.region_name
    response = my_s3.list_buckets()
    bucket_exists = False
    for bucket in response["Buckets"]:
        if bucket["Name"] == code_bucket_name:
            bucket_exists = True
            print(f"[{args.region}]: Found code bucket: " + code_bucket_name)

    if not bucket_exists:
        if args.skip_code_bucket_creation:
            print(f"[{my_session.region_name}]: Skipping Code Bucket creation due to command line args")
        else:
            print(f"[{my_session.region_name}]: Creating Code bucket " + code_bucket_name)

        # Consideration for us-east-1 S3 API
        if my_session.region_name == "us-east-1":
            my_s3.create_bucket(Bucket=code_bucket_name)
        else:
            my_s3.create_bucket(
                Bucket=code_bucket_name,
                CreateBucketConfiguration={"LocationConstraint": my_session.region_name},
            )
    return 0


def get_init_parser():
    parser = argparse.ArgumentParser(
        prog="rdk init",
        description="Sets up AWS Config.  This will enable configuration recording in AWS and ensure necessary S3 buckets and IAM Roles are created.",
    )

    parser.add_argument(
        "--config-bucket-exists-in-another-account",
        required=False,
        action="store_true",
        help="[optional] If the Config bucket exists in another account, remove the check of the bucket",
    )
    parser.add_argument(
        "--skip-code-bucket-creation",
        required=False,
        action="store_true",
        help='[optional] If you want to use custom code bucket for rdk, enable this and use flag --custom-code-bucket to "rdk deploy"',
    )
    parser.add_argument(
        "--control-tower",
        required=False,
        action="store_true",
        help="[optional] If your account is part of an AWS Control Tower setup --control-tower will skip the setup of configuration_recorder and delivery_channel",
    )
    parser.add_argument(
        "--generate-lambda-layer",
        required=False,
        action="store_true",
        help='[optional] Forces an update to the rdklib-layer in the region. If no rdklib-layer exists in this region then "rdk init" will automatically deploy one',
    )
    parser.add_argument(
        "--custom-layer-name",
        required=False,
        default="rdklib-layer",
        help='[optional] Sets the name of the generated lambda-layer, "rdklib-layer" by default',
    )

    return parser
