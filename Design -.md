# Rage-o-Clock — Design Document

## Overview

Rage-o-clock is a Windows background utility that detects system freeze events coinciding with user frustration (rapid mouse clicking) and applies graduated process relief to the most likely offending process. It is a module within the SYSCLEAN suite.

The core premise: a system freeze during basic tasks is almost never a clock-speed problem. It is a resource-contention problem — memory pressure forcing paging, a runaway process monopolizing CPU, or background services competing for disk I/O. Overclocking does nothing for a CPU that is already idle and waiting on a bottleneck. The correct response is to identify what is actually choking the machine and reduce its priority or remove it from contention entirely.

---

## Requirements

### Functional

- Detect OS scheduling stalls by measuring the gap between heartbeat thread wake cycles
- Detect user frustration via rolling-window mouse click rate
- Gate relief events on the AND of both signals to eliminate false positives
- Identify the primary offending process by CPU or memory consumption depending on system state
- Apply graduated relief: CPU priority reduction, I/O priority reduction, working set trim, optional process suspension
- De-prioritize known background hogs independently of the primary offender
- Auto-resume any suspended process after a fixed interval
- Restore all modified priorities and resume all suspended processes on clean exit
- Log every gated event to JSON with process snapshots for root-cause analysis

### Non-Functional

- Windows only
- Must never touch processes not owned by the current user
- Must never touch a hardcoded critical process whitelist regardless of resource consumption
- Must restore all system state on exit
- Must not require elevated privileges for user-owned process relief (admin helps for background services)
- Suspend behavior must be opt-out via a single config flag

### Out of Scope

- Linux / macOS support
- Kernel-level intervention
- Driver-related freeze recovery
- Automatic diagnosis of hardware faults

---

## Configuration Reference

All tunable values live in the `CFG` class.

|Parameter|Default|Description|
|---|---|---|
|`HEARTBEAT_INTERVAL`|`0.5s`|Expected sleep duration between heartbeat beats|
|`STALL_FACTOR`|`2.5`|Multiplier — gap exceeding interval × factor is a stall|
|`RAGE_WINDOW`|`3.0s`|Rolling window for click event counting|
|`RAGE_THRESHOLD`|`10`|Clicks within the window required to trigger rage signal|
|`GATE_OVERLAP`|`4.0s`|Stall and rage must occur within this window to gate|
|`EVENT_COOLDOWN`|`8.0s`|Minimum time between successive relief events|
|`MEM_PRESSURE_PCT`|`85.0`|Memory percent above which offender selection switches to RAM ranking|
|`ENABLE_SUSPEND`|`True`|Enables NtSuspendProcess relief step|
|`SUSPEND_DURATION`|`4.0s`|Auto-resume delay after suspension|
|`DRY_RUN`|`False`|Logs intended actions without executing them (recommended for initial testing)|

---

## Program Structure

Three concurrent components feed into a polling AND-gate on the main thread.

```
[Heartbeat thread]  →  last_stall_at, last_stall_dur
[Mouse listener thread]  →  rolling click deque
                                    ↓
[Main loop, 250ms poll]  →  stall_recent AND raging AND cooled?
                                    ↓
                            [Relief.run()]
                              snapshot → pick offender → deprioritize
                              → trim working set → calm hogs → suspend
                                    ↓
                            [JSON log append]
```

On exit (KeyboardInterrupt), a `finally` block restores all priorities and resumes all suspended processes before termination.

---

## The Three Main Classes

### `Heartbeat(threading.Thread)`

Runs on its own daemon thread. Measures OS scheduling responsiveness by sleeping for `HEARTBEAT_INTERVAL` and comparing the actual elapsed time against the expected time. If the gap exceeds `HEARTBEAT_INTERVAL × STALL_FACTOR` (default: 1.25s), the OS failed to schedule this thread on time — indicating the system was contended. Records `last_stall_at` and `last_stall_dur` for the gate to read. Sets its own priority to `ABOVE_NORMAL` on startup so it recovers early after a stall and measures stall duration accurately.

