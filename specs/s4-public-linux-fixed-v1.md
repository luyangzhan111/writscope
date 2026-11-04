# Linux public fixed transport compatibility v1

This is an explicit publication compatibility layer, not a paid experiment,
new model method, or change to frozen source/response/score bytes. Windows is
unchanged. Linux accepts only `mode=fixed`, no credential, and a fixed-response
dictionary; all other modes reject before process creation. Other platforms
are unsupported by this adapter.

`compatibility()` serializes process-global wiring and restores it on exit,
including exceptional exit. The frozen request, HTTP framing, byte limits,
deadline, send marker, receive persistence, model parser, journal and R4 are
reused. Only the original worker argv is redirected. Other subprocess calls
pass to the original Popen. The redirected worker starts a dedicated session;
no `preexec_fn` is used.

The new bootstrap accepts no caller-selected script. It checks its expected
parent before and after Linux `prctl(PR_SET_PDEATHSIG,SIGKILL)`, reads at most
1,500,000 configuration bytes, requires exactly mode/wire/fixed, verifies the
wire using the frozen checker, then invokes the frozen fixed child function.
The frozen child still requires the parent's durable dispatch permission and
credential=null before using HTTP MockTransport. It never selects live HTTP.

LinuxOwner verifies pgid==pid. While the child remains alive, close sends
SIGKILL to its dedicated group; the old receiver waits/reaps and joins readers
and writers. A reaped worker is not signaled again, avoiding recycled PIDs.
The trusted fixed worker creates no child processes. Parent-death SIGKILL is
a kernel guarantee for that worker; this is not a claim of a general sandbox
or crash-time kill of arbitrary descendant trees.

Actual receipts have version `s4_public_linux_fixed_receipt_v1` and guard
`linux_pdeathsig_process_group_fixed_v1`. These native values are persisted.
For validation only, the adapter makes a new in-memory dictionary projecting
version to `s4_owner_pilot_transport_v1` and guard to
`windows_job_kill_on_close`, then delegates all original structural/byte/time
checks. This is a declared vocabulary compatibility projection, not evidence
of a Windows Job on Linux. The actual receipt is never modified or rewritten.
Native receipts with live mode/backend, wrong guard, or mismatched version
are rejected. Historical Windows receipts continue directly through the
original validator, including read-only historical live receipt replay.

Publication overlays bind this module, its tests/spec and the publication
entry separately; they do not rewrite the original 1,046 file manifest,
source archive, model contracts, candidate hash, original scores or receipts.
Tests exercise denial before spawn, exact native metadata, immutable receipts,
historical delegation, global patch restoration, selective argv routing and
overlay corruption. Native fixed-process, timeout and parent-death controls
are Linux-only; skips on Windows are not Linux validation. No real key,
model request or fee is authorized by this adapter.
