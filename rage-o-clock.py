import ctypes
import json
import os
import sys
import threading
import time
from collections import deque
from ctypes import wintypes
from datetime import datetime

try:
    import psutil
except ImportError:
    sys.exit("psutil not installed")

try:
    from pynput import mouse
except ImportError:
    sys.exit("pynput not installed")


class CFG:
    HEARTBEAT_INTERVAL = 0.5
    STALL_FACTOR       = 2.5
    RAGE_WINDOW        = 3.0
    RAGE_THRESHOLD     = 10
    GATE_OVERLAP       = 4.0
    EVENT_COOLDOWN     = 8.0
    MEM_PRESSURE_PCT   = 85.0
    ENABLE_SUSPEND     = True
    SUSPEND_DURATION   = 4.0
    LOG_PATH           = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "freeze_guard_log.json"
    )
    CRITICAL = {
        "system", "registry", "idle", "memory compression",
        "csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",
        "lsass.exe", "smss.exe", "dwm.exe", "explorer.exe",
        "fontdrvhost.exe", "sihost.exe", "ctfmon.exe", "taskhostw.exe",
        "svchost.exe", "runtimebroker.exe", "shellexperiencehost.exe",
        "searchhost.exe", "startmenuexperiencehost.exe", "python.exe",
        "pythonw.exe", "conhost.exe", "audiodg.exe",
    }
    BACKGROUND_HOGS = {
        "searchindexer.exe", "searchprotocolhost.exe",
        "searchfilterhost.exe",
        "msmpeng.exe", "nissrv.exe",
        "onedrive.exe",
        "sihost.exe",
    }


_psapi  = ctypes.WinDLL("psapi.dll")    if sys.platform == "win32" else None
_ntdll  = ctypes.WinDLL("ntdll.dll")    if sys.platform == "win32" else None
_kernel = ctypes.WinDLL("kernel32.dll") if sys.platform == "win32" else None

if sys.platform == "win32":
    _kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _kernel.OpenProcess.restype  = wintypes.HANDLE

    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    _kernel.CloseHandle.restype  = wintypes.BOOL

    _psapi.EmptyWorkingSet.argtypes = [wintypes.HANDLE]
    _psapi.EmptyWorkingSet.restype  = wintypes.BOOL

    _ntdll.NtSuspendProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtSuspendProcess.restype  = wintypes.LONG

    _ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    _ntdll.NtResumeProcess.restype  = wintypes.LONG

PROCESS_SET_QUOTA          = 0x0100
PROCESS_SUSPEND_RESUME     = 0x0800
PROCESS_QUERY_LIMITED_INFO = 0x1000


def _open(pid, access):
    h = _kernel.OpenProcess(access, False, pid)
    return h or None


def empty_working_set(pid):
    h = _open(pid, PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFO)
    if not h:
        return False
    try:
        return bool(_psapi.EmptyWorkingSet(h))
    finally:
        _kernel.CloseHandle(h)


def nt_suspend(pid):
    h = _open(pid, PROCESS_SUSPEND_RESUME)
    if not h:
        return False
    try:
        return _ntdll.NtSuspendProcess(h) == 0
    finally:
        _kernel.CloseHandle(h)


def nt_resume(pid):
    h = _open(pid, PROCESS_SUSPEND_RESUME)
    if not h:
        return False
    try:
        return _ntdll.NtResumeProcess(h) == 0
    finally:
        _kernel.CloseHandle(h)


_OWN_USER = None


def _own_user():
    global _OWN_USER
    if _OWN_USER is None:
        try:
            _OWN_USER = psutil.Process().username()
        except Exception:
            _OWN_USER = ""
    return _OWN_USER


def is_actionable(proc):
    try:
        name = (proc.name() or "").lower()
        if name in CFG.CRITICAL:
            return False
        if proc.pid in (0, 4):
            return False
        if proc.username() != _own_user():
            return False
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


def is_actionable_bg(proc):
    try:
        name = (proc.name() or "").lower()
        if proc.pid in (0, 4):
            return False
        if name in CFG.CRITICAL and name not in CFG.BACKGROUND_HOGS:
            return False
        return True
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return False