Key attributes:

- `last_stall_at` — wall-clock timestamp of the most recent stall
- `last_stall_dur` — duration in seconds of the most recent stall
- `_stop` — `threading.Event` used to signal clean shutdown

### `RageCounter`

Manages a global mouse click listener via `pynput`. Maintains a `deque` of click timestamps pruned to a rolling `RAGE_WINDOW`. Thread-safe via `threading.Lock` — the pynput listener fires callbacks on its own internal thread, so the deque is shared mutable state between that thread and the main loop reading `rate()`.

Key methods:

- `rate()` — returns the number of clicks recorded within the current window
- `is_raging()` — returns `True` if `rate() >= RAGE_THRESHOLD`
- `_record()` — appends a timestamp and prunes stale entries; always called under lock

### `Relief`

Stateful action handler. Tracks which processes have had their priorities modified (`_touched` dict mapping PID to original priority) and which are currently suspended (`_suspended` set). Both structures are protected by a `threading.Lock` because the auto-resume timer fires on a separate thread.

Key methods:

- `_snapshot()` — two-pass CPU measurement with 0.3s sleep between passes; returns memory percent and processes sorted by CPU and RAM
- `_pick_offender()` — selects target based on whether system is in memory pressure or CPU contention
- `_deprioritize()` — sets CPU priority to `BELOW_NORMAL`, I/O priority to `IOPRIO_LOW`; saves original priority in `_touched`
- `_calm_background_hogs()` — sets known background processes to `IDLE_PRIORITY_CLASS` and `IOPRIO_VERYLOW`
- `_suspend_then_resume()` — calls `nt_suspend()`, spawns a daemon thread that calls `nt_resume()` after `SUSPEND_DURATION`
- `restore()` — resets all modified priorities from `_touched`, resumes all PIDs in `_suspended`; called in `finally` block on exit

---

## Essential System Functions

### Win32 / NT API (via ctypes)

All called through `ctypes.WinDLL`. Argument and return types are explicitly declared to prevent 32-bit truncation of 64-bit handles on x64 systems.

|Function|DLL|Access Flag Required|Purpose|
|---|---|---|---|
|`OpenProcess`|`kernel32.dll`|varies|Opens a handle to a process by PID with specified permissions|
|`CloseHandle`|`kernel32.dll`|—|Releases an open process handle; must be called after every OpenProcess|
|`EmptyWorkingSet`|`psapi.dll`|`PROCESS_SET_QUOTA`|Trims the process's resident pages back to the OS standby list; reclaims RAM without killing the process|
|`NtSuspendProcess`|`ntdll.dll`|`PROCESS_SUSPEND_RESUME`|Atomically suspends every thread in the process in a single kernel call|
|`NtResumeProcess`|`ntdll.dll`|`PROCESS_SUSPEND_RESUME`|Resumes all threads in a previously suspended process|

### Access Flag Constants

|Constant|Value|Used For|
|---|---|---|
|`PROCESS_SET_QUOTA`|`0x0100`|Required for `EmptyWorkingSet`|
|`PROCESS_SUSPEND_RESUME`|`0x0800`|Required for `NtSuspendProcess` / `NtResumeProcess`|
|`PROCESS_QUERY_LIMITED_INFO`|`0x1000`|Combined with `SET_QUOTA` for working set trim|

### psutil

High-level cross-platform wrapper over Win32 process APIs. Used for everything except suspension and working set management.

|Call|Purpose|
|---|---|
|`process_iter()`|Enumerate all running processes|
|`Process(pid).nice()`|Get or set CPU priority class|
|`Process(pid).ionice()`|Get or set I/O priority|
|`Process(pid).cpu_percent()`|CPU usage over a sample interval (requires two calls)|
|`Process(pid).memory_info().rss`|Resident set size — actual RAM currently occupied|
|`Process(pid).username()`|Process owner — used by safety guard|
|`virtual_memory().percent`|System-wide memory utilization|

