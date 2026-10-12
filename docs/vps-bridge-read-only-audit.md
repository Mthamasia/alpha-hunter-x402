# VPS bridge read-only / zero-Helius audit

This file exists because `tests/test_preview.py::test_preview_never_calls_helius_on_any_path`
can only prove one half of "the free preview never touches Helius": that
*this* process (the Railway satellite) never calls Helius directly. It
cannot prove anything about the AHX bridge itself -- a different process,
in a different repository, that this satellite talks to over HTTPS. This
is the other half, verified read-only, point-in-time.

## What was checked, and when

**2026-10-12**, against the live `alpha-hunter-bridge.service` on the AHX
VPS (`178.156.245.236`), read-only, no code changes:

1. **Source-level guarantee (the AHX bridge's own repository, not this
   one)**: `app/x402_bridge/__init__.py` documents, and
   `tests/test_x402_bridge.py` enforces with a `sys.addaudithook`, that
   importing/running the bridge entrypoint never imports any module
   whose name contains `workers`, `scheduler`, `collector`, `helius`, or
   `websocket`, and never opens a `socket.connect`/`socket.getaddrinfo`
   to anything but loopback. `test_no_workers_no_network_no_write_connections_no_db_change`
   additionally asserts every `sqlite3.connect` call during a real
   request ends in `?mode=ro`. Re-run fresh for this audit:
   `22 passed, 4 subtests passed`.

2. **Live process inspection on the VPS** (not a test -- direct
   observation of the actual running service):
   ```
   systemctl is-active alpha-hunter-bridge.service  -> active
   /proc/<pid>/fd                                   -> alpha_hunter.db(-wal/-shm),
                                                        its own listen socket, /dev/null, epoll
   ss -tnp | grep pid=<bridge pid>,                 -> EMPTY (zero established connections,
                                                        inbound or outbound, besides its
                                                        127.0.0.1:8787 listener)
   ```
   The bridge process has no socket open to anything other than its own
   loopback listener. There is nothing to a RPC endpoint, Helius or
   otherwise, open at all.

## What this does not cover

- This is a point-in-time check, not a continuous one. If the AHX bridge
  code changes, this evidence needs to be refreshed.
- It does not audit the separate VPS collector service
  (`alpha-hunter-collector.service`), which is a different, currently
  `inactive` service that legitimately does call Helius when running --
  this audit is scoped to the bridge the free/paid preview-and-investigate
  routes actually call.
