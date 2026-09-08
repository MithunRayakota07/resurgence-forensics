import React, { useEffect, useMemo, useRef, useState } from 'react'
import DiskMap from './components/DiskMap.jsx'
import BeamGraph from './components/BeamGraph.jsx'

const asset = (p) => `/api/asset?path=${encodeURIComponent(p)}`

const TOOL_LABEL = {
  'foremost': 'Foremost',
  'scalpel': 'Scalpel',
  'photorec': 'PhotoRec (default)',
  'photorec+bruteforce': 'PhotoRec + brute force',
}

function verdict(v) {
  if (!v) return { cls: 'none', label: 'nothing recovered' }
  if (v.exact) return { cls: 'ok', label: 'BYTE-EXACT' }
  if (v.renders) return { cls: 'bad', label: `CORRUPT · ssim ${v.ssim.toFixed(2)}` }
  if (v.size) return { cls: 'bad', label: 'UNREADABLE' }
  return { cls: 'none', label: 'nothing recovered' }
}

export default function App() {
  const [kind, setKind] = useState('hard')
  const [report, setReport] = useState(null)
  const [manifest, setManifest] = useState(null)
  const [error, setError] = useState(null)

  // live carve state
  const [running, setRunning] = useState(false)
  const [step, setStep] = useState(null)
  const [livePath, setLivePath] = useState([])
  const [visiting, setVisiting] = useState(null)
  const [liveDone, setLiveDone] = useState(null)
  const [stats, setStats] = useState({ steps: 0, scored: 0, pruned: 0 })
  const bufRef = useRef([])
  const esRef = useRef(null)
  const timerRef = useRef(null)

  useEffect(() => {
    fetch('/api/report').then((r) => r.ok ? r.json() : Promise.reject(new Error('report not built')))
      .then(setReport).catch((e) => setError(String(e.message || e)))
  }, [])

  useEffect(() => {
    fetch(`/api/manifest/${kind}`).then((r) => r.json()).then(setManifest).catch(() => {})
    stop()
    setStep(null); setLivePath([]); setLiveDone(null); setVisiting(null)
    setStats({ steps: 0, scored: 0, pruned: 0 })
  }, [kind])

  const img = useMemo(
    () => report?.images?.find((i) => i.kind === kind) || null, [report, kind])

  function stop() {
    if (esRef.current) { esRef.current.close(); esRef.current = null }
    if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null }
    bufRef.current = []
    setRunning(false)
  }

  /*
    Events are buffered and replayed on a fixed tick rather than rendered as
    they arrive. The search emits steps in bursts, and a burst renders as a
    flicker that reads as noise -- an even cadence is what makes the
    exploration legible. The DATA is untouched; only the pacing is smoothed.
  */
  function run() {
    stop()
    setStep(null); setLivePath([]); setLiveDone(null); setVisiting(null)
    setStats({ steps: 0, scored: 0, pruned: 0 })
    setRunning(true)
    setError(null)

    const es = new EventSource(`/api/carve/stream?kind=${kind}&prior=locality&beam=24`)
    esRef.current = es
    es.onmessage = (m) => bufRef.current.push(JSON.parse(m.data))
    es.onerror = () => { if (bufRef.current.length === 0) { setError('stream failed — is the API running?'); stop() } }

    timerRef.current = setInterval(() => {
      const ev = bufRef.current.shift()
      if (!ev) return
      if (ev.event === 'step') {
        setStep(ev)
        const beam0 = ev.beam && ev.beam[0]
        if (ev.mode === 'run' && beam0) {
          setLivePath((p) => [...p, ...beam0.clusters])
        } else if (beam0 && beam0.clusters.length > 1) {
          setLivePath(beam0.clusters)
        }
        const best = (ev.expansions || []).find((e) => e.kept)
        setVisiting(best ? best.cand : null)
        setStats((s) => ({
          steps: s.steps + 1,
          scored: s.scored + (ev.expansions || []).length,
          pruned: s.pruned + (ev.expansions || []).filter((e) => !e.kept).length,
        }))
      } else if (ev.event === 'file_done') {
        setLivePath(ev.clusters); setLiveDone(ev); setVisiting(null)
      } else if (ev.event === 'done') {
        stop()
      } else if (ev.event === 'error') {
        setError(ev.message); stop()
      }
    }, 130)
  }

  const ours = img?.tools?.find((t) => t.tool.startsWith('ours')) || null
  const baselines = img?.tools?.filter((t) => !t.tool.startsWith('ours')) || []
  const clusterRange = useMemo(() => {
    if (!manifest) return null
    const all = manifest.files.flatMap((f) => f.cluster_order)
    return [Math.min(...all) - 12, Math.max(...all) + 12]
  }, [manifest])

  return (
    <div className="app">
      <header className="top">
        <div>
          <div className="brand">
            <h1>Resurgence</h1>
            <div className="sub">Fragment Reassembly Engine</div>
          </div>
          <div className="tagline">sequencing, not classification · phase 0 prototype</div>
        </div>
        <div className="controls">
          <div className="seg">
            <button className={kind === 'easy' ? 'on' : ''} onClick={() => setKind('easy')}>
              easy.img
            </button>
            <button className={kind === 'hard' ? 'on' : ''} onClick={() => setKind('hard')}>
              hard.img
            </button>
          </div>
          <button className="btn" onClick={running ? stop : run}>
            {running ? 'Stop' : 'Run carve'}
          </button>
        </div>
      </header>

      {error && <div className="card"><div className="err">error: {error}</div></div>}

      {/* ---------------------------------------------------- disk map --- */}
      <section className="card">
        <div className="card-h">
          <span className="eyebrow">01 / Evidence</span>
          <h2>Disk layout</h2>
          <span className="note">
            {manifest ? `${manifest.total_clusters} clusters × ${manifest.cluster_size} B` : ''}
          </span>
        </div>
        <p className="card-sub">
          {kind === 'easy'
            ? 'Two JPEGs interleaved on the platter. A header-to-footer carver reads straight from the first file’s SOI to the first EOI it meets — swallowing the other file’s data on the way.'
            : 'One JPEG in three pieces, laid down out of order. The second fragment sits at a LOWER cluster than the first, so recovering it requires searching backwards — which forward-scanning carvers never do.'}
        </p>
        <DiskMap manifest={manifest} visiting={visiting} path={livePath} />
      </section>

      {/* ------------------------------------------------- beam search --- */}
      <section className="card">
        <div className="card-h">
          <span className="eyebrow">02 / Reassembly</span>
          <h2>Beam search over the fragment graph</h2>
          <span className="note">live trace from the real carver — nothing pre-recorded</span>
        </div>
        <p className="card-sub">
          Each candidate cluster is appended to the partial file and the JPEG entropy stream is
          decoded onward from that exact bit position. Candidates that desync the Huffman decoder,
          or that reach the full MCU count without landing on EOI, are eliminated outright. The
          survivors are ranked by how well image structure continues across the seam.
        </p>
        <div className="beamwrap">
          <BeamGraph step={step} path={livePath} clusterRange={clusterRange} />
          <div className="cands">
            <table>
              <thead>
                <tr><th>cluster</th><th>gap</th><th>corr</th><th>MCUs</th><th>verdict</th></tr>
              </thead>
              <tbody>
                {(step?.expansions || []).slice(0, 40).map((e, i) => (
                  <tr key={i} className={e.kept ? 'kept' : 'rej'}>
                    <td className="c0">c{e.cand}</td>
                    <td>{e.gap > 0 ? `+${e.gap}` : e.gap}</td>
                    <td>{e.corr ?? '—'}</td>
                    <td>{e.mcus_gained}</td>
                    <td>{e.kept ? `keep Δ${e.delta}` : (e.reason || '').slice(0, 30)}</td>
                  </tr>
                ))}
                {!step && (
                  <tr><td colSpan="5" style={{ color: '#5A6070', padding: '18px 10px' }}>
                    idle — press Run carve
                  </td></tr>
                )}
              </tbody>
            </table>
          </div>
        </div>
        <div className="statline">
          <span>steps <b>{stats.steps}</b></span>
          <span>candidates scored <b>{stats.scored}</b></span>
          <span>pruned by validator <b style={{ color: '#FF4D6A' }}>{stats.pruned}</b></span>
          {liveDone && <span>fragments found <b>{liveDone.n_fragments}</b></span>}
          {liveDone && <span>MCUs <b>{liveDone.mcus}/{liveDone.total_mcus}</b></span>}
          {liveDone && (
            <span>confidence <b style={{ color: '#FFB020' }}>
              {liveDone.confidence} {liveDone.confidence_calibrated ? '' : '(uncalibrated)'}
            </b></span>
          )}
        </div>
      </section>

      {/* ----------------------------------------------------- results --- */}
      {img && img.truth.map((t) => (
        <section className="card" key={t.name}>
          <div className="card-h">
            <span className="eyebrow">03 / Recovery</span>
            <h2>{t.name}</h2>
            <span className="note">
              {t.size_bytes.toLocaleString()} B · {t.n_fragments} fragments · sha {t.sha256.slice(0, 16)}…
            </span>
          </div>
          <div className="grid4">
            {[
              ...baselines.map((b) => ({ tool: TOOL_LABEL[b.tool] || b.tool, v: b.per_file[t.name], ours: false })),
              { tool: 'Resurgence', v: ours?.per_file[t.name], ours: true },
            ].map(({ tool, v, ours: isOurs }) => {
              const vd = verdict(v)
              return (
                <div key={tool}
                     className={`shot ${isOurs ? 'ours' : ''} ${v?.exact ? 'exact' : (v?.size ? 'bad' : '')}`}>
                  <div className="imgbox">
                    {v?.file
                      ? <img src={asset(v.file)} alt={tool} />
                      : <div className="empty">no file<br />recovered</div>}
                  </div>
                  <div className="meta">
                    <div className="t">{tool}<span className={`pill ${vd.cls}`}>{vd.label}</span></div>
                    <div className="d">
                      {v?.size ? `${v.size.toLocaleString()} B` : '—'}
                      {isOurs && v?.n_fragments ? ` · ${v.n_fragments} frags` : ''}
                      {isOurs && v?.confidence != null ? ` · conf ${v.confidence}` : ''}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        </section>
      ))}

      {/* ------------------------------------------------------- table --- */}
      {img && (
        <section className="card">
          <div className="card-h">
            <span className="eyebrow">04 / Benchmark</span>
            <h2>Same disk image, same scoring</h2>
            <span className="note">produced by bench/report.py — the UI cannot invent a number</span>
          </div>
          <table className="cmp">
            <thead>
              <tr>
                <th>tool</th><th>byte-exact</th><th>time</th>
                {img.truth.map((t) => <th key={t.name}>{t.name}</th>)}
              </tr>
            </thead>
            <tbody>
              {img.tools.map((tl) => {
                const isOurs = tl.tool.startsWith('ours')
                return (
                  <tr key={tl.tool} className={isOurs ? 'us' : ''}>
                    <td className="tool">{isOurs ? 'Resurgence (beam + validator + prior)' : (TOOL_LABEL[tl.tool] || tl.tool)}</td>
                    <td style={{ color: tl.exact === img.truth.length ? '#00E5A0' : tl.exact ? '#FFB020' : '#FF4D6A' }}>
                      {tl.exact} / {img.truth.length}
                    </td>
                    <td>{tl.elapsed_s}s</td>
                    {img.truth.map((t) => {
                      const vd = verdict(tl.per_file[t.name])
                      return <td key={t.name}><span className={`pill ${vd.cls}`}>{vd.label}</span></td>
                    })}
                  </tr>
                )
              })}
            </tbody>
          </table>
          <div className="banner">
            <b>Read this honestly.</b> PhotoRec is run with its brute-force mode
            (<code>paranoid_bf</code>) switched on, which is built specifically for fragmented
            JPEGs. On easy.img it beats the naive carvers and recovers one file byte-exact.
            It fails on hard.img because its search only ever extends forwards from the header —
            and there the next fragment lies behind it.
          </div>
        </section>
      )}

      <div className="foot">
        <b>What is doing the work here:</b> a JPEG format validator and a constrained search.
        There is <b>no machine learning in this demo, and none is claimed for compressed formats</b> —
        we measured learned adjacency at 0.563 AUC on JPEG entropy-stream data, below a
        byte-histogram baseline. The learned model belongs on documents and spreadsheets
        (0.838 XLS, 0.799 DOC), which is a separate result and not shown on this screen.<br />
        Confidence scores are UNCALIBRATED — “0.99” does not yet mean “99% of such files are exact”.
        Allocation prior is hand-set, not fitted to real aged filesystems. SHA-256 comparison is
        confined to the evaluation harness and is never a runtime signal.
      </div>
    </div>
  )
}
