// Painterly-DETAIL adversarial critic — the eval upgrade after the visual-critic MISSED a washed-out/watercolor plate
// (owner 2026-06-27: "it's not just paint — it has to be DETAILED, fine grain, crisp brushwork, NOT washed-out watercolor").
// Scores candidate renders ONLY on detail/graining/brushwork/stonework vs the PoE/BG/Disco reference frames, harshly.
// Run: Workflow({scriptPath:".../qa/painterly_detail_critic.js", args:{candidates:[{label,path}...], refs:[path...]}})
export const meta = {
  name: 'painterly-detail-critic',
  description: 'Adversarial DETAIL-vs-watercolor critic: scores each candidate render on fine detail / grain / crisp brushwork / intricate stonework vs the PoE-BG-Disco refs; flags washed-out watercolor',
  phases: [{ title: 'Detail', detail: 'each candidate x3 harsh detail-lens agents, ref-anchored' }],
}

let _A = args; if (typeof _A === 'string') { try { _A = JSON.parse(_A) } catch (e) { _A = {} } }
const candidates = (_A && _A.candidates) || []
const refs = (_A && _A.refs) || []
if (!candidates.length) { log('no candidates in args: ' + JSON.stringify(args).slice(0, 200)) }

const SCHEMA = {
  type: 'object', additionalProperties: false,
  required: ['label', 'detail_score', 'is_watercolor_washout', 'brushwork', 'grain_texture', 'stonework_prop_detail', 'vs_ref_gap', 'tells'],
  properties: {
    label: { type: 'string' },
    detail_score: { type: 'number', description: '0-10 fidelity of FINE DETAIL vs the refs: 9-10 = PoE/BG-caliber intricate detail; 5-6 = reads as a game but soft; 3-4 = detail breaking down; 0-2 = washed-out watercolor mush' },
    is_watercolor_washout: { type: 'boolean', description: 'true if it reads as soft/washed/muddy watercolor lacking the refs fine detail (the failure the owner flagged)' },
    brushwork: { type: 'string', description: 'crisp confident strokes vs soft/smudged' },
    grain_texture: { type: 'string', description: 'fine grain / surface texture present, or flat/hazy' },
    stonework_prop_detail: { type: 'string', description: 'are walls/floor/chest/sarcophagus/pillars intricately detailed + legible, or undefined blobs' },
    vs_ref_gap: { type: 'string', description: 'which ref, what detail it has that the candidate lacks' },
    tells: { type: 'array', items: { type: 'string' }, description: 'specific washout/detail tells + a fix' },
  },
}

const PRE = `You are a HARSH painterly-DETAIL specialist on a WorldOS visual-critic panel. The team previously shipped a camera-pinned dungeon plate that scored "fine" overall but the OWNER rejected it as "mushy watercolor — lost the actual detail; the chest and walls went weird and washed-out." Your ONE job: judge FINE DETAIL, GRAIN, CRISP BRUSHWORK, and INTRICATE STONEWORK/PROP DEFINITION — NOT composition, lighting, or registration (sibling lenses own those). Real painterly (the refs) is HIGHLY DETAILED: crisp confident brushstrokes, fine surface grain, intricate carved stone, legible props — NOT soft/washed/muddy/hazy watercolor. Grade 2-3 points HARSHER than a generalist; washout MUST score low and set is_watercolor_washout=true.

FIRST use the Read tool to view the CANDIDATE then the REFERENCE frames (the bar = real PoE2/BG2/Disco detail). Score the candidate's DETAIL gap to the refs. Be specific and unflattering. TEXT-ONLY JSON.`

phase('Detail')
const tasks = []
for (const c of candidates) for (let run = 1; run <= 3; run++) tasks.push({ c, run })
const results = (await parallel(tasks.map((t) => () =>
  agent(`${PRE}\n\nCANDIDATE (under test): ${t.c.path}\nREFERENCES (the detail bar): ${refs.join(' , ')}\n\nScore ONLY detail/grain/brushwork/stonework for "${t.c.label}". Return JSON.`,
    { label: `detail:${t.c.label}#${t.run}`, phase: 'Detail', schema: SCHEMA, effort: 'medium' })
    .then((r) => (r ? { ...r, _label: t.c.label } : null))
))).filter(Boolean)

// aggregate per candidate
const byLabel = {}
for (const r of results) { (byLabel[r._label] = byLabel[r._label] || []).push(r) }
const summary = Object.entries(byLabel).map(([label, rs]) => {
  const avg = rs.reduce((s, r) => s + (r.detail_score || 0), 0) / rs.length
  const washVotes = rs.filter((r) => r.is_watercolor_washout).length
  return { label, avg_detail: Math.round(avg * 10) / 10, washout_votes: `${washVotes}/${rs.length}`, sample_tells: (rs[0].tells || []).slice(0, 3) }
}).sort((a, b) => b.avg_detail - a.avg_detail)
return { ranking: summary, winner: summary[0] || null, all: results }
