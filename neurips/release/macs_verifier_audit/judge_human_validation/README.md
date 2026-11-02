# Human rating of the multi-turn judge

40 stored multi-turn transcripts (one run per condition × scenario cell; run drawn
with seed 20261101) rated on the judge's rubric, blind to system identity, judge
score and deterministic verdict. `packet.md` is the clean packet the raters received;
`key.json` maps items to conditions and stored scores.

| File | What it is |
|---|---|
| `sheet_JH.csv` | Final ratings by one author (JH), transcribed from the author's annotated packet |
| `sheet_JH_draft_1631.csv` | An earlier saved draft of the same author's ratings (14 items differ); same conclusions (rho 0.688 vs 0.689, same ordering, 29/40 pass agreement) |
| `gptsheet.csv` | Ratings by GPT-6 in the ChatGPT interface, given only `packet.md`; settings not controlled. An LLM, not a human |
| `sheet_GPT.csv` | `gptsheet.csv` with column names converted for the scripts; values unchanged |

```
python3 build_sheet.py ..                         # rebuilds packet.md, sheet.csv, key.json
python3 analyze.py sheet_JH.csv                   # pre-specified analysis -> human_judge_agreement.json
python3 exploratory.py sheet_JH.csv sheet_GPT.csv # exploratory follow-ups -> exploratory.json
```

The analysis plan and the interpretation rule in `analyze.py` were written before any
ratings were collected. `exploratory.py` was written after the ratings were seen. The
author rater knew the study's hypothesis; stylistic differences between systems were
not masked.
