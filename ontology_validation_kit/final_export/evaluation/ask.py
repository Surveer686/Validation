#!/usr/bin/env python3
"""Phase Y QA harness: answer ground_truth.csv questions against ontology_final.ttl.

Expected answers come ONLY from ground_truth.csv (built from the source PDF).
This script never modifies expected answers; misses are reported as findings.

Usage:
    python ask.py

Outputs:
    answers.csv    - one row per question: predicted answer, match, classification
    scorecard.md   - accuracy by category, misses, completeness, verdict
"""
import csv
import re
import sys
import unicodedata
from pathlib import Path

from rdflib import RDF, RDFS, Graph, URIRef

HERE = Path(__file__).resolve().parent
TTL_PATH = HERE.parent / "ontology_final.ttl"
GT_PATH = HERE / "ground_truth.csv"
ANSWERS_PATH = HERE / "answers.csv"
SCORECARD_PATH = HERE / "scorecard.md"

BASE = "https://example.org/healthcare-ontology/"
META_LOCAL = {"type", "entityType", "inDomain"}

VERDICT_THRESHOLDS = [(90, "PASS"), (75, "PASS_WITH_NOTES"), (0, "FAIL")]


def norm(s):
    s = unicodedata.normalize("NFKC", str(s))
    for a, b in [("\u2013", "-"), ("\u2014", "-"), ("\u2018", "'"), ("\u2019", "'"),
                 ("\u201c", '"'), ("\u201d", '"'), ("\xa0", " ")]:
        s = s.replace(a, b)
    s = s.lower()
    s = re.sub(r"\s+", " ", s).strip().rstrip(".").strip()
    return s


def local(pred):
    return str(pred).split("/")[-1].split("#")[-1]


def tokens(s):
    return [t for t in re.findall(r"[a-z0-9]+", norm(s)) if len(t) > 3]


class Ontology:
    def __init__(self, ttl_path):
        self.g = Graph().parse(str(ttl_path), format="turtle")
        self.by_label = {}
        for node in self.g.subjects(RDFS.label, None):
            for lab in self.g.objects(node, RDFS.label):
                self.by_label.setdefault(norm(lab), set()).add(node)

    def candidates(self, texts):
        """All node URIs matching any expected text/alias (exact, then containment)."""
        found, seen = [], set()
        for text in [t for t in texts if t]:
            key = norm(text)
            for uri in self.by_label.get(key, ()):
                if uri not in seen:
                    seen.add(uri)
                    found.append((uri, key))
        for text in [t for t in texts if t]:
            key = norm(text)
            if len(key) < 4:
                continue
            for lbl, uris in self.by_label.items():
                if len(lbl) < 4 or not (key in lbl or lbl in key):
                    continue
                tk, tl = tokens(key), tokens(lbl)
                if not tk or not tl:
                    allow = min(len(key), len(lbl)) >= 4
                elif key in lbl:
                    allow = set(tk).issubset(tl) and len(tl) <= len(tk) + 2
                else:
                    allow = set(tl).issubset(tk) and len(tk) <= len(tl) + 2
                if allow:
                    for uri in uris:
                        if uri not in seen:
                            seen.add(uri)
                            found.append((uri, lbl))
        return found

    def label_of(self, uri):
        for lab in self.g.objects(uri, RDFS.label):
            return str(lab)
        return str(uri)

    def near_labels(self, text):
        wanted = set(tokens(text))
        if not wanted:
            return []
        hits = []
        for lbl in self.by_label:
            if wanted.issubset(set(tokens(lbl))) and len(tokens(lbl)) <= len(wanted) + 2:
                hits.append(lbl)
        return hits[:3]

    def has_edge(self, su, ou, predicate=None):
        if predicate:
            pred = URIRef(BASE + "relation/" + re.sub(
                r"[^a-z0-9]+", "-", str(predicate).strip().lower()).strip("-"))
            return (su, pred, ou) in self.g, local(pred)
        for p, o in self.g.predicate_objects(su):
            if o == ou and local(p) not in META_LOCAL:
                return True, local(p)
        return False, None


def split_aliases(row):
    out = [row.get("expected", "")]
    out += [a for a in row.get("aliases", "").split("|") if a]
    return [t for t in out if t]


