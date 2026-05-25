##### **2026-05-23 18:50:35**
Right now I'm trying to figure out what python libs I'll need for this. What's already obvious to me is that I'll need:
sys - to make sure we're on Windows lol
os - get the absolute path of scripts, strip paths off filenames, checks if there even is a script
time and datetime - essential for the heartbeat mechanism to measure stall durations and the countdown to resume suspended functions
json - I've already commited to using json for the logs, human readable and otherwise.

##### **2026-05-23 18:47:14**
All the Windows functions essential to getting the processes, suspending and resuming them are trapped in DLL files.  ctypes is the library people recommend for this sort of thing. ctypes alone gives the machinery communication but wintypes is what give windows-specific definitions, though I'm not really sure what that means yet. 
##### **2026-05-23 18:50:19**
ctypes allows us to access "EmptyWorkingSet"

A handle to the process. The handle must have the **PROCESS_QUERY_INFORMATION** or **PROCESS_QUERY_LIMITED_INFORMATION** access right and the **PROCESS_SET_QUOTA** access right.

That comes directly from Microsoft docs. If it doesn't work, it returns 0. If it works, it returns.. not zero.

##### **2026-05-23 19:00:06**
Something I'm a little confused about right now is the suspension/resuming functions for processes and threads. 

**SuspendThread** suspends a single thread, and increments the suspend count, so an equal number of **ResumeThread**s are needed to resume a thread, but this can cause issues if between the time you suspend thread 1 and get to thread 4, thread 2 might have done something. 

I've been confused on what "NT" means, apparently it means "New Technology", and it's the modern win kernel architecture used since XP. NT Native API is the layer below Win32 API. The more you know. 

**NtSuspendProcess** is what is going to suspend an entire process, and all its threads. 

**NtResumeProcess** is how we resume the process

I'm learning a lot of helpful stuff from 
https://github.com/diversenok/Suspending-Techniques#snapshot--suspend-threads-not-covered
So shoutout **diversenok** lol

##### **2026-05-23 19:14:18**
**pynput** is what is going to pick up on the mouse and keyboard input to detect a **Rage Event**. Specifically the **keyboard** and **mouse** sub libs. Clicks are going to be easy to measure but I type pretty damn fast so the threshold for keyboard clicks is gonna have to be pretty high. I don't even really rage-click my keyboard but someone out there does. 

##### **2026-05-23 19:17:58**
After searching, I found that the **deque** sublib of **collections** is probably my best friend for the rage event window, seeing as **deque.popleft()** is O(1) and **list.pop()** is O(n)

##### **2026-05-23 19:22:39**
**psutil** is a fancy wrapper for my **ctypes** system calls. 

**`psutil`** — "process and system utilities." A third-party Python library that wraps platform-specific system APIs (Win32 on Windows, procfs on Linux, sysctl on macOS) into a clean, cross-platform Python interface. 

- `psutil.process_iter()` — iterates every running process on the system
- `psutil.Process(pid)` — gets a handle to a specific process by PID
- `proc.name()` — process name (`chrome.exe` etc.)
- `proc.username()` — who owns the process, used by `is_actionable()`
- `proc.nice()` — get or set CPU priority class
- `proc.ionice()` — get or set I/O priority
- `proc.cpu_percent()` — CPU usage over a sample interval
- `proc.memory_info().rss` — resident set size, how much RAM the process is actually occupying right now
- `psutil.virtual_memory().percent` — system-wide memory usage percentage, what the gate checks against `MEM_PRESSURE_PCT`
- The priority class constants — `psutil.BELOW_NORMAL_PRIORITY_CLASS`, `psutil.IDLE_PRIORITY_CLASS`, `psutil.ABOVE_NORMAL_PRIORITY_CLASS`, `psutil.IOPRIO_LOW`, `psutil.IOPRIO_VERYLOW` — are also defined by psutil rather than you having to look up and hardcode the raw integer values yourself

##### **2026-05-23 19:24:37**
**threading** will be important fior the heartbeat function, to know when to stop and start the threads. Should have been obvious to me but i forgor

##### **2026-05-23 19:25:57**
I feel like i'm ready to start coding, I am not really sure how to structure this project in obsidian so I'll start writing in here, and just copy/paste it into vs lol


##### **2026-05-23 19:39:34**
That is an annoying workflow, nevermind. I'll do it the other way around. first I'll run a check to make sure its running on windows
##### **2026-05-23 19:45:06**
I'm not sure if this is new or if my vs code just decided to update but after I start type it gives suggestion for the rest of the line and subsequent line. Kind of helpful

##### **2026-05-23 19:46:33**
sys.exit("Fuck off unix user")

Jk

##### **2026-05-23 19:48:51**
I can't lie I am a little lost, so now I'll preemptively figure out my class structure:

main() - initialization, header

Heartbeat() - for figuring out if my machine is freezing up

RageCounter() - For detecting a **rage event**

Relief() -  How it decides which processes to deprioritize or suspend

##### **2026-05-23 19:51:30**
The theme tonight is **catharsis** so I think I will add a message that displays after the stall has been resolved that tells you what was depri'd or suspended
##### **2026-05-23 19:57:02**
Starting with the heartbeat system, initialize as a daemon and establish float values for when the last stall was, and how long it lasted. 

##### **2026-05-23 20:06:03**
I'm reading up on threading docs, it's not something I've ever used before. Heartbeat has to inherit from threading.Thread, but I'm not sure how that is supposed to work if this will be multi-threaded. I'll be honest I dont know how threads work
##### **2026-05-25 12:59:16**
It's been a day and some change since my last timestamp on here, I got a little bored of the note-taking tbh. Here is what I've realized since I began writing my code:

