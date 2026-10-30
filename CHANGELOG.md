# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog][kac], and this project adheres
to [Semantic Versioning][semver].

## [1.0.0] - 2026-10-30

First release.

### Added

- UCI relay between a local GUI and Lc0 on a remote host, through the
  system OpenSSH client: Windows 10/11, Linux or macOS on the GUI side,
  macOS or Linux on the engine side
- Pure relay: the proxy never waits for an answer of the engine, the GUI
  drives the UCI dialogue alone
- Remote options from `config.json` (`lc0_weights`, `lc0_syzygy`,
  `lc0_nncache`, `lc0_ramlimit_mb`, `lc0_logfile`), sent once before the
  first `isready`, `ucinewgame`, `position` or `go`, so that the
  configured network is the first one Lc0 loads
- GUI `setoption` commands for `WeightsFile`, `SyzygyPath` and `LogFile`
  ignored, since their paths belong to the GUI computer
- Standard error of ssh and Lc0 relayed to the GUI without colour codes,
  except the harmless `Failed to import MPS module` of macOS 27.2, which
  only goes to the log
- Exit of the proxy when the connection or the engine stops, so that the
  GUI sees a stopped engine instead of a silent one
- Startup checks of `config.json`, reported on the standard error:
  absolute remote paths, number ranges, unknown keys
- ssh run with `-T` and `BatchMode=yes`; `-i`, `-p` and the user name are
  only passed when set, so that a `Host` alias of the ssh configuration
  keeps its own values
- `config.example.json` shipped with `StrictHostKeyChecking=accept-new`
  and `ServerAliveInterval=15`
- Optional proxy log with every line in both directions
- Documentation of the optional `restrict,command=` hardening of the
  remote `authorized_keys`, and of the misleading `bench` speeds of large
  networks on Apple Silicon

[kac]: https://keepachangelog.com/en/1.1.0/
[semver]: https://semver.org/spec/v2.0.0.html