def classify_missing(onto, expected, predicted_note):
    near = onto.near_labels(expected)
    if near:
        return "PRESENT_LABEL_DIFFERS", predicted_note or ("near: " + "; ".join(near))
    return "MISSING_FROM_ONTOLOGY", predicted_note


def method_node(onto, row):
    cands = onto.candidates(split_aliases(row))
    if cands:
        return True, "MATCH", onto.label_of(cands[0][0])
    cls, pred = classify_missing(onto, row["expected"], "")
    return False, cls, pred


def method_nodes(onto, row):
    items = [i for i in row["expected"].split("|") if i]
    found, missing = [], []
    for item in items:
        if onto.candidates([item]):
            found.append(item)
        else:
            missing.append(item)
    all_ok = not missing
    predicted = f"{len(found)}/{len(items)} found"
    if all_ok:
        return True, "MATCH", predicted
    note = predicted + "; missing: " + "; ".join(missing)
    cls, pred = classify_missing(onto, missing[0], note)
    return False, cls, pred


def method_edge(onto, row):
    subs = onto.candidates([row.get("subject", "")])
    if not subs:
        return False, "SUBJECT_MISSING", "subject not in graph"
    objs = onto.candidates(split_aliases(row))
    predicate = row.get("predicate", "") or None
    obj_labels = [onto.label_of(u) for u, _ in objs]
    for su, _ in subs:
        for ou, _ in objs:
            ok, via = onto.has_edge(su, ou, predicate)
            if ok:
                return True, "MATCH", f"{onto.label_of(ou)} (via {via})"
    if not objs:
        cls, pred = classify_missing(onto, row["expected"], "subject found; object not in graph")
        return False, cls, pred
    pred = "object node exists but no such edge -> " + "; ".join(obj_labels)
    return False, "NOT_LINKED", pred


def method_edges(onto, row):
    subs = onto.candidates([row.get("subject", "")])
    if not subs:
        return False, "SUBJECT_MISSING", "subject not in graph"
    su = subs[0][0]
    predicate = row.get("predicate", "") or None
    items = [i for i in row["expected"].split("|") if i]
    linked, unlinked = [], []
    for item in items:
        objs = onto.candidates([item])
        hit = any(onto.has_edge(su, ou, predicate)[0] for ou, _ in objs)
        (linked if hit else unlinked).append(item)
    if not unlinked:
        return True, "MATCH", f"{len(linked)}/{len(items)} linked"
    pred = f"linked {len(linked)}/{len(items)}; unlinked: " + "; ".join(unlinked)
    return False, "NOT_LINKED", pred


def method_defines_text(onto, row):
    subs = onto.candidates([row.get("subject", "")])
    if not subs:
        return False, "SUBJECT_MISSING", "subject not in graph"
    su = subs[0][0]
    pred_uri = URIRef(BASE + "relation/defines")
    objs = list(onto.g.objects(su, pred_uri))
    if not objs:
        return False, "NO_DEFINITION_EDGE", "subject has no defines edge"
    expected = norm(row["expected"])
    for ou in objs:
        got = norm(onto.label_of(ou))
        if expected == got or expected in got or got in expected:
            return True, "MATCH", onto.label_of(ou)
    return False, "WRONG_DEFINITION", "defined as: " + "; ".join(onto.label_of(o) for o in objs)


METHODS = {
    "node": method_node,
    "nodes": method_nodes,
    "edge": method_edge,
    "edges": method_edges,
    "defines_text": method_defines_text,
}


def verdict(pct):
    for floor, name in VERDICT_THRESHOLDS:
        if pct >= floor:
            return name
    return "FAIL"


