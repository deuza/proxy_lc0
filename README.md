[![GitHub release](https://img.shields.io/github/v/release/deuza/proxy_lc0?style=plastic)](https://github.com/deuza/proxy_lc0/releases)
![GitHub Release Date](https://img.shields.io/github/release-date/deuza/proxy_lc0)
[![GitHub last commit](https://img.shields.io/github/last-commit/deuza/proxy_lc0?style=plastic)](https://github.com/deuza/proxy_lc0/commits/main)
![GitHub commit activity](https://img.shields.io/github/commit-activity/t/deuza/proxy_lc0)

[![License: CC0](https://img.shields.io/badge/license-CC0_1.0-lightgrey.svg?style=plastic)](https://creativecommons.org/publicdomain/zero/1.0/)
![Hack The Planet](https://img.shields.io/badge/hack-the--planet-black?style=flat-square\&logo=gnu\&logoColor=white)
![Built With Love](https://img.shields.io/badge/built%20with-%E2%9D%A4%20by%20DeuZa-red?style=plastic)

# proxy_lc0

A small Python proxy that runs Lc0 (Leela Chess Zero) on a remote host over
native OpenSSH, and exposes it to any local UCI GUI (Nibbler, Arena, Lucas
Chess, ...) as if the engine were installed locally.

Typical use: the GUI runs on a Windows laptop without a suitable GPU, while
Lc0 runs on an Apple Silicon Mac (Metal backend) or on a Linux box with a
GPU.

Companion project of
[proxy_stockfish](https://github.com/deuza/proxy_stockfish).

A French version of this document is available in
[README.fr.md](README.fr.md).

## How it works

```text
+-------------+  stdin   +-----------+   ssh    +------------------------+
|  UCI GUI    | -------> |           | -------> |  Lc0 on the remote     |
|  (Nibbler)  | <------- | proxy_lc0 | <------- |  host (Metal, CUDA...) |
|             |  stdout  |           |          |                        |
|  info box   | <------- |           | <------- |  stderr of ssh and Lc0 |
+-------------+  stderr  +-----------+          +------------------------+
```

The GUI starts the proxy as a local UCI engine. The proxy starts the system
`ssh` client, which runs Lc0 on the remote host, and relays the UCI lines in
both directions. It never waits for an answer of the engine: the GUI keeps
full control of the UCI dialogue.

On top of this plain relay, the proxy does three things:

- Before the first command that may prepare or start a search (`isready`,
  `ucinewgame`, `position` or `go`), it sends the options of `config.json`
  that describe the remote host: log file, network, Syzygy folders, cache
  size and RAM limit. Lc0 loads its network on the first `ucinewgame`,
  `position` or `go`, so the configured network is the first one loaded.
- It ignores the `setoption` commands of the GUI for `WeightsFile`,
  `SyzygyPath` and `LogFile`: these are paths on the GUI computer, which
  mean nothing on the remote host.
- It copies the standard error of ssh and Lc0 to its own standard error,
  so the GUI can display it (Nibbler shows it in its info box). A known
  harmless macOS message is kept out of the GUI and only goes to the log
  (see Troubleshooting).

Every other command and option goes through unchanged. If the connection
or the engine dies, the proxy exits, so the GUI sees a stopped engine
instead of a silent one.

## Requirements

On the GUI computer:

- The OpenSSH client (`ssh` in the PATH). Windows 10 (1809 or later) and
  Windows 11 provide it as an optional feature: Settings > System >
  Optional features > OpenSSH Client. Check with `ssh -V`.
- Python 3.8 or later, only to run the script or to build the executable.
  The proxy uses the Python standard library only.

On the remote host:

- Lc0 and a network file. The proxy was developed against Lc0 0.32.1.
- An SSH server with key authentication. On macOS: System Settings >
  General > Sharing > Remote Login.
- Optional: Syzygy tablebases.

## Remote host setup

The example below is a Mac with Homebrew, adapt the paths to your host.

Install Lc0 and note its absolute path:

```sh
brew install lc0
which lc0
```

On Apple Silicon, `which lc0` gives `/opt/homebrew/bin/lc0`. A command run
through ssh does not read `~/.zprofile`, where Homebrew sets its PATH, so
the proxy needs this absolute path.

Download a network from the
[Lc0 Best Networks page](https://lczero.org/play/networks/bestnets/) and
store it on the remote host, for example in `/Users/user/lc0/`.
Gzipped (`.pb.gz`) and plain (`.pb`) files both work. Without a configured
network, Lc0 takes the most recent network found next to its binary: with
Homebrew, the small network shipped by the
[formula](https://formulae.brew.sh/formula/lc0).

Syzygy tables are optional. Lc0 looks for the table files directly inside
each listed folder, never in sub-folders. Lc0 does not use Gaviota tables.

From the GUI computer, check the whole chain once by hand. This first
interactive connection also lets you check and accept the host key:

```sh
ssh user@host /opt/homebrew/bin/lc0 bench --weights=/Users/user/lc0/net.pb
```

Expected: `Loading weights file from:` with your network, then, on a Mac,
`Initialized metal backend on device Apple M1` (or your chip), and the
benchmark results ending with `Nodes/second`.

With a large network on Apple Silicon, `bench` reports very low speeds. The
Metal backend compiles the network once for each batch size, which takes
about 200 ms for a network like BT3 on an M1, and the 500 ms that `bench`
gives each position are spent compiling. To measure the real speed, use
`backendbench`, which repeats a fixed batch size:

```sh
/opt/homebrew/bin/lc0 backendbench --weights=/Users/user/lc0/net.pb \
  --start-batch-size=64 --max-batch-size=64 --batches=10
```

A long analysis gives the same answer: with BT3, an Apple M1 reached about
260 nodes per second once the first 20 seconds of compilation were over.

### Optional hardening

The key used by the proxy only ever needs to start Lc0, so you can lock it
down on the remote host. Use a dedicated key, since it will no longer open
a shell. In `~/.ssh/authorized_keys` on the remote host, prefix this key
with a forced command and the `restrict` option:

```text
restrict,command="/opt/homebrew/bin/lc0" ssh-ed25519 AAAA... you@laptop
```

The key can then only start Lc0, whatever command the client asks for: no
shell, no forwarding, no pseudo-terminal. The remote host ignores
`lc0_command`, so Lc0 command-line flags, if any, go in the forced command.
See the AUTHORIZED_KEYS FILE FORMAT section of sshd(8).

## Configuration

Copy `config.example.json` to `config.json`, in the same folder as
`proxy_lc0.py` or `proxy_lc0.exe`, and edit it. Keys starting with `_` are
comments. Settings:

- `host` (required): host name, address, or a `Host` alias of your ssh
  configuration.
- `username`: remote user. Omitted: ssh default (ssh configuration or
  local user name).
- `port`: SSH port. Omitted: ssh default (ssh configuration or 22).
- `key_file`: private key. `~` is expanded, a relative path is relative to
  the proxy folder. Omitted: default keys and agent of ssh.
- `ssh_options`: extra ssh arguments, as a list of strings. They come
  first on the command line, so they win over the proxy defaults.
- `lc0_command` (required): command run on the remote host, the absolute
  path of Lc0, optionally followed by Lc0 command-line flags.
- `lc0_weights`: network file (`WeightsFile`).
- `lc0_syzygy`: Syzygy folder (`SyzygyPath`), or a list of folders.
- `lc0_nncache`: size of the network cache, in positions (`NNCacheSize`,
  Lc0 default 2000000).
- `lc0_ramlimit_mb`: memory limit of the search, in MB (`RamLimitMb`, Lc0
  default 0, no limit). See below.
- `lc0_logfile`: Lc0 log file on the remote host (`LogFile`), appended at
  each run. Useful to see exactly what Lc0 received.
- `logging_enabled`: `true` to write the proxy log (default `false`).
- `log_file`: proxy log file, default `debug.log` in the proxy folder.

All `lc0_*` paths are paths on the remote host. They must be absolute and
start with `/`: Lc0 opens them as they are and does not expand `~`.

The keys missing from `config.json` send nothing: Lc0 keeps its defaults.
The proxy checks the file at startup and reports errors on its standard
error, so the GUI shows them. To check the JSON syntax alone:

```sh
python -m json.tool config.json
```

The `ServerAliveInterval=15` option of the example makes ssh detect a dead
connection after about 45 seconds, after which the proxy exits.

The example also sets `StrictHostKeyChecking=accept-new`: ssh records the
key of an unknown host at the first connection, without asking, and refuses
a host whose key has changed. Without this option, connect once by hand
before using the proxy (see the remote host setup), since the proxy runs
ssh in batch mode and cannot answer the question.

### Memory

During an infinite analysis, the search tree of Lc0 keeps growing with the
number of nodes. `RamLimitMb` stops the growth when the estimate of Lc0
(search tree plus network cache, the network itself not included) reaches
the limit; Lc0 still answers `stop` with its best move, as UCI requires.
The Lc0 help warns that this estimate is rough. Choose the limit from the
RAM of the remote host, minus the system and the network: for instance
6000 on a 16 GB Mac with a network of about 2.6 GB.

## Build the Windows executable

In the project folder, in PowerShell or cmd, with a dedicated virtual
environment:

```powershell
python -m venv venv
.\venv\Scripts\pip install pyinstaller
.\venv\Scripts\pyinstaller --onefile --console --name proxy_lc0 proxy_lc0.py
copy config.json dist\
```

The executable is `dist\proxy_lc0.exe`, with its `config.json` next to it.
Keep `--console`: the UCI dialogue goes through standard input and output.
No prebuilt executable is provided, build your own from the source.

## Manual test

Run `proxy_lc0.exe` (or `python proxy_lc0.py`) in a terminal and type:

```text
uci
isready
position startpos
go nodes 200
quit
```

Expected: the option list of Lc0 ending with `uciok`, then `readyok`, some
`info` lines and `bestmove`. The lines of Lc0 standard error, such as
`Loading weights file from:`, appear in the terminal too.

## Use in a GUI

Nibbler: Engine > Choose engine..., then select `proxy_lc0.exe`. Nothing
else to set: Nibbler recognises Lc0 through its `id name` line and sends
its usual options. Engine > Weights > Lc0 WeightsFile... and Engine >
Choose Syzygy path... have no effect through the proxy, which says so in
the info box: the remote paths come from `config.json`.

Other GUIs: install `proxy_lc0.exe` as a UCI engine, like a local engine.

## Troubleshooting

The proxy log (`logging_enabled`) records every line in both directions
(`>>` from the GUI, `<<` from the engine), the standard error lines, and
the options sent or ignored. It grows fast during an analysis: enable it
only to diagnose.

Messages that the GUI may show:

- `Host key verification failed.`: the host key has changed, or the host
  is unknown and `StrictHostKeyChecking=accept-new` is not set. Check the
  key, then run the ssh command of the remote host setup once by hand.
- `Permission denied (publickey)`: the remote host refuses the key, check
  `key_file` and the `authorized_keys` file of the remote user.
- `command not found` or `No such file or directory`, then `ssh exit
  status 127`: `lc0_command` must be the absolute path of Lc0.
- `Cannot read weights from`: check `lc0_weights`.
- `Failed to load Syzygy tablebases!`: check `lc0_syzygy`.
- `connection to the remote engine closed`: the engine or the connection
  stopped; the proxy exits so that the GUI can restart the engine.

`Failed to import MPS module` appears in the proxy log only. Seen on macOS
27.2 with an Apple M1, once for each new batch size: the Neural Engine
compiler rejects part of the network and MPSGraph keeps it on the GPU. The
speed was measured identical with and without this message, so the proxy
keeps it out of the GUI, where Nibbler would show it in red.

To stop an engine left running on the remote host (this stops every Lc0 of
that user):

```sh
ssh user@host pkill -x lc0
```

With the optional hardening, run this command with another key or from a
local session: the restricted key only starts Lc0.

## Tested setup

- GUI side: Nibbler on Windows 11, with the OpenSSH client of Windows. The
  proxy also ran on Debian 13 and macOS.
- Engine side: Apple M1 with 16 GB, macOS 27.2, Lc0 0.32.1 from Homebrew
  with the Metal backend, Syzygy tablebases; Debian 13, with Lc0 0.32.1
  built from source.
- Speed on the Apple M1: about 2,100 nodes per second with the network
  shipped by Homebrew, about 260 with BT3-768x15x24h-swa-2790000 once warm.

## Limits

- The remote host must run macOS or Linux (absolute POSIX paths).
- Each running proxy starts its own Lc0 on the remote host, with its own
  copy of the network in memory: avoid many instances at once, for
  instance tournaments with several workers.

## Differences with proxy_stockfish

- The relay never waits: no timeout and no automatic `stop`, the GUI
  drives the UCI dialogue alone.
- `config.json` only describes the remote host (network, tables, memory);
  the analysis options stay in the hands of the GUI.
- The standard error of the remote side reaches the GUI.

## License

[CC0 1.0 Universal](https://creativecommons.org/publicdomain/zero/1.0/). Do
whatever you want with it.

---

Tested with Lc0 0.32.1, clients on Windows 11, Debian 13 and macOS, servers
on Debian 13 and macOS (Homebrew).