---

## Safety Architecture

### `is_actionable(proc)`

Primary safety guard. A process must pass all three checks before FREEZE_GUARD will touch it:

1. Name is not in `CFG.CRITICAL`
2. PID is not 0 (System) or 4 (Idle)
3. Owner matches the current user via `psutil.Process().username()` — returns `DOMAIN\username` format on Windows; cached on startup to avoid per-call overhead

### `is_actionable_bg(proc)`

Relaxed guard for background hog de-prioritization only. Omits the ownership check because processes like Defender and the Search Indexer run as SYSTEM but are safe to de-prioritize. Still excludes anything on `CFG.CRITICAL` that is not also in `CFG.BACKGROUND_HOGS`.

### Critical Process Whitelist

Processes FREEZE_GUARD will never touch under any circumstances. Suspending any of these causes desktop hangs, BSODs, or session termination.

```
system, registry, idle, memory compression,
csrss.exe, wininit.exe, winlogon.exe, services.exe,
lsass.exe, smss.exe, dwm.exe, explorer.exe,
fontdrvhost.exe, sihost.exe, ctfmon.exe, taskhostw.exe,
svchost.exe, runtimebroker.exe, shellexperiencehost.exe,
searchhost.exe, startmenuexperiencehost.exe,
python.exe, pythonw.exe, conhost.exe, audiodg.exe
```

### Background Hog List

Processes that are de-prioritized (never suspended) when active during a relief event.

```
searchindexer.exe, searchprotocolhost.exe, searchfilterhost.exe,
msmpeng.exe, nissrv.exe,
onedrive.exe, sihost.exe
```

---

## Known Limitations

**Suspend risk on resource holders** — `NtSuspendProcess` is atomic but not consequence-free. A suspended process holding a mutex or critical section that another process is waiting on can cause a secondary hang. The 4-second auto-resume bounds the exposure but does not eliminate it. `ENABLE_SUSPEND = False` drops to priority-only relief if this becomes a problem.

**Defender priority restoration** — Windows may automatically restore `MsMpEng.exe` priority if it detects it has been depressed, as a malware-resistance measure. De-prioritization of Defender may silently revert within seconds.

**Log file race** — `_append_log` reads, appends, and writes the entire JSON file. Two relief events firing in rapid succession (possible if `EVENT_COOLDOWN` is lowered for testing) can result in the first event being overwritten by the second. The default cooldown makes this unlikely in production.

**No keyboard signal** — rage detection is click-only. Fast typists during a freeze who do not click will not trigger the gate. This is intentional — keyboard rate produces more false positives than click rate.

**ctypes on non-Win32** — DLL handles are set to `None` on non-Windows platforms and the platform check in `main()` exits immediately. The wrappers are not callable outside Windows.

---

## Log Format

Each gated event appends one JSON object to `freeze_guard_log.json` in the script directory.

```json
{
  "ts": "2026-05-25T14:32:01",
  "stall_seconds": 1.84,
  "mem_percent": 91.2,
  "bottleneck": "memory",
  "top_cpu": [
    { "name": "chrome.exe", "pid": 8421, "cpu": 34.2 }
  ],
  "top_mem": [
    { "name": "chrome.exe", "pid": 8421, "mem_mb": 4201.3 }
  ],
  "actions": [
    "deprioritized chrome.exe(8421)",
    "trimmed working set chrome.exe(8421)",
    "calmed background: searchindexer.exe(1204)",
    "suspended chrome.exe(8421) for 4s"
  ]
}
```

Run `python freeze_guard.py log` to print a summary of all logged events with offender frequency ranking and a memory-pressure verdict if applicable.

---

## Dependencies

```
psutil      pip install psutil
pynput      pip install pynput
```

Python 3.8+. Windows 10 / 11 x64. No elevated privileges required for user-owned process relief; admin recommended for background service de-prioritization.