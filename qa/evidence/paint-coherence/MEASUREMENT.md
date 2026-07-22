# Paint-coverage coherence — 5 owner-room measurement (deterministic pass)

Method: geometry-truth walkable cells projected to plate pixels (contract dimetric rig at the room's
pinned cameraPin ortho); per-cell edge-dominant coverage score vs a robust majority-floor baseline.
VQA adjudication of the ambiguous band was wired + attempted but is blocked on LLM auth (401) in this
sandbox — the numbers below are the DETERMINISTIC verdicts; ambiguous cells await the batched VQA call.

| room | walkable | open | covered | ambiguous | walkable_covered | spawn (o/c/a) | arrival (o/c/a) |
|------|---------:|-----:|--------:|----------:|-----------------:|---------------|-----------------|
| crypt | 115 | 56 | 44 | 15 | 41 | 0open/2cov/1amb | 1open/1cov/0amb |
| tavern | 92 | 44 | 31 | 17 | 29 | 1open/2cov/0amb | 2open/0cov/0amb |
| shop | 65 | 37 | 13 | 15 | 10 | 0open/3cov/0amb | 2open/0cov/0amb |
| tavern_snug | 55 | 20 | 21 | 14 | 20 | 2open/1cov/0amb | 1open/0cov/1amb |
| throne_hall | 120 | 61 | 36 | 23 | 34 | 0open/2cov/1amb | 1open/0cov/1amb |

## Hard-gate findings (spawn / door-arrival not visually open)

- **crypt**: spawn covered=[[7, 7], [8, 7]] ambiguous=[[6, 7]]; arrival covered=[[15, 5]] ambiguous=[]
- **tavern**: spawn covered=[[6, 5], [7, 5]] ambiguous=[]; arrival covered=[] ambiguous=[]
- **shop**: spawn covered=[[5, 4], [6, 4], [7, 4]] ambiguous=[]; arrival covered=[] ambiguous=[]
- **tavern_snug**: spawn covered=[[5, 4]] ambiguous=[]; arrival covered=[] ambiguous=[[11, 4]]
- **throne_hall**: spawn covered=[[7, 6], [8, 6]] ambiguous=[[7, 5]]; arrival covered=[] ambiguous=[[8, 11]]