class Heartbeat(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True, name="heartbeat")
        self.last_stall_at  = 0.0
        self.last_stall_dur = 0.0
        self._stop = threading.Event()

    def run(self):
        try:
            psutil.Process().nice(psutil.ABOVE_NORMAL_PRIORITY_CLASS)
        except Exception:
            pass
        while not self._stop.is_set():
            t0  = time.perf_counter()
            time.sleep(CFG.HEARTBEAT_INTERVAL)
            gap = time.perf_counter() - t0
            if gap > CFG.HEARTBEAT_INTERVAL * CFG.STALL_FACTOR:
                self.last_stall_at  = time.time()
                self.last_stall_dur = gap
                print(f"[heartbeat] STALL {gap:5.2f}s (expected {CFG.HEARTBEAT_INTERVAL}s)")

    def stop(self):
        self._stop.set()


class RageCounter:
    def __init__(self):
        self._events = deque()
        self._lock   = threading.Lock()
        self._mouse  = mouse.Listener(on_click=self._on_click)

    def start(self):
        self._mouse.start()

    def stop(self):
        self._mouse.stop()

    def _record(self):
        now = time.time()
        with self._lock:
            self._events.append(now)
            cutoff = now - CFG.RAGE_WINDOW
            while self._events and self._events[0] < cutoff:
                self._events.popleft()

    def _on_click(self, x, y, button, pressed):
        if pressed:
            self._record()

    def rate(self):
        now = time.time()
        with self._lock:
            cutoff = now - CFG.RAGE_WINDOW
            while self._events and self._events[0] < cutoff:
                self._events.popleft()
            return len(self._events)

    def is_raging(self):
        return self.rate() >= CFG.RAGE_THRESHOLD