def main():
    if not TTL_PATH.exists():
        sys.exit(f"missing {TTL_PATH}")
    onto = Ontology(TTL_PATH)
    with open(GT_PATH, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    results = []
    for row in rows:
        fn = METHODS.get(row["method"])
        if fn is None:
            match, cls, pred = False, "UNKNOWN_METHOD", ""
        else:
            match, cls, pred = fn(onto, row)
        results.append({**row, "predicted": pred, "match": "PASS" if match else "FAIL",
                        "classification": cls})

    with open(ANSWERS_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["q_id", "category", "method", "subject", "predicate",
                                          "expected", "predicted", "match", "classification",
                                          "doc_location"])
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in w.fieldnames})

    total = len(results)
    correct = sum(r["match"] == "PASS" for r in results)
    pct = round(100 * correct / total, 1)
    cats = {}
    for r in results:
        c = cats.setdefault(r["category"], [0, 0])
        c[1] += 1
        c[0] += r["match"] == "PASS"
    misses = [r for r in results if r["match"] == "FAIL"]
    comp = [r for r in results if r["category"] == "COMPLETENESS"]
    comp_ok = sum(r["match"] == "PASS" for r in comp)

    lines = []
    lines.append("# Phase Y scorecard — QA against AI_in_Healthcare_Report.pdf")
    lines.append("")
    lines.append(f"- Questions: {total} (ground_truth.csv, answers extracted from the PDF only)")
    lines.append(f"- Overall accuracy: **{correct}/{total} = {pct}%**")
    lines.append(f"- Verdict (thresholds: >=90 PASS, 75-89 PASS_WITH_NOTES, <75 FAIL): **{verdict(pct)}**")
    lines.append(f"- Completeness probes: **{comp_ok}/{len(comp)}**")
    lines.append(f"- Ontology under test: `{TTL_PATH.name}`")
    lines.append("")
    lines.append("## Accuracy by category")
    lines.append("")
    lines.append("| Category | Correct | Of | % |")
    lines.append("|---|---|---|---|")
    for cat, (ok, n) in sorted(cats.items()):
        lines.append(f"| {cat} | {ok} | {n} | {round(100 * ok / n, 1)} |")
    lines.append("")
    lines.append("## Misses (each is a finding, not a matcher bug)")
    lines.append("")
    if misses:
        lines.append("| ID | Category | Question | Expected | Predicted / classification | Doc |")
        lines.append("|---|---|---|---|---|---|")
        for r in misses:
            q = r["question"][:80]
            exp = r["expected"][:70].replace("|", "; ")
            pred = (r["predicted"][:60] + " [" + r["classification"] + "]")
            lines.append(f"| {r['q_id']} | {r['category']} | {q} | {exp} | {pred} | {r['doc_location']} |")
    else:
        lines.append("None.")
    lines.append("")
    lines.append("## Classification legend")
    lines.append("")
    lines.append("- `MISSING_FROM_ONTOLOGY` — no node (or near node) for the expected fact.")
    lines.append("- `PRESENT_LABEL_DIFFERS` — a closely matching node exists under a different label.")
    lines.append("- `NOT_LINKED` — expected nodes exist but the required edge is absent.")
    lines.append("- `SUBJECT_MISSING` — the question's subject entity is absent.")
    lines.append("- `NO_DEFINITION_EDGE` — term exists but has no defines edge.")
    lines.append("- `WRONG_DEFINITION` — defines edge points at the wrong/placeholder text.")
    lines.append("")
    lines.append("## Method")
    lines.append("")
    lines.append("Deterministic: rdflib label lookup + direct-edge checks + defines-text comparison.")
    lines.append("Matching normalizes case, unicode dashes/quotes, whitespace, trailing periods;")
    lines.append("short tokens (<4 chars) require exact match, longer allow mutual containment.")
    lines.append("Expected answers are never altered to fit the graph.")
    lines.append("")
    lines.append("## Known limitations")
    lines.append("")
    lines.append("- Chart images in the PDF have no extractable text; questions come from tables/prose only.")
    lines.append("- 34 questions sample the report; they do not cover every statement.")
    lines.append("- Accuracy measures factual coverage of extracted ontology content, not OWL reasoning.")
    lines.append("")
    SCORECARD_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"questions: {total}  correct: {correct}  accuracy: {pct}%  verdict: {verdict(pct)}")
    for cat, (ok, n) in sorted(cats.items()):
        print(f"  {cat:15s} {ok}/{n}")
    print(f"answers   -> {ANSWERS_PATH}")
    print(f"scorecard -> {SCORECARD_PATH}")


if __name__ == "__main__":
    main()
