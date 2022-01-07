#    Copyright 2017-2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"). You may not use this file except in compliance with the License. A copy of the License is located at
#
#        http://aws.amazon.com/apache2.0/
#
#    or in the "license" file accompanying this file. This file is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the License for the specific language governing permissions and limitations under the License.

MY_VERSION = "0.9.0"
RDKLIB_LAYER_VERSION = {
    "ap-southeast-1": "28",
    "ap-south-1": "5",
    "us-east-2": "5",
    "us-east-1": "5",
    "us-west-1": "4",
    "us-west-2": "4",
    "ap-northeast-2": "5",
    "ap-southeast-2": "5",
    "ap-northeast-1": "5",
    "ca-central-1": "5",
    "eu-central-1": "5",
    "eu-west-1": "5",
    "eu-west-2": "4",
    "eu-west-3": "5",
    "eu-north-1": "5",
    "sa-east-1": "5",
}
RDKLIB_LAYER_SAR_ID = "arn:aws:serverlessrepo:ap-southeast-1:711761543063:applications/rdklib"
RDKLIB_ARN_STRING = "arn:aws:lambda:{region}:711761543063:layer:rdklib-layer:{version}"
PARALLEL_COMMAND_THROTTLE_PERIOD = 2  # 2 seconds, used in running commands in parallel over multiple regions
ACCEPTED_RESOURCE_TYPES = [
    "AWS::ApiGateway::Stage",
    "AWS::ApiGatewayV2::Stage",
    "AWS::ApiGateway::RestApi",
    "AWS::ApiGatewayV2::Api",
    "AWS::CloudFront::Distribution",
    "AWS::CloudFront::StreamingDistribution",
    "AWS::CloudWatch::Alarm",
    "AWS::DynamoDB::Table",
    "AWS::EC2::Volume",
    "AWS::EC2::Host",
    "AWS::EC2::EIP",
    "AWS::EC2::Instance",
    "AWS::EC2::NetworkInterface",
    "AWS::EC2::SecurityGroup",
    "AWS::EC2::NatGateway",
    "AWS::EC2::EgressOnlyInternetGateway",
    "AWS::EC2::FlowLog",
    "AWS::EC2::VPCEndpoint",
    "AWS::EC2::VPCEndpointService",
    "AWS::EC2::VPCPeeringConnection",
    "AWS::ECR::Repository",
    "AWS::ECS::Cluster",
    "AWS::ECS::TaskDefinition",
    "AWS::ECS::Service",
    "AWS::ECS::TaskSet",
    "AWS::EFS::FileSystem",
    "AWS::EFS::AccessPoint",
    "AWS::EKS::Cluster",
    "AWS::Elasticsearch::Domain",
    "AWS::QLDB::Ledger",
    "AWS::Kineses::Stream",
    "AWS::Kineses::StreamConsumer",
    "AWS::Redshift::Cluster",
    "AWS::Redshift::ClusterParameterGroup",
    "AWS::Redshift::ClusterSecurityGroup",
    "AWS::Redshift::ClusterSnapshot",
    "AWS::Redshift::ClusterSubnetGroup",
    "AWS::Redshift::EventSubscription",
    "AWS::RDS::DBInstance",
    "AWS::RDS::DBSecurityGroup",
    "AWS::RDS::DBSnapshot",
    "AWS::RDS::DBSubnetGroup",
    "AWS::RDS::EventSubscription",
    "AWS::RDS::DBCluster",
    "AWS::RDS::DBClusterSnapshot",
    "AWS::SNS::Topic",
    "AWS::SQS::Queue",
    "AWS::S3::Bucket",
    "AWS::S3::AccountPublicAccessBlock",
    "AWS::EC2::CustomerGateway",
    "AWS::EC2::InternetGateway",
    "AWS::EC2::NetworkAcl",
    "AWS::EC2::RouteTable",
    "AWS::EC2::Subnet",
    "AWS::EC2::VPC",
    "AWS::EC2::VPNConnection",
    "AWS::EC2::VPNGateway",
    "AWS::AutoScaling::AutoScalingGroup",
    "AWS::AutoScaling::LaunchConfiguration",
    "AWS::AutoScaling::ScalingPolicy",
    "AWS::AutoScaling::ScheduledAction",
    "AWS::Backup::BackupPlan",
    "AWS::Backup::BackupSelection",
    "AWS::Backup::BackupVault",
    "AWS::Backup::RecoveryPoint",
    "AWS::ACM::Certificate",
    "AWS::CloudFormation::Stack",
    "AWS::CloudTrail::Trail",
    "AWS::CodeBuild::Project",
    "AWS::CodePipeline::Pipeline",
    "AWS::Config::ResourceCompliance",
    "AWS::Config::ConformancePackCompliance",
    "AWS::ElasticBeanstalk::Application",
    "AWS::ElasticBeanstalk::ApplicationVersion",
    "AWS::ElasticBeanstalk::Environment",
    "AWS::IAM::User",
    "AWS::IAM::Group",
    "AWS::IAM::Role",
    "AWS::IAM::Policy",
    "AWS::KMS::Key",
    "AWS::Lambda::Function",
    "AWS::NetworkFirewall::Firewall",
    "AWS::NetworkFirewall::FirewallPolicy",
    "AWS::NetworkFirewall::RuleGroup",
    "AWS::SecretsManager::Secret",
    "AWS::ServiceCatalog::CloudFormationProduct",
    "AWS::ServiceCatalog::CloudFormationProvisionedProduct",
    "AWS::ServiceCatalog::Portfolio",
    "AWS::Shield::Protection",
    "AWS::ShieldRegional::Protection",
    "AWS::SSM::ManagedInstanceInventory",
    "AWS::SSM::PatchCompliance",
    "AWS::SSM::AssociationCompliance",
    "AWS::SSM::FileData",
    "AWS::WAF::RateBasedRule",
    "AWS::WAF::Rule",
    "AWS::WAF::WebACL",
    "AWS::WAF::RuleGroup",
    "AWS::WAFRegional::RateBasedRule",
    "AWS::WAFRegional::Rule",
    "AWS::WAFRegional::WebACL",
    "AWS::WAFRegional::RuleGroup",
    "AWS::WAFv2::WebACL",
    "AWS::WAFv2::RuleGroup",
    "AWS::WAFv2::ManagedRuleSet",
    "AWS::XRay::EncryptionConfig",
    "AWS::ElasticLoadBalancingV2::LoadBalancer",
    "AWS::ElasticLoadBalancing::LoadBalancer",
]

