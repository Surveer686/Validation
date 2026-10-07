#!/usr/bin/env python3
"""Phase 1: duplicate-relationship audit for business_entity_ontology.json.

Classifies every relationship occurrence into:
  - exact duplicate groups (within-domain repeat / cross-domain copy / mixed direction encoding)
  - inverse predicate pairs          (A --treats--> B  /  B --treated_by--> A)
  - reversed same-predicate pairs    (A --treats--> B  /  B --treats--> A)
  - same endpoints, different predicate
  - predicate lemma variants         (DETECT/DETECTS, APPLIED_TO/APPLIES_TO, ...)
  - same stem, different marker      (LOCATED_AT/LOCATED_ON)
  - self loops
plus a reconciliation of declared 389 vs computed 388 statistics,
and a built-in self-test using the Hospital/Patient scenario.

Usage:
  python phase1_duplicate_audit.py [path/to/business_entity_ontology.json]
Output:
  ontology_validation_output/phase1_duplicate_report.json
"""
import json
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path

DIR_WORDS = {
    "by", "of", "from", "to", "in", "at", "on", "with",
    "about", "for", "into", "as", "via", "during", "over", "after",
}

IRREGULAR = {
    "driven": "drive", "given": "give", "taken": "take", "made": "make",
    "built": "build", "used": "use", "applied": "apply", "enabled": "enable",
    "related": "relate", "located": "locate", "detected": "detect",
    "excluded": "exclude", "required": "require", "included": "include",
    "generated": "generate", "regulated": "regulate", "embedded": "embed",
    "treated": "treat", "evaluated": "evaluate", "tested": "test",
    "reported": "report", "suggested": "suggest", "championed": "champion",
    "dependent": "depend", "derived": "derive", "governed": "govern",
    "enhanced": "enhance", "improved": "improve", "deployed": "deploy",
    "represents": "represent", "describes": "describe", "requires": "require",
}


def lemma(token):
    w = token.lower()
    if w in IRREGULAR:
        return IRREGULAR[w]
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith(("ches", "shes", "sses", "xes", "zes")):
        return w[:-2]
    if w.endswith("ing") and len(w) > 5:
        return w[:-3]
    if w.endswith("ed") and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and not w.endswith("ss") and len(w) > 3:
        return w[:-1]
    return w


def pred_signature(predicate):
    tokens = [t for t in predicate.lower().split("_") if t]
    dirs = tuple(t for t in tokens if t in DIR_WORDS)
    stems = tuple(lemma(t) for t in tokens if t not in DIR_WORDS)
    return stems, dirs


def is_inverse_pair(p1, p2):
    if p1 == p2:
        return False
    s1, d1 = pred_signature(p1)
    s2, d2 = pred_signature(p2)
    if not s1 or s1 != s2:
        return False
    if "from" in d1 and "to" in d2:
        return True
    if "to" in d1 and "from" in d2:
        return True
    if "by" in d1 and "by" not in d2:
        return True
    if "by" in d2 and "by" not in d1:
        return True
    return False


def classify_predicate_pairs(predicates):
    out = {
        "lemma_variants": [],
        "inverse_candidates": [],
        "same_stem_diff_marker": [],
    }
    for p1, p2 in combinations(sorted(predicates), 2):
        s1, d1 = pred_signature(p1)
        s2, d2 = pred_signature(p2)
        if not s1 or s1 != s2:
            continue
        if d1 == d2:
            out["lemma_variants"].append({"predicates": [p1, p2], "stems": list(s1)})
        elif is_inverse_pair(p1, p2):
            out["inverse_candidates"].append({"predicates": [p1, p2]})
        else:
            out["same_stem_diff_marker"].append(
                {"predicates": [p1, p2], "markers": [list(d1), list(d2)]}
            )
    return out


