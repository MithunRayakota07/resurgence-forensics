import React from 'react'

/*
  The disk layout strip.

  This single picture is what makes fragmentation obvious to someone who has
  never heard the word: each tick is one 4 KiB cluster in physical order, and
  the coloured runs are one file scattered across the platter with unrelated
  data sitting between the pieces.
*/

const COLORS = ['#7C5CFF', '#38BDF8', '#FFB020', '#00E5A0']

export default function DiskMap({ manifest, visiting, path, focus }) {
  if (!manifest) return null

  const owner = new Map()   // cluster -> {file index, fragment index, position}
  manifest.files.forEach((f, fi) => {
    f.fragments.forEach((fr) => {
      for (let i = 0; i < fr.n_clusters; i++) {
        owner.set(fr.start_cluster + i, { fi, frag: fr.fragment })
      }
    })
  })

  // Zoom to the region that actually contains evidence, plus margin -- the
  // full 4096-cluster image is mostly empty and shows nothing at this width.
  const used = [...owner.keys()]
  const lo = Math.max(0, Math.min(...used) - 14)
  const hi = Math.min(manifest.total_clusters - 1, Math.max(...used) + 14)
  const n = hi - lo + 1

  const W = 1180, H = 74, top = 16
  const cw = W / n
  const pathSet = new Set(path || [])
  const order = new Map((path || []).map((c, i) => [c, i]))

  return (
    <div>
      <svg className="diskmap" viewBox={`0 0 ${W} ${H + 30}`} preserveAspectRatio="none">
        {Array.from({ length: n }, (_, k) => {
          const c = lo + k
          const o = owner.get(c)
          const fill = o ? COLORS[o.fi % COLORS.length] : '#1B1F29'
          const isVisiting = visiting === c
          const inPath = pathSet.has(c)
          return (
            <g key={c}>
              <rect
                x={k * cw + 0.4} y={top} width={Math.max(1, cw - 0.8)} height={38}
                fill={fill} opacity={o ? (inPath ? 1 : 0.55) : 0.5} rx={1.5}
              />
              {inPath && (
                <rect x={k * cw + 0.4} y={top + 40} width={Math.max(1, cw - 0.8)} height={4}
                      fill="#00E5A0" rx={1} />
              )}
              {isVisiting && (
                <rect x={k * cw - 0.6} y={top - 5} width={Math.max(2, cw + 1.2)} height={48}
                      fill="none" stroke="#FFB020" strokeWidth="1.6" rx={2}>
                  <animate attributeName="opacity" values="1;0.25;1" dur="0.8s" repeatCount="indefinite" />
                </rect>
              )}
              {inPath && cw > 9 && (
                <text x={k * cw + cw / 2} y={top + 56} fontSize="7.5" fill="#00E5A0"
                      textAnchor="middle" fontFamily="monospace">{order.get(c) + 1}</text>
              )}
            </g>
          )
        })}
        {[0, 0.25, 0.5, 0.75, 1].map((f) => {
          const c = Math.round(lo + f * (n - 1))
          return (
            <text key={f} x={Math.min(W - 26, Math.max(14, (c - lo) * cw))} y={11}
                  fontSize="9" fill="#5A6070" fontFamily="monospace"
                  textAnchor={f === 0 ? 'start' : f === 1 ? 'end' : 'middle'}>
              c{c}
            </text>
          )
        })}
      </svg>

      <div className="legend">
        {manifest.files.map((f, i) => (
          <span key={f.name}>
            <i className="swatch" style={{ background: COLORS[i % COLORS.length] }} />
            {f.name} — {f.n_fragments} fragment{f.n_fragments > 1 ? 's' : ''}
          </span>
        ))}
        <span><i className="swatch" style={{ background: '#1B1F29' }} />unallocated / other data</span>
        <span><i className="swatch" style={{ background: '#00E5A0' }} />recovered order</span>
        <span><i className="swatch" style={{ background: '#FFB020' }} />candidate under test</span>
      </div>
    </div>
  )
}