CONFIG_ROLE_ASSUME_ROLE_POLICY_DOCUMENT = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Sid": "LOCAL",
            "Effect": "Allow",
            "Principal": {"Service": ["config.amazonaws.com"]},
            "Action": "sts:AssumeRole",
        },
        {
            "Sid": "REMOTE",
            "Effect": "Allow",
            "Principal": {"AWS": {"Fn::Sub": "arn:${AWS::Partition}:iam::${LambdaAccountId}:root"}},
            "Action": "sts:AssumeRole",
        },
    ],
}
CONFIG_ROLE_POLICY_DOCUMENT = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Action": "s3:PutObject*",
            "Resource": {"Fn::Sub": "arn:${AWS::Partition}:s3:::${ConfigBucket}/AWSLogs/${AWS::AccountId}/*"},
            "Condition": {"StringLike": {"s3:x-amz-acl": "bucket-owner-full-control"}},
        },
        {
            "Effect": "Allow",
            "Action": "s3:GetBucketAcl",
            "Resource": {"Fn::Sub": "arn:${AWS::Partition}:s3:::${ConfigBucket}"},
        },
    ],
}

RULES_DIR = ""
UTIL_FILENAME = "rule_util"
RULE_HANDLER = "rule_code"
RULE_TEMPLATE = "rdk-rule.template"
CONFIG_BUCKET_PREFIX = "config-bucket"
CONFIG_ROLE_NAME = "config-role"
ASSUME_ROLE_POLICY_FILE = "configRuleAssumeRolePolicyDoc.json"
DELIVERY_PERMISSION_POLICY_FILE = "deliveryPermissionsPolicy.json"
CODE_BUCKET_PREFIX = "config-rule-code-bucket-"
PARAMETER_FILE_NAME = "parameters.json"
EXAMPLE_CI_DIR = "example_ci"
TEST_CI_FILENAME = "test_ci.json"
EVENT_TEMPLATE_FILENAME = "test_event_template.json"
ROOT_DIR = __file__