def normalize(data):
    occ = []
    for dom in data.get("businesses", []):
        dname = dom.get("business")
        for ent in dom.get("entities", []):
            name = ent.get("name")
            for rel in ent.get("relationships", []) or []:
                p, dr = rel.get("type"), rel.get("direction")
                if dr == "outgoing":
                    s, t = name, rel.get("target")
                elif dr == "incoming":
                    s, t = rel.get("source"), name
                else:
                    s, t = rel.get("source"), rel.get("target")
                if (
                    isinstance(s, str) and s and isinstance(t, str) and t
                    and isinstance(p, str) and p
                ):
                    occ.append({
                        "s": s, "p": p, "t": t, "domain": dname,
                        "entity": name, "direction": dr,
                    })
    return occ


def classify(occ):
    groups = defaultdict(list)
    orient = defaultdict(set)
    for o in occ:
        groups[(o["s"], o["p"], o["t"])].append(o)
        orient[(o["s"], o["t"])].add(o["p"])

    exact = []
    for (s, p, t), os_ in groups.items():
        if len(os_) < 2:
            continue
        per_domain = Counter(o["domain"] for o in os_)
        within = sum(c - 1 for c in per_domain.values())
        cross = len(per_domain) - 1
        dirs = sorted({str(o["direction"]) for o in os_})
        mixed = len(dirs) > 1
        if within > 0:
            severity = "High"
        elif mixed:
            severity = "Medium"
        else:
            severity = "Low"
        exact.append({
            "source": s, "predicate": p, "target": t,
            "occurrences": len(os_), "extra_occurrences": len(os_) - 1,
            "within_domain_extra": within, "cross_domain_extra": cross,
            "domains": dict(per_domain), "directions": dirs,
            "mixed_direction_encoding": mixed, "severity": severity,
        })
    exact.sort(key=lambda e: (-e["extra_occurrences"], e["predicate"]))

    inverse_confirmed, reversed_confirmed, other_cross = [], [], []
    same_dir_diff = []
    for (s, t), preds in orient.items():
        if len(preds) > 1:
            same_dir_diff.append({"source": s, "target": t, "predicates": sorted(preds)})
        if s >= t:
            continue
        back = orient.get((t, s), set())
        for p in sorted(preds):
            for q in sorted(back):
                entry = {"a": s, "predicate": p, "b": t, "reverse_predicate": q}
                if p == q:
                    reversed_confirmed.append(entry)
                elif is_inverse_pair(p, q):
                    inverse_confirmed.append(entry)
                else:
                    other_cross.append(entry)
    same_dir_diff.sort(key=lambda e: e["source"])
    for lst in (inverse_confirmed, reversed_confirmed, other_cross):
        lst.sort(key=lambda e: (e["a"], e["predicate"], e["reverse_predicate"]))

    self_loops = [
        {"source": s, "predicate": p, "target": t, "occurrences": len(os_)}
        for (s, p, t), os_ in groups.items() if s == t
    ]
    self_loops.sort(key=lambda e: e["predicate"])

    pred_pairs = classify_predicate_pairs({o["p"] for o in occ})

    return {
        "unique_triples": len(groups),
        "exact_duplicates": exact,
        "inverse_confirmed": inverse_confirmed,
        "reversed_same_predicate": reversed_confirmed,
        "cross_orientation_other": other_cross,
        "same_endpoints_diff_predicate": same_dir_diff,
        "self_loops": self_loops,
        "predicate_pairs": pred_pairs,
    }


def stats_variants(occ, data):
    norm = {(o["s"], o["p"], o["t"]) for o in occ}
    withdir = {(o["s"], o["p"], o["t"], o["direction"]) for o in occ}
    entity_first = set()
    per_domain = set()
    for o in occ:
        if o["direction"] == "outgoing":
            entity_first.add((o["entity"], o["p"], o["t"]))
        else:
            entity_first.add((o["entity"], o["p"], o["s"]))
    seen_dom = defaultdict(set)
    for o in occ:
        seen_dom[o["domain"]].add((o["s"], o["p"], o["t"]))
    per_domain_total = sum(len(v) for v in seen_dom.values())
    undirected = {(min(s, t), p, max(s, t)) for s, p, t in norm}
    stripped = {tuple(x.strip() for x in trip) for trip in norm}
    casefolded = {tuple(x.casefold() for x in trip) for trip in norm}
    endpoint_pairs = {frozenset((s, t)) for s, _, t in norm}
    variants = {
        "normalized_unique_triples": len(norm),
        "with_direction_marker": len(withdir),
        "entity_always_subject": len(entity_first),
        "sum_of_per_domain_unique": per_domain_total,
        "undirected_triples": len(undirected),
        "whitespace_stripped": len(stripped),
        "casefolded": len(casefolded),
        "unordered_endpoint_pairs": len(endpoint_pairs),
    }
    declared = (data.get("statistics") or {}).get("unique_relationships")
    matched = [k for k, v in variants.items() if v == declared]
    return {
        "declared": declared,
        "variants": variants,
        "matching_definitions": matched,
        "reconciled": bool(matched),
    }


