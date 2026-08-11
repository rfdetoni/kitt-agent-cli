import sys
import argparse
from kitt.cli.repl import KittREPL

def main():
    parser = argparse.ArgumentParser(
        prog="kitt",
        description="Kitt Agent CLI — Autonomous coding agent with Context Engine & Task Router"
    )
    parser.add_argument("-p", "--print", help="Print response for a single prompt and exit", type=str)
    parser.add_argument("--root", help="Root directory of repository", type=str, default=".")

    args = parser.parse_args()

    repl = KittREPL(root_dir=args.root)

    if args.print:
        repl.process_turn(args.print)
    else:
        repl.start()

    return 0

if __name__ == '__main__':
    sys.exit(main())
