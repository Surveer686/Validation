# Phase Y scorecard — QA against AI_in_Healthcare_Report.pdf

- Questions: 34 (ground_truth.csv, answers extracted from the PDF only)
- Overall accuracy: **30/34 = 88.2%**
- Verdict (thresholds: >=90 PASS, 75-89 PASS_WITH_NOTES, <75 FAIL): **PASS_WITH_NOTES**
- Completeness probes: **3/5**
- Ontology under test: `ontology_final.ttl`

## Accuracy by category

| Category | Correct | Of | % |
|---|---|---|---|
| CASE_REC | 4 | 4 | 100.0 |
| COMPLETENESS | 3 | 5 | 60.0 |
| DEFINITION | 2 | 4 | 50.0 |
| LIST | 4 | 4 | 100.0 |
| RELATION | 7 | 7 | 100.0 |
| VALUE | 10 | 10 | 100.0 |

## Misses (each is a finding, not a matcher bug)

| ID | Category | Question | Expected | Predicted / classification | Doc |
|---|---|---|---|---|---|
| D03 | DEFINITION | What is explainability? | The degree to which a model's output can be interpreted and justified  | defined as: Formal Authorization (E.G., Fda Clearance/Approv [WRONG_DEFINITION] | §2 Definitions p.3 |
| D04 | DEFINITION | What is regulatory clearance? | Formal authorization (e.g., FDA clearance/approval) permitting clinica | subject has no defines edge [NO_DEFINITION_EDGE] | §2 Definitions p.3 |
| C01 | COMPLETENESS | Which Case Study A success factor refers to a stepwise rollout strategy? | Phased Rollout |  [MISSING_FROM_ONTOLOGY] | §11 Case Study A p.13 |
| C03 | COMPLETENESS | What is the glossary definition of model drift? | A decline in model performance over time as real-world data patterns d | defined as: Model Drift Definition [WRONG_DEFINITION] | §B Glossary p.19 |

## Classification legend

- `MISSING_FROM_ONTOLOGY` — no node (or near node) for the expected fact.
- `PRESENT_LABEL_DIFFERS` — a closely matching node exists under a different label.
- `NOT_LINKED` — expected nodes exist but the required edge is absent.
- `SUBJECT_MISSING` — the question's subject entity is absent.
- `NO_DEFINITION_EDGE` — term exists but has no defines edge.
- `WRONG_DEFINITION` — defines edge points at the wrong/placeholder text.

## Method

Deterministic: rdflib label lookup + direct-edge checks + defines-text comparison.
Matching normalizes case, unicode dashes/quotes, whitespace, trailing periods;
short tokens (<4 chars) require exact match, longer allow mutual containment.
Expected answers are never altered to fit the graph.

## Known limitations

- Chart images in the PDF have no extractable text; questions come from tables/prose only.
- 34 questions sample the report; they do not cover every statement.
- Accuracy measures factual coverage of extracted ontology content, not OWL reasoning.