def self_test():
    base = "Test Domain"
    hospital = [
        {"s": "Hospital A", "p": "treats", "t": "Patient B", "domain": base,
         "entity": "Hospital A", "direction": "outgoing"},
        {"s": "Patient B", "p": "treated_by", "t": "Hospital A", "domain": base,
         "entity": "Patient B", "direction": "outgoing"},
        {"s": "Hospital A", "p": "treats", "t": "Patient B", "domain": base,
         "entity": "Hospital A", "direction": "outgoing"},
    ]
    r = classify(hospital)
    checks = []

    def check(name, cond, detail):
        checks.append({"check": name, "passed": bool(cond), "detail": detail})

    ex = [e for e in r["exact_duplicates"] if e["predicate"] == "treats"]
    check("exact_repeat_detected",
          len(ex) == 1 and ex[0]["occurrences"] == 2 and ex[0]["extra_occurrences"] == 1,
          ex)
    check("inverse_pair_detected",
          r["inverse_confirmed"] == [{
              "a": "Hospital A", "predicate": "treats", "b": "Patient B",
              "reverse_predicate": "treated_by"}],
          r["inverse_confirmed"])
    check("lemma_variant_not_flagged_as_inverse",
          all(sorted(c["predicates"]) != ["treated_by", "treats"]
              for c in r["predicate_pairs"]["lemma_variants"]),
          r["predicate_pairs"]["lemma_variants"])

    mirrored = hospital + [
        {"s": "Hospital A", "p": "treats", "t": "Patient B", "domain": base,
         "entity": "Patient B", "direction": "incoming"},
    ]
    r2 = classify(mirrored)
    ex2 = [e for e in r2["exact_duplicates"] if e["predicate"] == "treats"]
    check("mixed_direction_encoding_flagged",
          len(ex2) == 1 and ex2[0]["mixed_direction_encoding"] is True
          and ex2[0]["occurrences"] == 3,
          ex2)
    check("reversed_same_predicate_flagged",
          classify([
              {"s": "A", "p": "treats", "t": "B", "domain": base,
               "entity": "A", "direction": "outgoing"},
              {"s": "B", "p": "treats", "t": "A", "domain": base,
               "entity": "B", "direction": "outgoing"},
          ])["reversed_same_predicate"] == [
              {"a": "A", "predicate": "treats", "b": "B", "reverse_predicate": "treats"}],
          "A treats B / B treats A")
    check("transition_from_to_inverse",
          is_inverse_pair("TRANSITION_FROM", "TRANSITION_TO"),
          pred_signature("TRANSITION_FROM"), )
    passed = sum(1 for c in checks if c["passed"])
    return {"passed": passed, "total": len(checks), "checks": checks}


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).with_name(
        "business_entity_ontology.json")
    data = json.loads(src.read_text(encoding="utf-8"))
    occ = normalize(data)
    res = classify(occ)
    stats = stats_variants(occ, data)
    test = self_test()

    exact_groups = res["exact_duplicates"]
    extras_total = sum(e["extra_occurrences"] for e in exact_groups)
    within_total = sum(e["within_domain_extra"] for e in exact_groups)
    cross_total = sum(e["cross_domain_extra"] for e in exact_groups)
    mixed_groups = sum(1 for e in exact_groups if e["mixed_direction_encoding"])

    logical_estimate = (res["unique_triples"] - len(res["inverse_confirmed"])
                        - len(res["reversed_same_predicate"]))

    report = {
        "meta": {
            "source_file": src.name,
            "occurrences": len(occ),
            "unique_triples": res["unique_triples"],
            "distinct_predicates": len({o["p"] for o in occ}),
            "tool": "phase1_duplicate_audit.py",
        },
        "self_test": test,
        "exact_duplicates": {
            "summary": {
                "groups": len(exact_groups),
                "extra_occurrences_total": extras_total,
                "within_domain_extra": within_total,
                "cross_domain_extra": cross_total,
                "groups_with_mixed_direction_encoding": mixed_groups,
            },
            "groups": exact_groups,
        },
        "inverse_predicate_pairs": {
            "vocabulary_candidates": res["predicate_pairs"]["inverse_candidates"],
            "confirmed_in_data": res["inverse_confirmed"],
        },
        "reversed_same_predicate": res["reversed_same_predicate"],
        "cross_orientation_other_predicates": res["cross_orientation_other"],
        "same_endpoints_diff_predicate": res["same_endpoints_diff_predicate"],
        "predicate_lemma_variants": res["predicate_pairs"]["lemma_variants"],
        "same_stem_diff_marker": res["predicate_pairs"]["same_stem_diff_marker"],
        "self_loops": res["self_loops"],
        "statistics_reconciliation": stats,
        "logical_merge_estimate": {
            "unique_triples": res["unique_triples"],
            "minus_inverse_pairs": len(res["inverse_confirmed"]),
            "minus_reversed_same_predicate": len(res["reversed_same_predicate"]),
            "estimated_logical_facts": logical_estimate,
            "note": "Upper-bound dedup effect; final number depends on approved policy.",
        },
    }

    out_dir = Path(__file__).with_name("ontology_validation_output")
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "phase1_duplicate_report.json"
    out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"occurrences={len(occ)}  unique_triples={res['unique_triples']}  "
          f"predicates={report['meta']['distinct_predicates']}")
    print(f"EXACT duplicates: groups={len(exact_groups)} extras={extras_total} "
          f"(within-domain={within_total}, cross-domain={cross_total}, "
          f"mixed-direction groups={mixed_groups})")
    print(f"INVERSE pairs: vocabulary candidates={len(res['predicate_pairs']['inverse_candidates'])} "
          f"confirmed in data={len(res['inverse_confirmed'])}")
    for e in res["inverse_confirmed"][:10]:
        print(f"    {e['a']} --{e['predicate']}--> {e['b']}  ==  "
              f"{e['b']} --{e['reverse_predicate']}--> {e['a']}")
    print(f"REVERSED same predicate: {len(res['reversed_same_predicate'])}")
    for e in res["reversed_same_predicate"][:10]:
        print(f"    {e['a']} --{e['predicate']}--> {e['b']}  <->  reverse")
    print(f"SAME endpoints different predicate: {len(res['same_endpoints_diff_predicate'])}")
    print(f"CROSS-orientation other predicates: {len(res['cross_orientation_other'])}")
    print(f"LEMMA variants: {len(res['predicate_pairs']['lemma_variants'])}")
    for e in res["predicate_pairs"]["lemma_variants"][:10]:
        print(f"    {' / '.join(e['predicates'])}")
    print(f"SAME stem different marker: {len(res['predicate_pairs']['same_stem_diff_marker'])}")
    print(f"SELF loops: {len(res['self_loops'])}")
    print(f"STATISTICS: declared={stats['declared']} matching definitions="
          f"{stats['matching_definitions'] or 'NONE'}")
    print(f"LOGICAL estimate: {logical_estimate} facts "
          f"(from {res['unique_triples']})")
    print(f"SELF-TEST: {test['passed']}/{test['total']} passed")
    for c in test["checks"]:
        if not c["passed"]:
            print(f"  FAILED: {c['check']} -> {c['detail']}")
    print(f"report -> {out_path}")
    if test["passed"] != test["total"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
