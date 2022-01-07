import argparse

from rdk.Util import Util


def rulesets(args):
    args = get_rulesets_parser().parse_args(args.command_args, args)

    if args.subcommand in ["add", "remove"] and (not args.ruleset or not args.rulename):
        print("You must specify a ruleset name and a rule for the `add` and `remove` commands.")
        return 1

    if args.subcommand == "list":
        Util.list_rulesets()
    elif args.subcommand == "add":
        Util.add_ruleset_rule(args.ruleset, args.rulename)
    elif args.subcommand == "remove":
        Util.remove_ruleset_rule(args.ruleset, args.rulename)
    else:
        print("Unknown subcommand.")


def get_rulesets_parser():
    parser = argparse.ArgumentParser(
        prog="rdk rulesets",
        usage="rdk rulesets [list | [ [ add | remove ] <ruleset> <rulename> ]",
        description="Used to describe and manipulate RuleSet tags on Rules.",
    )
    parser.add_argument("subcommand", help="One of list, add, or remove")
    parser.add_argument("ruleset", nargs="?", help="Name of RuleSet")
    parser.add_argument("rulename", nargs="?", help="Name of Rule to be added or removed")
    return parser
