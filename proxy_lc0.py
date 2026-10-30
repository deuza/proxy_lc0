#!/usr/bin/env python3
"""UCI relay that runs Lc0 on a remote host over SSH.

A chess GUI (Nibbler, Arena, Lucas Chess, ...) starts this program as a
local UCI engine. The program starts the system OpenSSH client, which runs
Lc0 on the remote host, then relays the UCI lines in both directions. It
never waits for an answer of the engine: the GUI keeps full control of the
UCI synchronisation (uciok, readyok, bestmove).

On top of this plain relay, the program does three things:

1. Before the first GUI command that may prepare or start a search
   (isready, ucinewgame, position or go), it sends the options of
   config.json that describe the remote host: log file, weights file,
   Syzygy folders, cache size and RAM limit. Lc0 loads its network on the
   first ucinewgame, position or go, so the configured network is the
   first one loaded.
2. It drops the GUI setoption commands for WeightsFile, SyzygyPath and
   LogFile: their values are paths on the GUI computer, which mean nothing
   on the remote host.
3. It copies the standard error of ssh, and therefore of Lc0, to its own
   standard error, so that the GUI can display it (Nibbler shows it in its
   info box). Known harmless lines (HIDDEN_STDERR_LINES) only go to the log.

The settings are read from config.json, in the directory of the program
(the directory of the executable once frozen with PyInstaller).
Only the Python standard library is used.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess  # nosec B404 # argv list only, never shell=True
import sys
import threading
from typing import IO, Any, NoReturn

__version__ = "1.0.0"

PROG = "proxy_lc0"
CONFIG_NAME = "config.json"
DEFAULT_LOG_FILE = "debug.log"

# Line terminator written to the GUI: "\r\n" on Windows, like a native
# Windows engine, "\n" everywhere else.
EOL = os.linesep.encode("ascii")

# GUI commands before which the options of config.json are sent, once.
# A compliant GUI sends isready before any search, but some GUIs send
# ucinewgame or position first: every one of them is a trigger, so that the
# options always reach Lc0 before it loads its network.
INJECTION_TRIGGERS = frozenset({"isready", "ucinewgame", "position", "go"})

# GUI options that are never forwarded, compared in lower case because UCI
# option names are case insensitive. Their values are paths on the GUI
# computer, the remote values come from config.json.
FILTERED_OPTIONS = frozenset({"weightsfile", "syzygypath", "logfile"})

# Remote options: (config key, Lc0 UCI option, kind of value, maximum).
# The maximums are the ones that Lc0 0.32.1 announces in its option list.
# LogFile comes first, so that Lc0 also logs the options that follow.
REMOTE_OPTIONS: tuple[tuple[str, str, str, int], ...] = (
    ("lc0_logfile", "LogFile", "path", 0),
    ("lc0_weights", "WeightsFile", "path", 0),
    ("lc0_syzygy", "SyzygyPath", "pathlist", 0),
    ("lc0_nncache", "NNCacheSize", "int", 999999999),
    ("lc0_ramlimit_mb", "RamLimitMb", "int", 100000000),
)

# Every key that config.json may contain. Keys starting with "_" are free
# comments and are ignored.
KNOWN_KEYS = frozenset(
    {
        "host",
        "username",
        "port",
        "key_file",
        "ssh_options",
        "lc0_command",
        "logging_enabled",
        "log_file",
    }
    | {entry[0] for entry in REMOTE_OPTIONS}
)

# ANSI escape sequences (CSI). Outside Windows, Lc0 always colours its
# banner on stderr, even when stderr is not a terminal; a GUI would show the
# codes as garbage, so they are removed from the relayed stderr.
ANSI_ESCAPE = re.compile(rb"\x1b\[[0-?]*[ -/]*[@-~]")

# Remote stderr lines kept out of the GUI, compared after removal of the
# colour codes and of the surrounding blanks; they still go to the log.
# "Failed to import MPS module": seen on macOS 27.2 with an Apple M1 at each
# new batch size, when the Neural Engine compiler rejects part of the
# network and MPSGraph keeps it on the GPU. The speed was measured identical
# with and without it, but Nibbler shows it in red because of "Failed".
HIDDEN_STDERR_LINES = frozenset({b"Failed to import MPS module"})

# Seconds granted to ssh to exit after quit or end of input, then to the
# reader threads to deliver their last lines.
QUIT_TIMEOUT = 5.0
JOIN_TIMEOUT = 2.0

# Logger of the program; setup_logging() attaches the file handler to the
# root logger, which this one propagates to.
LOG = logging.getLogger(PROG)

# One lock for every write to our standard error, which is shared by the
# proxy messages and the relayed stderr of the remote side.
_STDERR_LOCK = threading.Lock()


class ConfigError(Exception):
    """Raised when config.json is missing, unreadable or inconsistent."""


# ---------------------------------------------------------------------------
# Messages for the GUI
# ---------------------------------------------------------------------------


def write_stderr(line: bytes) -> None:
    """Write one line, without its terminator, to our standard error.

    The GUI reads this stream (Nibbler displays it in its info box).
    Errors are ignored: a GUI that closed the stream must not stop the relay.
    """
    stream = sys.stderr
    if stream is None:
        return
    with _STDERR_LOCK:
        try:
            stream.flush()
            stream.buffer.write(line + EOL)
            stream.buffer.flush()
        except (OSError, ValueError, AttributeError):
            pass


def notice(message: str) -> None:
    """Show a message of the proxy itself to the GUI, on standard error."""
    write_stderr(f"{PROG}: {message}".encode("utf-8", "replace"))


def fatal(message: str) -> NoReturn:
    """Log and show a startup error, then leave with status 1."""
    LOG.critical(message)
    notice(message)
    logging.shutdown()
    sys.exit(1)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def program_dir() -> str:
    """Return the directory that holds config.json.

    Once frozen by PyInstaller, sys.executable is the path of the .exe;
    otherwise sys.argv[0] is the path of this script. The path is made
    absolute right away, before anything may change the current directory.
    """
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(sys.argv[0]))


def load_config(base_dir: str) -> dict[str, Any]:
    """Read and return the JSON object stored in config.json.

    The "utf-8-sig" encoding also accepts the byte order mark that some
    Windows editors put at the start of UTF-8 files.
    """
    path = os.path.join(base_dir, CONFIG_NAME)
    try:
        with open(path, "r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except FileNotFoundError as exc:
        raise ConfigError(f"configuration file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"invalid JSON in {path}: {exc}") from exc
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a JSON object")
    return data


def is_unset(value: Any) -> bool:
    """Tell whether a configuration value means "not set"."""
    return value is None or value == "" or value == []


def config_text(config: dict[str, Any], key: str,
                required: bool = False) -> str | None:
    """Return a one-line string setting, or None when it is not set."""
    value = config.get(key)
    if is_unset(value):
        if required:
            raise ConfigError(f'"{key}" is required in {CONFIG_NAME}')
        return None
    if not isinstance(value, str):
        raise ConfigError(f'"{key}" must be a string')
    if "\r" in value or "\n" in value:
        raise ConfigError(f'"{key}" must fit on one line')
    return value


def config_required(config: dict[str, Any], key: str) -> str:
    """Return a one-line string setting that must be present."""
    value = config_text(config, key, required=True)
    if value is None:  # cannot happen, config_text raised already
        raise ConfigError(f'"{key}" is required in {CONFIG_NAME}')
    return value


def config_port(config: dict[str, Any]) -> str | None:
    """Return the SSH port as a string, or None when it is not set.

    Both 22 and "22" are accepted, as in config.json of proxy_stockfish.
    """
    value = config.get("port")
    if is_unset(value):
        return None
    number = -1
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, str):
        try:
            number = int(value.strip())
        except ValueError:
            number = -1
    if not 1 <= number <= 65535:
        raise ConfigError('"port" must be a number between 1 and 65535')
    return str(number)


def config_ssh_options(config: dict[str, Any]) -> list[str]:
    """Return the extra ssh arguments, a list of strings (may be empty)."""
    value = config.get("ssh_options")
    if is_unset(value):
        return []
    if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value):
        raise ConfigError('"ssh_options" must be a list of strings')
    return list(value)


def remote_path(key: str, value: Any) -> str:
    """Check one path of the remote host and return it.

    The remote host is macOS or Linux: the path must be absolute. Lc0
    opens it as is and does not expand "~", which only a shell would do.
    """
    if not isinstance(value, str):
        raise ConfigError(f'"{key}" must be a string')
    if "\r" in value or "\n" in value:
        raise ConfigError(f'"{key}" must fit on one line')
    if not value.startswith("/"):
        raise ConfigError(
            f'"{key}" must be an absolute path on the remote host, '
            f'starting with "/" (Lc0 does not expand "~"): {value}')
    return value


def remote_path_list(key: str, value: Any) -> str:
    """Check a list of remote folders and return it joined with ":".

    Lc0 splits SyzygyPath on ":" outside Windows and only looks for tables
    directly in each listed folder, never in sub-folders. The setting is
    either one string, already separated with ":", or a list of folders.
    """
    if isinstance(value, str):
        parts = value.split(":")
    elif isinstance(value, list) and all(
            isinstance(item, str) for item in value):
        parts = list(value)
        for part in parts:
            if ":" in part:
                raise ConfigError(
                    f'"{key}": a folder of the list cannot contain ":": '
                    f'{part}')
    else:
        raise ConfigError(f'"{key}" must be a string or a list of strings')
    return ":".join(remote_path(key, part) for part in parts)


def remote_int(key: str, value: Any, maximum: int) -> str:
    """Check a whole number between 0 and maximum and return its text."""
    if (isinstance(value, bool) or not isinstance(value, int)
            or not 0 <= value <= maximum):
        raise ConfigError(
            f'"{key}" must be a whole number between 0 and {maximum}')
    return str(value)


def remote_option_lines(config: dict[str, Any]) -> list[str]:
    """Build the setoption commands of config.json, in sending order.

    A key that is missing, null, empty or an empty list sends nothing:
    Lc0 then keeps its own default.
    """
    lines = []
    for key, option, kind, maximum in REMOTE_OPTIONS:
        value = config.get(key)
        if is_unset(value):
            continue
        if kind == "path":
            text = remote_path(key, value)
        elif kind == "pathlist":
            text = remote_path_list(key, value)
        else:
            text = remote_int(key, value, maximum)
        lines.append(f"setoption name {option} value {text}")
    return lines


def ssh_command(config: dict[str, Any],
                base_dir: str) -> tuple[list[str], str]:
    """Build the ssh argument list and return it with the ssh target.

    Order of the arguments:
    - the "ssh_options" of config.json come first: for each setting, ssh
      keeps the first value it gets, so they win over the defaults below;
    - "-T" never asks for a pseudo-terminal, which would alter the UCI
      stream (character echo, line ending translation);
    - "BatchMode=yes" makes ssh fail at once instead of waiting for a
      password or a host key confirmation that nobody can type;
    - "-i", "-p" and "user@" only when config.json sets them, so that a
      Host alias of the ssh configuration keeps its own values.
    """
    ssh_bin = shutil.which("ssh")
    if not ssh_bin:
        raise ConfigError(
            "ssh client not found in PATH: install the OpenSSH client")

    host = config_required(config, "host")
    lc0 = config_required(config, "lc0_command")
    username = config_text(config, "username")
    port = config_port(config)
    key_file = config_text(config, "key_file")

    command = [ssh_bin] + config_ssh_options(config)
    command += ["-T", "-o", "BatchMode=yes"]
    if key_file:
        # Expand "~" first (on Windows Python uses USERPROFILE), then make
        # a relative path relative to the directory of the program.
        path = os.path.expanduser(key_file)
        if not os.path.isabs(path):
            path = os.path.join(base_dir, path)
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            raise ConfigError(f"key file not found: {path}")
        command += ["-i", path]
    if port:
        command += ["-p", port]
    target = f"{username}@{host}" if username else host
    # ssh hands the remote command to the shell of the remote user: lc0
    # flags may follow the path, and a path with spaces must be quoted.
    command += [target, lc0]
    return command, target


def check_unknown_keys(config: dict[str, Any]) -> None:
    """Report the keys of config.json that the proxy does not know.

    A typo such as "lc0_weight" would otherwise go unnoticed, and Lc0
    would silently load another network.
    """
    unknown = sorted(key for key in config
                     if key not in KNOWN_KEYS and not key.startswith("_"))
    if unknown:
        message = (f"unknown keys in {CONFIG_NAME}, ignored: "
                   + ", ".join(unknown))
        LOG.warning(message)
        notice(message)


def setup_logging(config: dict[str, Any], base_dir: str) -> None:
    """Write a DEBUG log to log_file when logging_enabled is true.

    The log holds every line in both directions, so it grows fast during
    an analysis: keep it off outside troubleshooting. A relative log_file
    is relative to the directory of the program. When logging is off, the
    logging calls cost almost nothing.
    """
    enabled = config.get("logging_enabled", False)
    if not isinstance(enabled, bool):
        raise ConfigError('"logging_enabled" must be true or false')
    if not enabled:
        logging.disable(logging.CRITICAL)
        return
    name = config_text(config, "log_file") or DEFAULT_LOG_FILE
    path = os.path.expanduser(name)
    if not os.path.isabs(path):
        path = os.path.join(base_dir, path)
    try:
        handler = logging.FileHandler(path, encoding="utf-8")
    except OSError as exc:
        notice(f"cannot open the log file {path}: {exc}; logging is off")
        logging.disable(logging.CRITICAL)
        return
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(threadName)s] %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)


# ---------------------------------------------------------------------------
# Relay
# ---------------------------------------------------------------------------


def command_word(tokens: list[str]) -> str:
    """Return the UCI command of a tokenised line, in lower case."""
    return tokens[0].lower() if tokens else ""


def option_name(tokens: list[str]) -> str | None:
    """Return the option name of "setoption name <name> [value <v>]".

    The name may contain spaces; it ends before the "value" keyword.
    Return None when the line does not follow this pattern.
    """
    if len(tokens) < 3 or tokens[1].lower() != "name":
        return None
    words = []
    for token in tokens[2:]:
        if token.lower() == "value":
            break
        words.append(token)
    return " ".join(words) if words else None


class Relay:
    """Own the ssh process and the two threads that read it.

    - The "lc0-stdout" thread copies every engine line to our stdout.
    - The "lc0-stderr" thread copies every stderr line to our stderr.
    - The main thread reads the GUI (see relay_gui_commands) and writes
      to the stdin of ssh through send().

    Nobody waits for an answer of the engine. When the remote side closes
    without being asked to, the proxy exits, so that the GUI sees an
    engine that has stopped instead of an engine that stays silent.
    """

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self.process = process
        # Set before a wanted stop (quit, end of GUI input): the end of the
        # engine output is then expected and not an error.
        self.stopping = threading.Event()
        self.stdout_thread = threading.Thread(
            target=self._pump_stdout, name="lc0-stdout", daemon=True)
        self.stderr_thread = threading.Thread(
            target=self._pump_stderr, name="lc0-stderr", daemon=True)

    def start(self) -> None:
        """Start the two reader threads."""
        self.stdout_thread.start()
        self.stderr_thread.start()

    def send(self, line: bytes) -> bool:
        """Send one line to Lc0, ended by a plain "\\n".

        The GUI line ending (often "\\r\\n" on Windows) was removed by the
        caller: the remote side always gets Unix line endings. Return False
        when the pipe to ssh is closed.
        """
        stdin: IO[bytes] | None = self.process.stdin
        if stdin is None:
            return False
        try:
            stdin.write(line + b"\n")
            stdin.flush()
        except (OSError, ValueError) as exc:
            LOG.error("cannot write to ssh: %s", exc)
            return False
        return True

    def _pump_stdout(self) -> None:
        """Copy the engine output to the GUI until the end of the stream."""
        source: IO[bytes] | None = self.process.stdout
        if source is None:
            return
        out = sys.stdout.buffer
        try:
            for raw in iter(source.readline, b""):
                line = raw.rstrip(b"\r\n")
                LOG.debug("<< %s", line.decode("utf-8", "replace"))
                out.write(line + EOL)
                out.flush()
        except (OSError, ValueError) as exc:
            # Our stdout is closed (the GUI has gone: the main thread will
            # see the end of its input and stop everything), or the pipe of
            # ssh was closed by shutdown(): nothing more to relay.
            LOG.warning("stdout relay stopped: %s", exc)
            return
        except Exception:  # pylint: disable=broad-exception-caught
            LOG.exception("unexpected error in the stdout relay")
        if not self.stopping.is_set():
            self._engine_lost()

    def _pump_stderr(self) -> None:
        """Copy the stderr of ssh and Lc0 to our stderr, and log it.

        ANSI colour codes are removed on the way (see ANSI_ESCAPE). The
        lines of HIDDEN_STDERR_LINES are logged but not shown to the GUI.
        """
        source: IO[bytes] | None = self.process.stderr
        if source is None:
            return
        try:
            for raw in iter(source.readline, b""):
                line = ANSI_ESCAPE.sub(b"", raw.rstrip(b"\r\n"))
                text = line.decode("utf-8", "replace")
                if line.strip() in HIDDEN_STDERR_LINES:
                    LOG.info("stderr, hidden from the GUI: %s", text)
                    continue
                LOG.info("stderr: %s", text)
                write_stderr(line)
        except Exception:  # pylint: disable=broad-exception-caught
            LOG.exception("unexpected error in the stderr relay")

    def _engine_lost(self) -> None:
        """End the whole proxy after an unexpected end of the engine output.

        The main thread is blocked on the GUI input and cannot be woken up,
        hence os._exit(). The last stderr lines (the ssh or Lc0 error
        message) are delivered first.
        """
        self.stderr_thread.join(JOIN_TIMEOUT)
        try:
            status: int | None = self.process.wait(timeout=QUIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            status = None
        message = ("connection to the remote engine closed "
                   f"(ssh exit status {status})")
        LOG.error(message)
        notice(message)
        logging.shutdown()
        os._exit(1)  # pylint: disable=protected-access

    def shutdown(self) -> None:
        """Stop ssh and Lc0 cleanly after quit or the end of GUI input.

        Closing the stdin of ssh sends an end of input to Lc0, which then
        leaves its command loop and exits; ssh follows. If ssh is still
        alive after QUIT_TIMEOUT seconds, it is terminated, then killed.
        """
        self.stopping.set()
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except (OSError, ValueError):
                pass
        try:
            self.process.wait(timeout=QUIT_TIMEOUT)
        except subprocess.TimeoutExpired:
            LOG.warning("ssh still running, terminating it")
            self.process.terminate()
            try:
                self.process.wait(timeout=JOIN_TIMEOUT)
            except subprocess.TimeoutExpired:
                LOG.warning("ssh still running, killing it")
                self.process.kill()
                self.process.wait()
        self.stdout_thread.join(JOIN_TIMEOUT)
        self.stderr_thread.join(JOIN_TIMEOUT)
        # The Relay owns the process: release the read ends of the pipes.
        for pipe in (self.process.stdout, self.process.stderr):
            if pipe is not None:
                try:
                    pipe.close()
                except (OSError, ValueError):
                    pass
        LOG.info("ssh exit status %s", self.process.returncode)


def relay_gui_commands(relay: Relay, option_lines: list[str]) -> int:
    """Read the GUI commands and forward them to Lc0 until quit.

    Return 0 after quit or the end of the GUI input, 1 when the pipe to ssh
    is broken. The remote options are sent once, just before the first
    command of INJECTION_TRIGGERS; the GUI setoption commands listed in
    FILTERED_OPTIONS are dropped.
    """
    injected = False
    gui = sys.stdin.buffer
    while True:
        raw = gui.readline()
        if not raw:
            LOG.info("end of GUI input")
            return 0
        line = raw.rstrip(b"\r\n")
        text = line.decode("utf-8", "replace")
        LOG.debug(">> %s", text)
        tokens = text.split()
        command = command_word(tokens)

        if command == "setoption":
            name = option_name(tokens)
            if name is not None and name.lower() in FILTERED_OPTIONS:
                message = (f"GUI option {name} ignored: the remote paths "
                           f"come from {CONFIG_NAME}")
                LOG.info(message)
                notice(message)
                continue

        if not injected and command in INJECTION_TRIGGERS:
            injected = True
            for option_line in option_lines:
                LOG.info("injected: %s", option_line)
                if not relay.send(option_line.encode("utf-8")):
                    return 1

        if command == "quit":
            # Set before sending: the end of the engine output that follows
            # is then expected.
            relay.stopping.set()
        if not relay.send(line):
            return 1
        if command == "quit":
            LOG.info("quit received from the GUI")
            return 0


def main() -> int:
    """Read the configuration, start ssh and relay until the end."""
    if sys.stdin is None or sys.stdout is None:
        # Frozen without a console (--noconsole): no UCI channel at all.
        return 1
    base_dir = program_dir()
    try:
        config = load_config(base_dir)
        setup_logging(config, base_dir)
    except ConfigError as exc:
        notice(str(exc))
        return 1
    LOG.info("%s %s started from %s", PROG, __version__, base_dir)

    try:
        check_unknown_keys(config)
        command, target = ssh_command(config, base_dir)
        option_lines = remote_option_lines(config)
    except ConfigError as exc:
        fatal(str(exc))
    LOG.info("ssh command: %s", subprocess.list2cmdline(command))
    notice(f"version {__version__}, running {command[-1]} on {target}")

    try:
        # Not in a "with" block: the Relay below owns the process and
        # closes it in Relay.shutdown().
        # pylint: disable-next=consider-using-with
        process = subprocess.Popen(  # nosec B603 # argv list, no shell
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        fatal(f"cannot start ssh: {exc}")

    relay = Relay(process)
    relay.start()
    status = 1
    try:
        status = relay_gui_commands(relay, option_lines)
    except KeyboardInterrupt:
        LOG.info("interrupted from the keyboard")
        status = 130
    finally:
        relay.shutdown()
        LOG.info("%s stopped with status %s", PROG, status)
        logging.shutdown()
    return status


if __name__ == "__main__":
    sys.exit(main())
