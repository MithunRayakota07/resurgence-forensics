"""
Run the real baseline carvers against the same disk images we use.

PhotoRec, Foremost and Scalpel are invoked as the actual installed binaries
via WSL. We do not reimplement them: a reimplemented baseline is a strawman,
and the entire value of this table depends on it being honest.

PhotoRec is run TWICE -- default, and with brute-force mode (paranoid_bf)
enabled. Its brute-force mode is specifically built for fragmented JPEGs and
uses libjpeg to detect which block is foreign. Benchmarking against PhotoRec
with that switch off would be quietly rigging the comparison, and anyone who
knows the tool would catch it in one command.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCALPEL_CONF = "jpg y 20000000 \\xff\\xd8\\xff \\xff\\xd9\n"


def win_to_wsl(p: str) -> str:
    p = os.path.abspath(p).replace("\\", "/")
    if len(p) > 1 and p[1] == ":":
        return "/mnt/" + p[0].lower() + p[2:]
    return p


def wsl(cmd: str, timeout: int = 900):
    t0 = time.time()
    try:
        r = subprocess.run(["wsl.exe", "-d", "Ubuntu", "-u", "root", "--", "bash", "-lc", cmd],
                           capture_output=True, timeout=timeout)
        out = r.stdout.decode("utf-8", "replace").replace("\x00", "")
        err = r.stderr.decode("utf-8", "replace").replace("\x00", "")
        return r.returncode, out, err, time.time() - t0
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT after %ds" % timeout, time.time() - t0


def collect(out_dir: str, exts=(".jpg", ".jpeg")):
    found = []
    for root, _dirs, files in os.walk(out_dir):
        for fn in files:
            if fn.lower().endswith(exts):
                p = os.path.join(root, fn)
                if os.path.getsize(p) > 0:
                    found.append(p)
    return sorted(found)


def run_photorec(image: str, out_dir: str, brute: bool):
    os.makedirs(out_dir, exist_ok=True)
    w_img, w_out = win_to_wsl(image), win_to_wsl(out_dir)
    opts = "options,paranoid_bf," if brute else ""
    cmd = ("cd %s && photorec /log /d %s/recup /cmd %s "
           "partition_none,%sfileopt,everything,disable,jpg,enable,search"
           % (w_out, w_out, w_img, opts))
    rc, out, err, el = wsl(cmd)
    return {"tool": "photorec" + ("+bruteforce" if brute else ""),
            "rc": rc, "elapsed_s": round(el, 2), "files": collect(out_dir),
            "stderr": err[-400:], "cmd": cmd}


def run_foremost(image: str, out_dir: str):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    w_img, w_out = win_to_wsl(image), win_to_wsl(out_dir)
    cmd = "foremost -t jpg -i %s -o %s" % (w_img, w_out)
    rc, out, err, el = wsl(cmd)
    return {"tool": "foremost", "rc": rc, "elapsed_s": round(el, 2),
            "files": collect(out_dir), "stderr": err[-400:], "cmd": cmd}


def run_scalpel(image: str, out_dir: str, conf_path: str):
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir, ignore_errors=True)
    with open(conf_path, "w", newline="\n") as f:
        f.write(SCALPEL_CONF)
    w_img, w_out, w_conf = win_to_wsl(image), win_to_wsl(out_dir), win_to_wsl(conf_path)
    cmd = "scalpel -c %s -o %s %s" % (w_conf, w_out, w_img)
    rc, out, err, el = wsl(cmd)
    return {"tool": "scalpel", "rc": rc, "elapsed_s": round(el, 2),
            "files": collect(out_dir), "stderr": err[-400:], "cmd": cmd}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("image")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    base = args.out or os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "out", "baselines",
                                    os.path.basename(args.image).replace(".img", ""))
    os.makedirs(base, exist_ok=True)

    runs = []
    print("running baselines on %s" % args.image)
    for fn, name in ((lambda: run_foremost(args.image, os.path.join(base, "foremost")), "foremost"),
                     (lambda: run_scalpel(args.image, os.path.join(base, "scalpel"),
                                          os.path.join(base, "scalpel.conf")), "scalpel"),
                     (lambda: run_photorec(args.image, os.path.join(base, "photorec"), False),
                      "photorec"),
                     (lambda: run_photorec(args.image, os.path.join(base, "photorec_bf"), True),
                      "photorec+bruteforce")):
        r = fn()
        runs.append(r)
        print("  %-22s rc=%-4s %5.1fs  %d file(s) recovered"
              % (r["tool"], r["rc"], r["elapsed_s"], len(r["files"])))
        if r["rc"] != 0 and r["stderr"]:
            print("      stderr: %s" % r["stderr"].strip()[:200])

    res = {"image": os.path.basename(args.image), "runs": runs}
    if args.json:
        with open(args.json, "w") as f:
            json.dump(res, f, indent=2)
        print("json: %s" % args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
