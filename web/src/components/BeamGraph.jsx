import React from 'react'

/*
  The fragment graph: nodes are clusters laid out in physical order, arcs are
  candidate transitions the beam search actually evaluated.

  Every arc drawn here is a real scored extension from the search trace --
  rejected ones (the decoder desynced, or the scan hit EOI at the wrong MCU
  count) fade out in red, surviving ones stay violet, and the current best
  path is the solid line along the bottom. Nothing is choreographed; if the
  search changes its mind, the picture changes with it.
*/

export default function BeamGraph({ step, path, clusterRange }) {
  const W = 720, H = 340, base = H - 54

  if (!step) {
    return (
      <svg className="graph" viewBox={`0 0 ${W} ${H}`}>
        <text x={W / 2} y={H / 2} fontSize="13" fill="#5A6070" textAnchor="middle"
              fontFamily="monospace">
          run the carver to explore the fragment graph
        </text>
      </svg>
    )
  }

  const exps = step.expansions || []
  const froms = Array.isArray(step.from) ? step.from : [step.from]
  const pts = new Set([...froms, ...exps.map((e) => e.cand), ...(path || [])])
  let lo = clusterRange ? clusterRange[0] : Math.min(...pts)
  let hi = clusterRange ? clusterRange[1] : Math.max(...pts)
  if (hi - lo < 8) { hi = lo + 8 }
  const x = (c) => 34 + ((c - lo) / (hi - lo)) * (W - 68)

  const deltas = exps.filter((e) => e.kept).map((e) => e.delta)
  const best = deltas.length ? Math.max(...deltas) : 0
  const worst = deltas.length ? Math.min(...deltas) : -1

  return (
    <svg className="graph" viewBox={`0 0 ${W} ${H}`}>
      <defs>
        <linearGradient id="pathg" x1="0" x2="1">
          <stop offset="0%" stopColor="#7C5CFF" />
          <stop offset="100%" stopColor="#00E5A0" />
        </linearGradient>
      </defs>

      <text x={16} y={22} fontSize="10.5" fill="#7C5CFF" fontFamily="monospace"
            letterSpacing="1.4">
        STEP {step.step} · {step.mode === 'run' ? 'RUN MERGE' : 'BRANCH'} · {exps.length} CANDIDATES
      </text>

      {/* the committed path so far */}
      {(path || []).length > 1 && (
        <polyline
          points={(path || []).filter((c) => c >= lo && c <= hi)
            .map((c) => `${x(c)},${base + 26}`).join(' ')}
          fill="none" stroke="url(#pathg)" strokeWidth="2" opacity="0.85" />
      )}

      {/* candidate arcs */}
      {exps.map((e, i) => {
        const x0 = x(froms[0]), x1 = x(e.cand)
        if (!isFinite(x0) || !isFinite(x1)) return null
        const span = Math.abs(x1 - x0)
        const h = Math.min(190, 34 + span * 0.55)
        const mx = (x0 + x1) / 2
        const kept = e.kept
        const norm = kept && best !== worst ? (e.delta - worst) / (best - worst) : 0
        const isBest = kept && e.delta === best
        return (
          <path key={i}
            d={`M ${x0} ${base} Q ${mx} ${base - h} ${x1} ${base}`}
            fill="none"
            stroke={kept ? (isBest ? '#00E5A0' : '#7C5CFF') : '#FF4D6A'}
            strokeWidth={isBest ? 2.4 : kept ? 0.6 + norm * 1.3 : 0.5}
            opacity={kept ? (isBest ? 0.95 : 0.22 + norm * 0.5) : 0.16}
            strokeDasharray={kept ? undefined : '2 3'} />
        )
      })}

      {/* cluster ticks */}
      {[...pts].filter((c) => c >= lo && c <= hi).map((c) => {
        const inPath = (path || []).includes(c)
        const isFrom = froms.includes(c)
        return (
          <g key={c}>
            <circle cx={x(c)} cy={base} r={isFrom ? 4 : inPath ? 3 : 2}
                    fill={isFrom ? '#FFB020' : inPath ? '#00E5A0' : '#3A4152'} />
            {(isFrom || inPath) && (
              <text x={x(c)} y={base + 15} fontSize="7.5" fill="#5A6070"
                    textAnchor="middle" fontFamily="monospace">{c}</text>
            )}
          </g>
        )
      })}

      <line x1={20} y1={base} x2={W - 20} y2={base} stroke="#232733" strokeWidth="1" />
      <text x={16} y={H - 10} fontSize="9" fill="#5A6070" fontFamily="monospace">
        green = best surviving extension · violet = kept · dashed red = pruned by format validator
      </text>
    </svg>
  )
}
