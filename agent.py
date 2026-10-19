"""Print one JSON snapshot of this machine's load. Run by server.py over SSH (forced command)."""
import glob, json, os, platform, shutil, subprocess, sys, time
import psutil


def run(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=15).stdout
    except Exception:
        return ""


def gpus():
    smi = shutil.which("nvidia-smi") or ("/usr/lib/wsl/lib/nvidia-smi" if os.path.exists("/usr/lib/wsl/lib/nvidia-smi") else None)
    if not smi:
        return []
    out = []
    q = "name,utilization.gpu,memory.used,memory.total,temperature.gpu,power.draw"
    for line in run([smi, f"--query-gpu={q}", "--format=csv,noheader,nounits"]).splitlines():
        f = [x.strip() for x in line.split(",")]
        num = lambda s: float(s) if s.replace(".", "", 1).isdigit() else None
        out.append(dict(name=f[0], util=num(f[1]), mem_used=num(f[2]), mem_total=num(f[3]),
                        temp=num(f[4]), power=num(f[5]), procs=[]))
    for line in run([smi, "--query-compute-apps=pid,used_memory",
                     "--format=csv,noheader,nounits"]).splitlines():
        pid, mem = [x.strip() for x in line.split(",")]
        try:
            p = psutil.Process(int(pid))
            name, user = p.name(), (p.username() or "").split("\\")[-1]
        except Exception:
            name, user = "?", "?"
        if out:  # ponytail: assumes one GPU per machine, true for all four
            out[0]["procs"].append(dict(pid=int(pid), name=name, user=user,
                                        mem=float(mem) if mem.isdigit() else None))
    return out


def disks():
    seen, out = set(), []
    for d in psutil.disk_partitions(all=False):
        mp = d.mountpoint
        if sys.platform == "darwin" and mp != "/System/Volumes/Data" and not mp.startswith("/Volumes/"):
            continue
        if mp.startswith(("/boot", "/snap", "/var/snap")) or d.fstype in ("squashfs", "vfat") or d.device in seen:
            continue
        try:
            u = psutil.disk_usage(mp)
        except Exception:
            continue
        if u.total < 5e9 or (u.total, u.free) in seen:  # macOS lists the Data volume twice
            continue
        seen.update([d.device, (u.total, u.free)])
        out.append(dict(mount="/" if mp == "/System/Volumes/Data" else mp, total=u.total, free=u.free))
    return out


def idle_minutes(t):
    """quser's IDLE TIME as minutes: none or . (input just now), 5, 1:05, 2+03:10."""
    if t in ("none", "."):
        return 0
    days, _, hm = t.rpartition("+")
    h, _, m = hm.rpartition(":")
    return int(days or 0) * 1440 + int(h or 0) * 60 + int(m)


def desktop(procs):
    """Windows: minutes since the last keyboard or mouse input in an active desktop session (console or remote; None
    when none is active) and the names of the running programs, so jobs elsewhere can make way (wsl_yield.py). The
    idle time comes from desktop_idle.pyw in the desktop session when it is running; quser's shows the console session
    as never idle, so it is only a fallback that errs on "in use"."""
    if os.name != "nt":
        return None
    apps = sorted({p.info["name"] for p in procs if p.info["name"]})
    d = {}
    for _ in range(10):  # the helper replaces the file every 20 s, and Windows refuses a read just then
        try:
            with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop_idle.json")) as f:
                d = json.load(f)
            break
        except PermissionError:
            time.sleep(0.2)
        except (OSError, ValueError):
            break
    try:
        if time.time() - d["t"] < 120:
            return dict(idle_min=int(d["idle_s"] // 60), active=d.get("active"), slots=d.get("slots"), apps=apps,
                        source="desktop_idle")
    except (OSError, ValueError, KeyError):
        pass
    idle = []
    for line in run(["quser"]).splitlines()[1:]:
        parts = line.split()
        if "Active" in parts[:-1]:
            try:
                idle.append(idle_minutes(parts[parts.index("Active") + 1]))
            except ValueError:
                pass
    return dict(idle_min=min(idle) if idle else None, apps=apps, source="quser")


def snapshot():
    procs = list(psutil.process_iter(["name", "username", "memory_info"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    cpu = psutil.cpu_percent(interval=1)  # also the sampling window for per-process CPU
    top = []
    for p in procs:
        if p.pid == 0:
            continue
        try:
            c = p.cpu_percent(None)
        except Exception:
            continue
        mi = p.info["memory_info"]
        top.append(dict(pid=p.pid, name=p.info["name"], user=(p.info["username"] or "").split("\\")[-1],
                        cpu=round(c, 1), rss=mi.rss if mi else 0))
    top.sort(key=lambda x: (x["cpu"], x["rss"]), reverse=True)
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    tmux_bin = shutil.which("tmux") or next(iter(glob.glob(os.path.expanduser("~/micromamba/envs/*/bin/tmux"))), None)
    tmux = run([tmux_bin, "ls", "-F", "#{session_name}"]).split() if tmux_bin else []
    return dict(
        ok=True, time=time.time(), os=f"{platform.system()} {platform.release()}",
        uptime=time.time() - psutil.boot_time(),
        cores=psutil.cpu_count(), cpu=cpu,
        load=None if os.name == "nt" else [round(x, 2) for x in psutil.getloadavg()],
        mem_total=vm.total, mem_avail=vm.available, swap_total=sw.total, swap_used=sw.used,
        disks=disks(), gpus=gpus(), top=top[:8],
        top_mem=sorted(top, key=lambda x: x["rss"], reverse=True)[:3],
        users=sorted({u.name.split("\\")[-1] for u in psutil.users()}
                     | {p["user"] for p in top if os.name == "nt" and p["name"] == "explorer.exe"}),
        tmux=tmux, reboot_required=os.path.exists("/var/run/reboot-required"), desktop=desktop(procs),
    )


if __name__ == "__main__":
    assert [idle_minutes(t) for t in ("none", ".", "5", "1:05", "2+03:10")] == [0, 0, 5, 65, 3070]
    print(json.dumps(snapshot()))
