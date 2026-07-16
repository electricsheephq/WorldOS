export const meta = {
  name: 'blind-beauty-panels',
  description: 'Blind 5-scorer control-anchored beauty panels (THE versioned ruler — see qa/PANEL-PROTOCOL.md)',
  phases: [{ title: 'Score' }, { title: 'Aggregate' }],
}
// args: { rooms: [{id, plate}], control: <path> }  — paths are absolute
const CONTROL = args.control
const SCHEMA = {
  type: 'object',
  properties: {
    score_a: { type: 'number' }, score_b: { type: 'number' },
    notes: { type: 'string' },
  },
  required: ['score_a', 'score_b'],
}
const results = await pipeline(
  args.rooms,
  room => parallel([0, 1, 2, 3, 4].map(i => () => {
    // Alternate A/B order per scorer so position bias cancels (the panel protocol).
    const candFirst = i % 2 === 0
    const first = candFirst ? room.plate : CONTROL
    const second = candFirst ? CONTROL : room.plate
    return agent(
      `You are scoring two isometric fantasy-RPG background paintings for hand-painted concept-art ` +
      `quality (composition, painterly texture, lighting/chiaroscuro, material read, atmosphere). ` +
      `Read image A at ${first} and image B at ${second} with the Read tool. Score EACH on 0-10 ` +
      `(pre-rendered PoE2/BG3-class = 8-10; clean but generic = 5-7; flat/AI-artifacted = 0-4). ` +
      `They are unrelated images from different projects — judge each on its own merits. ` +
      `FORMAT RULES: return ONLY the structured output; score_a = image A, score_b = image B.`,
      { label: `score:${room.id}:s${i}`, phase: 'Score', schema: SCHEMA, model: 'sonnet', effort: 'low' }
    ).then(v => v && ({ cand: candFirst ? v.score_a : v.score_b, ctrl: candFirst ? v.score_b : v.score_a, notes: v.notes }))
  })).then(votes => {
    const vs = votes.filter(Boolean)
    const med = a => { const s = [...a].sort((x, y) => x - y); return s[Math.floor(s.length / 2)] }
    return {
      id: room.id,
      n: vs.length,
      cand_median: med(vs.map(v => v.cand)),
      ctrl_median: med(vs.map(v => v.ctrl)),
      delta: med(vs.map(v => v.cand)) - med(vs.map(v => v.ctrl)),
      notes: vs.map(v => v.notes).filter(Boolean).slice(0, 5),
    }
  })
)
log(`panels done: ${results.filter(Boolean).map(r => `${r.id} Δ${r.delta}`).join(' · ')}`)
return { results: results.filter(Boolean), ship_bar: 'delta >= -2.0 vs control (control-anchored band)' }