Shamefully I admit I did kill my computer a few times testing this haha, so I've compiled a list of critical system processes to whitelist. These include:

`"system", "registry", "idle", "memory compression",`

`"csrss.exe", "wininit.exe", "winlogon.exe", "services.exe",`

`"lsass.exe", "smss.exe", "dwm.exe", "explorer.exe",`

`"fontdrvhost.exe", "sihost.exe", "ctfmon.exe", "taskhostw.exe",`

`"svchost.exe", "runtimebroker.exe", "shellexperiencehost.exe",`

`"searchhost.exe", "startmenuexperiencehost.exe", "python.exe",`

`"pythonw.exe", "conhost.exe", "wininit.exe", "audiodg.exe",`

And I made another list of programs that can be starved a little, but never suspended: 

`"searchindexer.exe", "searchprotocolhost.exe",`

`"searchfilterhost.exe", # Windows Search`

`"msmpeng.exe", "nissrv.exe", # Defender`

`"onedrive.exe", # OneDrive sync`

`"sihost.exe", # shell infra (de-prio only)`

I had to define a CFG class to put my whitelists, as well as all of my float constants for stuff like my heartbeat interval counter, **rage event** time window, etc. The CFG is also where most of my NT specific functions are defined, like taking a process snapshot, grabbing process handles, empty working set (best effort), suspend processes, resume processes, etc. 

I've accidently suspended MY OWN python script a few times! I realized I needed a user-specific safeguard, because I was manufacturing the issues that I sought to fix. So I made a fucntion that takes a `psutil.Process` object and returns `True` only if all three conditions hold: the process name is not in `CFG.CRITICAL`, the PID is not 0 or 4 (System and Idle), and the process is owned by the same Windows user running the script.

**`_append_log(record)`** is my logging function. It reads the existing JSON log file into a list, appends the new record dict, and writes the whole thing back. If the file doesn't exist or is malformed it starts fresh.

**`print_log_summary()`** reads the JSON log and prints a human-readable root-cause report: total events, how many were memory-pressure vs CPU-bound, which process names appeared most often at the top of the CPU or memory list at freeze time, and a verdict if memory pressure is recurring.

I still only have 3 real classes, the ones I started with:
Heartbeat, RageCounter, and Main.

I had to do away with the keyboard listener entirely, I just type way too damn fast for it to work. 

I had to add a lock for my threads. `_events` is being written to by two threads simultaneously, the mouse listener thread and the keyboard listener thread are both calling `_record()` concurrently. The main loop thread is also reading `_events` via `rate()` and `is_raging()` at the same time.

`deque` in Python is implemented in C and individual operations like `append` and `popleft` are generally atomic at the bytecode level due to the GIL. So outright corruption of the deque structure itself is unlikely. But the _compound operation_ in `_record()` is not atomic:

My deque kept getting trimmed incorrectly, leading to rate() returning slightly wrong values for the rage counter. My lock prevents two threads from executing Python bytecode simultaneously, which does protect single operations. But it releases between bytecode instructions, and a compound operation like the record/trim loop spans many instructions.

`ctypes.WinDLL` gives you a handle to the DLL but it doesn't know the argument types or return types of any function until you tell it

ctypes will assume every argument and return value is a 32 bit integer. On my 64 bit machine, handles and pointers are 64-bit, so any handle that comes back gets silently truncated to 32 bits before it's stored. The function reports success, you get a handle value back, but it's garbage. Every subsequent operation using that handle `EmptyWorkingSet`, `NtSuspendProcess`, `CloseHandle`  is operating on a corrupted value. The failure mode is unpredictable: access violations, wrong process getting targeted.

To rectify this I had to add explicit type declarations for each wrapper. These go right after the three `WinDLL` lines, before any of the wrapper functions are defined. `wintypes.HANDLE` is already imported via `from ctypes import wintypes` so nothing new was needed.

Two other things I had to change for my 64-bit setup specifically:
The `PROCESS_SET_QUOTA`, `PROCESS_SUSPEND_RESUME`, and `PROCESS_QUERY_LIMITED_INFO` access right constants are the same values on 32 and 64-bit, those are fine as-is. And `NtSuspendProcess` / `NtResumeProcess` return an `NTSTATUS` value which is a 32-bit `LONG` even on 64-bit Windows, so that declaration is correct. The only things that change size between 32 and 64-bit are pointers and handles, which is exactly what `wintypes.HANDLE` accounts for.
##### **2026-05-25 13:30:40**
I've just added my ctypes signatures. The five argtypes and restype declarations are wrapped ibn if sys.platform == "win32" and placed immediately after the DLL handles are opened, before any function is called. Handles are now typed as wintypes.HANDLE (64 bit for my machine), arguments as DWORD or BOOL where appropriate, and NTtSuspendProcess and NtResumeProcess return LONG rather than default integers.

When I test this now, nothing goes terribly wrong. I'm happy to say I haven't been encountering a ton of freezes, I haven't even really had a chance to test this properly on one. But the RageCounter is working, I can invoke it by doing a quick-time event on my mouse, and as far as I can tell, it smoothly and seamlessly deprioritizes heavier processes (I haven't witnessed a suspend yet, whoich means my logic is working.) 

I wanted to work this out and commit it this morning, I have a lot more important things to work on while I look for a new job. I'll come back to this soon, and see if I can't use what I've learned about NT to make any more improvements to my daily operations. I meant to make a design document but I got a little lazy and gave my code to Claude and asked it to make me one. Shelving and committing now : )