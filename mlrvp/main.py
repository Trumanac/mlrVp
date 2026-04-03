"""mlrVp entry point."""

import sys
from mlrvp.app import run_app


def main():
    sys.exit(run_app(sys.argv))


if __name__ == "__main__":
    main()
