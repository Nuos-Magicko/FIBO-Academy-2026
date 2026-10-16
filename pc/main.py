"""The one starting point for every laptop program in this project.

  python pc/main.py                      list the commands
  python pc/main.py <command> --help     help for one command
  python pc/main.py viewer --host 192.168.1.248

Each command lives in its own folder:
  ui/         the viewer window
  recording/  saving sessions, replaying them, the synthetic sample
  tools/      checks and experiments: check, baseline, report, soak,
              verify-board, serial-log
  analysis/   pipeline, tracker and heatmaps (used by the commands)
  connection/ the message format and clock comparison (used by the commands)
  utils/      small shared helpers
"""

import importlib
import sys

COMMANDS = {
    "viewer": (
        "ui.viewer",
        "live window, or replay a saved session (--replay)",
    ),
    "replay": (
        "recording.replay",
        "replay a saved session and print its counters",
    ),
    "make-synthetic": (
        "recording.synthetic",
        "write the made-up sample session",
    ),
    "check": (
        "tools.check",
        "connect, change settings (--set), check that everything arrives",
    ),
    "baseline": (
        "tools.baseline",
        "repeatable experiment: every mode x repeats, conditions saved",
    ),
    "report": (
        "tools.report",
        "tables from a baseline folder (and a soak folder)",
    ),
    "soak": ("tools.soak", "long stability run with the serial log"),
    "verify-board": (
        "tools.verify_board",
        "flash (optional) and check a board",
    ),
    "serial-log": (
        "tools.serial_log",
        "reset the board and save its start-up messages",
    ),
}


def print_commands() -> None:
    print(__doc__.split("\n\n")[0] + "\n\nCommands:")
    for name in COMMANDS:
        module_name, summary = COMMANDS[name]
        print(f"  {name:<15} {summary}")
    print("\nExample: python pc/main.py viewer --replay samples/synthetic_run")


def main() -> int:
    """Run one command. sys.argv is rewritten so the command's own --help shows
    "main.py <command>"."""
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print_commands()
        return 0
    command = sys.argv[1]
    if command not in COMMANDS:
        print(f"unknown command {command!r}\n")
        print_commands()
        return 2
    module_name, summary = COMMANDS[command]
    sys.argv = [f"main.py {command}"] + sys.argv[2:]
    module = importlib.import_module(module_name)
    return module.main()


if __name__ == "__main__":
    sys.exit(main())
