"""
FastAPI backend for the Phase 0 demo.

Two rules this file exists to enforce:

  1. Nothing is mocked. /api/carve/stream runs the real beam search on the
     real disk image and streams the real trace. If the carver breaks, the
     screen breaks. A demo that cannot fail is not evidence of anything.

  2. The UI never invents numbers. Every figure it shows comes from
     bench/out/report.json, which is produced by the scoring harness.
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from carve.beam import BeamCarver
from carve.blocks import ClusterView
from carve.carver import confidence
from carve.priors.base import get_prior

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGES = os.path.join(ROOT, "corpus", "images")
OUT = os.path.join(ROOT, "bench", "out")

app = FastAPI(title="Fragment carving demo")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5183", "http://127.0.0.1:5183"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/report")
def report():
    p = os.path.join(OUT, "report.json")
    if not os.path.exists(p):
        raise HTTPException(404, "report.json not built yet -- run bench/report.py")
    with open(p) as f:
        return json.load(f)


@app.get("/api/manifest/{kind}")
def manifest(kind: str):
    p = os.path.join(IMAGES, "%s.manifest.json" % kind)
    if not os.path.exists(p):
        raise HTTPException(404, "no manifest for %r" % kind)
    with open(p) as f:
        return json.load(f)


@app.get("/api/asset")
def asset(path: str = Query(...)):
    """Serve a file from the repo, restricted to the two output trees."""
    full = os.path.normpath(os.path.join(ROOT, path))
    allowed = (os.path.join(ROOT, "corpus", "images"), os.path.join(ROOT, "bench", "out"))
    if not any(full.startswith(a) for a in allowed) or not os.path.isfile(full):
        raise HTTPException(404, "not found")
    # The results panel re-renders on every trace tick during a live carve.
    # Without this the browser refetches each recovered image ~8x a second and
    # the side-by-side flickers exactly when someone is looking at it.
    return FileResponse(full, headers={"Cache-Control": "public, max-age=300"})


def _sse(obj) -> str:
    return "data: %s\n\n" % json.dumps(obj)


@app.get("/api/carve/stream")
def carve_stream(kind: str = "easy", prior: str = "locality", beam: int = 24):
    """
    Run the real carver and stream beam-search events as they happen.

    The trace is the same structure the Phase 1 fragment-graph visualisation
    will consume, so the animation is wired to real search output from night
    one and never to a mock.
    """
    image = os.path.join(IMAGES, "%s.img" % kind)
    if not os.path.exists(image):
        raise HTTPException(404, "no image %r" % kind)

    q: "queue.Queue" = queue.Queue()

    def worker():
        try:
            view = ClusterView(image)
            pr = get_prior(prior)
            headers = view.find_jpeg_headers()
            q.put({"event": "scan", "clusters": len(view), "headers": headers,
                   "prior": pr.name, "prior_is_measured": pr.is_measured,
                   "cluster_size": view.cluster_size})
            for idx, hc in enumerate(headers):
                q.put({"event": "file_start", "index": idx, "header_cluster": hc})
                carver = BeamCarver(
                    view, pr, beam_width=beam, collect_trace=True,
                    on_step=lambda e, i=idx: q.put({"event": "step", "index": i, **e}),
                )
                r = carver.carve(hc)
                q.put({"event": "file_done", "index": idx, "ok": r.ok,
                       "clusters": r.clusters, "n_fragments": r.n_fragments,
                       "mcus": r.mcus, "total_mcus": r.total_mcus,
                       "confidence": confidence(r),
                       "confidence_calibrated": False,
                       "elapsed_s": round(r.elapsed_s, 2),
                       "candidates_scored": r.candidates_scored,
                       "reason": r.reason})
            q.put({"event": "done"})
        except Exception as e:  # surface failures instead of hanging the UI
            q.put({"event": "error", "message": "%s: %s" % (type(e).__name__, e)})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()

    def gen():
        while True:
            item = q.get()
            if item is None:
                break
            yield _sse(item)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
