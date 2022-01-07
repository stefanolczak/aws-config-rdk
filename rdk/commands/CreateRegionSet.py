import argparse
import yaml


def create_region_set(args):
    args = get_create_region_set_parser().parse_args(args.command_args, args)
    output_file = args.output_file
    output_dict = {
        "default": ["us-east-1", "us-west-1", "eu-north-1", "ap-southeast-1"],
        "aws-cn-region-set": ["cn-north-1", "cn-northwest-1"],
    }
    with open(f"{output_file}.yaml", "w+") as file:
        yaml.dump(output_dict, file, default_flow_style=False)


def get_create_region_set_parser():
    parser = argparse.ArgumentParser(
        prog="rdk create-region-set",
        description="Outputs a YAML region set file for multi-region deployment.",
    )
    parser.add_argument(
        "-o",
        "--output-file",
        required=False,
        default="regions",
        help="Filename of the generated region set file",
    )
    return parser
