#!/usr/bin/env bash
# Phase 0, from a clean checkout. Regenerates everything; nothing is cached.
set -e
python corpus/generate/synthetic.py                       # build easy.img + hard.img
python bench/test_decoder.py                              # validate the JPEG decoder first
for k in easy hard; do
  python carve/carver.py corpus/images/$k.img \
      --out bench/out/ours_$k --json bench/out/ours_$k.json --no-trace
  python bench/run_baselines.py corpus/images/$k.img \
      --json bench/out/baselines_$k.json                  # needs WSL Ubuntu
done
python bench/report.py                                    # the comparison table
echo
echo "now:  python -m uvicorn api.main:app --port 8781"
echo "and:  npm run dev --prefix web        # http://localhost:5183"
