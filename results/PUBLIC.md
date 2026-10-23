# About the published results

These files are stripped copies of the private run data. Kept: run ids, engine, model,
question id, cost, token counts, latency, call counts, cited URLs and domains, judge
scores, and the deterministic scores (`primary_hit`, `source_recall`, `answer_recall`),
precomputed because they need the excerpts. Removed: model answers, citation excerpts
(third-party page text), judge notes, raw HTTP logs and request payloads.

`eval-report` needs the full private data, so it can't regenerate reports from these
files. Each folder's `report.md` is the report as generated from the private data.
