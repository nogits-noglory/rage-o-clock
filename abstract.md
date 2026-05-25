I'm getting increasingly frustrated with my PC freezing, stalling, crashing, becoming unresponsive under modest working conditions. I am trying to devise a system that detects "rage", this usually looks like button mashing. When applications freeze, my instinct is to mash my mouse. Though I know this is in vain, my frustration tells me this will encourage my computer to perform better and get over the hump. My system aims to make this rage constructive.

Someone will read this and ask about my PC, and they'd be right to. My motherboard and CPU were top of the line in 2013. My GPU predates ray-tracing I'm pretty sure. When I feel like dropping a grand on a new rig, my haters will be vindicated, but until then this DIY boy will have to make do. 

Initially, I was under the impression that a short overclock might get my PC through a stall. This isn't the case...with a few caveats. When applications freeze, keyboard input is delayed, hover animations take seconds to play, the I/o wait and memory is usually the culprit. Meanwhile, the CPU is sitting pretty, perhaps even idle. Overclocking won't really do much for me in such cases. 

So here is my thinking: a "rage gesture" (that's what I'll be calling from now on) is easy to detect, pynput makes it easy to create a hook that can detect rage-mashing based on interval and mouse location. That part is worked out, albeit abstractly. I've never actually used that library. 

The actual helpful mechanisms at play will be:

**Heartbeat watcher -**
This is the freeze detector that works indepently of your rage. Dedicated memory threads sleep for roughly 500ms and timestamps each wakeup. If a gap is distinctly larger than the interval, say 2 seconds, this is a pretty good sign that the system has stalled. If this thread were given slightly above normal priority, this could make an application recover pretty swiftly. 

**The gate -**

The gate is triggered when the heartbeat watcher and the rage detector both detect a "rage event". When this happens, a screenshot of all system processes is taken with psutil, and then we figure out who the culprit(s) are. Specifically, psutil.virtual_memory().percent to see what is hogging all my damn ram. After the culp(s) have been identified, we drop their memory, CPU and I/O priority. We have to do this for background processes as well (often, in my case). In rougher waters we can do the more cathartic thing and fully (but temporarily) suspend the offending processes, before resuming after the hurdle has been jumped over. The tricky part is that I'll probably have to hardcode rules to keep system-critical processes safe, even if they're the ones bogging me down. 

I'm not trying to sound like a hardware expert, most of what I've just written comes from research I've done in the last hour. If playing uncharted 4 (2016 game btw) and watching youtube in tandem was freezing your PC every 5 minutes, I hope you'd learn this stuff too. 

Now I'll begin on my design, which I'll be doing with occam's razor, since I'm gonna have to figure out which libraries I need, and how to use them on a need-to-know basis. Also need to figure out to use Obsidian, which I am using. Right now. I was previously timestamping in notepad, but it messes with the formatting when I commit on Github, so I figured this might make my life a little easier. 
