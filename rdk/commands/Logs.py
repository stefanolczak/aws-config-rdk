import argparse
import sys
import time

from rdk.Util import Util


def logs(args):
    args = get_logs_parser().parse_args(args.command_args, args)

    args.rulename = Util.clean_rule_name(args.rulename)

    my_session = Util.get_boto_session()
    cw_logs = my_session.client("logs")
    log_group_name = Util.get_log_group_name()

    # Retrieve the last number of log events as specified by the user.
    try:
        log_streams = cw_logs.describe_log_streams(
            logGroupName=log_group_name,
            orderBy="LastEventTime",
            descending=True,
            limit=int(args.number),  # This is the worst-case scenario if there is only one event per stream
        )

        # Sadly we can't just use filter_log_events, since we don't know the timestamps yet and filter_log_events
        # doesn't appear to support ordering.
        my_events = Util.get_log_events(cw_logs, log_streams, int(args.number))

        latest_timestamp = 0

        if my_events is None:
            print("No Events to display.")
            return 0

        for event in my_events:
            if event["timestamp"] > latest_timestamp:
                latest_timestamp = event["timestamp"]

            Util.print_log_event(event)

        if args.follow:
            try:
                while True:
                    # Wait 2 seconds
                    time.sleep(2)

                    # Get all events between now and the timestamp of the most recent event.
                    my_new_events = cw_logs.filter_log_events(
                        logGroupName=log_group_name,
                        startTime=latest_timestamp + 1,
                        endTime=int(time.time()) * 1000,
                        interleaved=True,
                    )

                    for event in my_new_events["events"]:
                        if "timestamp" in event:
                            # Get the timestamp on the most recent event.
                            if event["timestamp"] > latest_timestamp:
                                latest_timestamp = event["timestamp"]

                            # Print the event.
                            Util.print_log_event(event)
            except KeyboardInterrupt:
                sys.exit(0)

    except cw_logs.exceptions.ResourceNotFoundException as e:
        print(e.response["Error"]["Message"])


def get_logs_parser():
    parser = argparse.ArgumentParser(
        prog="rdk logs",
        usage="rdk logs <rulename> [-n/--number NUMBER] [-f/--follow]",
        description="Displays CloudWatch logs for the Lambda Function for the specified Rule.",
    )
    parser.add_argument("rulename", metavar="<rulename>", help="Rule whose logs will be displayed")
    parser.add_argument(
        "-f",
        "--follow",
        action="store_true",
        help="[optional] Continuously poll Lambda logs and write to stdout.",
    )
    parser.add_argument(
        "-n",
        "--number",
        default=3,
        help="[optional] Number of previous logged events to display.",
    )
    return parser
