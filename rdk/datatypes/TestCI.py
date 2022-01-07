class TestCI:
    def __init__(self, ci_type):
        # convert ci_type string to filename format
        ci_file = ci_type.replace("::", "_") + ".json"
        try:
            self.ci_json = json.load(
                open(
                    os.path.join(path.dirname(__file__), "template", EXAMPLE_CI_DIR, ci_file),
                    "r",
                )
            )
        except FileNotFoundError:
            print(
                "No sample CI found for "
                + ci_type
                + ", even though it appears to be a supported CI.  Please log an issue at https://github.com/awslabs/aws-config-rdk."
            )
            exit(1)

    def get_json(self):
        return self.ci_json