class Relief:
    def __init__(self):
        self._touched   = {}
        self._suspended = set()
        self._lock      = threading.Lock()

    def _snapshot(self):
        procs = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                p.cpu_percent(None)
                procs.append(p)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(0.3)
        rows = []
        for p in procs:
            try:
                rows.append({
                    "pid":  p.pid,
                    "name": p.name(),
                    "cpu":  p.cpu_percent(None),
                    "mem":  p.memory_info().rss,
                    "proc": p,
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        mem_pct = psutil.virtual_memory().percent
        by_cpu  = sorted(rows, key=lambda r: r["cpu"], reverse=True)
        by_mem  = sorted(rows, key=lambda r: r["mem"], reverse=True)
        return mem_pct, by_cpu, by_mem

    def _pick_offender(self, mem_pct, by_cpu, by_mem):
        ranking = by_mem if mem_pct >= CFG.MEM_PRESSURE_PCT else by_cpu
        for row in ranking:
            if is_actionable(row["proc"]):
                return row, ("memory" if ranking is by_mem else "cpu")
        return None, None

    def _deprioritize(self, proc):
        try:
            with self._lock:
                if proc.pid not in self._touched:
                    self._touched[proc.pid] = proc.nice()
            proc.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
            try:
                proc.ionice(psutil.IOPRIO_LOW)
            except Exception:
                pass
            return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False

    def _calm_background_hogs(self):
        calmed = []
        for p in psutil.process_iter(["pid", "name"]):
            try:
                name = (p.info["name"] or "").lower()
                if name in CFG.BACKGROUND_HOGS and is_actionable_bg(p):
                    p.nice(psutil.IDLE_PRIORITY_CLASS)
                    try:
                        p.ionice(psutil.IOPRIO_VERYLOW)
                    except Exception:
                        pass
                    calmed.append(f"{name}({p.pid})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return calmed

    def _suspend_then_resume(self, pid, name):
        if not nt_suspend(pid):
            return False
        with self._lock:
            self._suspended.add(pid)
        print(f"  >> SUSPENDED {name} (PID {pid}) -- auto-resume in {CFG.SUSPEND_DURATION:.0f}s")

        def _later():
            time.sleep(CFG.SUSPEND_DURATION)
            nt_resume(pid)
            with self._lock:
                self._suspended.discard(pid)
            print(f"  >> RESUMED {name} (PID {pid})")

        threading.Thread(target=_later, daemon=True).start()
        return True

    def run(self, stall_dur):
        mem_pct, by_cpu, by_mem = self._snapshot()
        offender, reason = self._pick_offender(mem_pct, by_cpu, by_mem)

        record = {
            "ts":            datetime.now().isoformat(timespec="seconds"),
            "stall_seconds": round(stall_dur, 2),
            "mem_percent":   mem_pct,
            "bottleneck":    reason,
            "top_cpu": [
                {"name": r["name"], "pid": r["pid"], "cpu": r["cpu"]}
                for r in by_cpu[:5]
            ],
            "top_mem": [
                {"name": r["name"], "pid": r["pid"], "mem_mb": round(r["mem"] / 1048576, 1)}
                for r in by_mem[:5]
            ],
            "actions": [],
        }

        if not offender:
            record["actions"].append("no actionable offender found")
            print("[relief] no actionable offender -- freeze is likely a system/driver process")
            _append_log(record)
            return record

        proc = offender["proc"]
        name = offender["name"]
        pid  = offender["pid"]
        print(f"[relief] offender: {name} (PID {pid}) -- bottleneck: {reason}")

        if self._deprioritize(proc):
            record["actions"].append(f"deprioritized {name}({pid})")

        if empty_working_set(pid):
            record["actions"].append(f"trimmed working set {name}({pid})")

        calmed = self._calm_background_hogs()
        if calmed:
            record["actions"].append("calmed background: " + ", ".join(calmed))

        if CFG.ENABLE_SUSPEND:
            if self._suspend_then_resume(pid, name):
                record["actions"].append(f"suspended {name}({pid}) for {CFG.SUSPEND_DURATION:.0f}s")

        _append_log(record)
        return record

    def restore(self):
        with self._lock:
            for pid, original in list(self._touched.items()):
                try:
                    psutil.Process(pid).nice(original)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
            self._touched.clear()
            for pid in list(self._suspended):
                nt_resume(pid)
            self._suspended.clear()
        print("[relief] restored all priorities, resumed all suspended")


def _append_log(record):
    data = []
    if os.path.exists(CFG.LOG_PATH):
        try:
            with open(CFG.LOG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = []
    data.append(record)
    try:
        with open(CFG.LOG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        print(f"[log] write failed: {e}")


def print_log_summary():
    if not os.path.exists(CFG.LOG_PATH):
        print("no log yet.")
        return
    with open(CFG.LOG_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"\nFREEZE_GUARD :: {len(data)} event(s) logged")
    mem_events = sum(1 for r in data if r.get("bottleneck") == "memory")
    cpu_events = sum(1 for r in data if r.get("bottleneck") == "cpu")
    print(f"  memory-pressure events: {mem_events}")
    print(f"  cpu-bound events:       {cpu_events}")
    tally = {}
    for r in data:
        for row in r.get("top_mem", [])[:1] + r.get("top_cpu", [])[:1]:
            tally[row["name"]] = tally.get(row["name"], 0) + 1
    if tally:
        print("  most frequent offender (top of cpu/mem at freeze time):")
        for name, n in sorted(tally.items(), key=lambda x: -x[1])[:5]:
            print(f"    {name:30s} {n}x")
    if mem_events > cpu_events and mem_events >= 3:
        print("\n  VERDICT: recurring memory pressure. the durable fix is "
              "more RAM or fewer concurrent heavy apps.")


def main():
    if sys.platform != "win32":
        sys.exit("Fuck off unix user")

    print("=" * 60)
    print("RAGE-O-CLOCK - ARMED! TIME TO RAAAAGEE!!!!!!!")
    print(f"  heartbeat : stall if gap > {CFG.HEARTBEAT_INTERVAL * CFG.STALL_FACTOR:.1f}s")
    print(f"  rage      : burst if >= {CFG.RAGE_THRESHOLD} clicks / {CFG.RAGE_WINDOW:.0f}s")
    print(f"  suspend   : {'ENABLED' if CFG.ENABLE_SUSPEND else 'disabled'}")
    print(f"  log       : {CFG.LOG_PATH}")
    print("  ctrl-c to stop ")
    print("=" * 60)

    _own_user()
    heartbeat = Heartbeat()
    rage      = RageCounter()
    relief    = Relief()
    heartbeat.start()
    rage.start()

    last_event = 0.0
    try:
        while True:
            time.sleep(0.25)
            now = time.time()

            stall_recent = (now - heartbeat.last_stall_at) < CFG.GATE_OVERLAP
            raging       = rage.is_raging()
            cooled       = (now - last_event) > CFG.EVENT_COOLDOWN

            if stall_recent and raging and cooled:
                print(f"\n[gate] RAGE EVENT -- stall {heartbeat.last_stall_dur:.2f}s"
                      f" + rage {rage.rate()} clicks/{CFG.RAGE_WINDOW:.0f}s")
                relief.run(heartbeat.last_stall_dur)
                last_event = time.time()
                print()
    except KeyboardInterrupt:
        print("stopping...")
    finally:
        heartbeat.stop()
        rage.stop()
        relief.restore()
        print_log_summary()
        print("disarmed.")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "log":
        print_log_summary()
    else:
        main()
