#!/usr/bin/env python3
"""Export cleaned JSON -> final Turtle + extended SHACL shapes (+ validation).

Outputs (final_export/):
  - ontology_final.ttl         graph with deduplicated reified relationships
  - shacl_shapes_final.ttl     4 structural shapes + vocabulary sh:in + no-self-loop rule
  - shacl_report.txt/.ttl      validation result, or run instructions if pyshacl missing
  - EXPORT_README.md           how to inspect/query the export
  - export_report.json         machine-readable gate G2/G3 results

Usage:
  python export_final.py
"""
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS

from phase1_duplicate_audit import normalize

KIT = Path(__file__).resolve().parent
OUT = KIT / "final_export"
BASE = Namespace("https://example.org/healthcare-ontology/")


def slug(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text).strip().lower()).strip("-")
    return s or "unnamed"


def uri_for(name, kind):
    key = str(name).strip()
    suffix = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return URIRef(str(BASE) + kind + "/" + slug(key) + "-" + suffix)


def pred_for_relation(name):
    return URIRef(str(BASE) + "relation/" + slug(name))


def turtle_string(s):
    return str(s).replace("\\", "\\\\").replace('"', '\\"')


def build_graph(data):
    g = Graph()
    g.bind("ex", BASE)
    g.bind("rdf", RDF)
    g.bind("rdfs", RDFS)
    ontology = URIRef(str(BASE) + "ontology")
    g.add((ontology, RDF.type, URIRef("http://www.w3.org/2002/07/owl#Ontology")))
    g.add((ontology, RDFS.label, Literal(data.get("ontology_name", "Imported ontology"))))
    g.add((ontology, RDFS.comment, Literal(data.get("description", ""))))

    triple_domains = defaultdict(set)
    triple_dirs = defaultdict(set)
    for domain in data.get("businesses", []):
        dname = domain.get("business", "Unnamed domain")
        du = uri_for(dname, "domain")
        g.add((du, RDF.type, BASE.BusinessDomain))
        g.add((du, RDFS.label, Literal(dname)))
        for ent in domain.get("entities", []):
            name, typ = ent.get("name"), ent.get("type")
            if not isinstance(name, str) or not name.strip():
                continue
            eu = uri_for(name, "entity")
            g.add((eu, RDF.type, BASE.Entity))
            g.add((eu, RDFS.label, Literal(name)))
            if isinstance(typ, str) and typ.strip():
                g.add((eu, BASE.entityType, Literal(typ)))
            g.add((eu, BASE.inDomain, du))
            for rel in ent.get("relationships", []):
                p, d = rel.get("type"), rel.get("direction")
                if not isinstance(p, str) or not p.strip():
                    continue
                if d == "outgoing":
                    s, t = name, rel.get("target")
                elif d == "incoming":
                    s, t = rel.get("source"), name
                else:
                    continue
                if not isinstance(s, str) or not isinstance(t, str):
                    continue
                triple_domains[(s, p, t)].add(dname)
                triple_dirs[(s, p, t)].add(d)

    for (s, p, t), doms in triple_domains.items():
        su, tu = uri_for(s, "entity"), uri_for(t, "entity")
        g.add((su, pred_for_relation(p), tu))
        rid = URIRef(str(BASE) + "relationship/" + hashlib.sha1(
            f"{s}|{p}|{t}".encode("utf-8")).hexdigest()[:16])
        g.add((rid, RDF.type, BASE.Relationship))
        g.add((rid, BASE.source, su))
        g.add((rid, BASE.target, tu))
        g.add((rid, BASE.relationshipType, Literal(p)))
        dirs = triple_dirs[(s, p, t)]
        direction = next(iter(dirs)) if len(dirs) == 1 else "outgoing"
        g.add((rid, BASE.direction, Literal(direction)))
        for dname in doms:
            g.add((rid, BASE.observedInDomain, uri_for(dname, "domain")))
    return g, triple_domains, triple_dirs


def build_shapes(data, base_text):
    types = sorted({ent.get("type") for _, ent in _records(data) if ent.get("type")})
    preds = sorted({rel.get("type") for _, rel in _all_rels(data) if rel.get("type")})

    def in_list(values, per_line=6):
        chunks = []
        for i in range(0, len(values), per_line):
            chunks.append(" ".join(f'"{turtle_string(v)}"'
                                   for v in values[i:i + per_line]))
        return "(\n        " + "\n        ".join(chunks) + "\n    )"

    extra = f"""

ex:EntityTypeVocabulary a sh:NodeShape ;
    sh:targetClass ex:Entity ;
    sh:property [ sh:path ex:entityType ;
        sh:in {in_list(types)} ;
        sh:message "entityType must be one of the declared type vocabulary." ] .

ex:RelationshipTypeVocabulary a sh:NodeShape ;
    sh:targetClass ex:Relationship ;
    sh:property [ sh:path ex:relationshipType ;
        sh:in {in_list(preds)} ;
        sh:message "relationshipType must be one of the declared predicate vocabulary." ] .

ex:NoSelfLoopShape a sh:NodeShape ;
    sh:targetClass ex:Relationship ;
    sh:sparql [
        a sh:SPARQLConstraint ;
        sh:message "Relationship source and target must differ (self-loop)." ;
        sh:select \"\"\"SELECT $this WHERE {{ $this ex:source ?x ; ex:target ?y . FILTER(?x = ?y) }}\"\"\"
    ] .
"""
    return base_text.rstrip() + "\n" + extra


def _records(data):
    for dom in data.get("businesses", []):
        for ent in dom.get("entities", []):
            yield dom, ent


def _all_rels(data):
    for _, ent in _records(data):
        for rel in ent.get("relationships", []):
            yield _, rel


def run_validation(g, shapes_graph):
    try:
        from pyshacl import validate
    except ImportError:
        try:
            subprocess.run(
                [sys.executable, "-m", "pip", "install", "pyshacl",
                 "--quiet", "--disable-pip-version-check"],
                timeout=120, check=False,
                capture_output=True,
            )
            from pyshacl import validate
        except Exception as exc:
            return None, f"pyshacl unavailable ({exc.__class__.__name__})"
    try:
        conforms, report_graph, text = validate(
            g, shacl_graph=shapes_graph, inference="rdfs",
            abort_on_first=False, allow_infos=True, allow_warnings=True)
        return (bool(conforms), report_graph, text), None
    except Exception as exc:
        return None, f"validation error: {exc}"


def main():
    src = KIT / "business_entity_ontology.cleaned.json"
    if not src.exists():
        raise SystemExit("business_entity_ontology.cleaned.json missing - run apply_decisions.py first")
    data = json.loads(src.read_text(encoding="utf-8"))
    OUT.mkdir(exist_ok=True)

    g, triple_domains, triple_dirs = build_graph(data)
    ttl_path = OUT / "ontology_final.ttl"
    g.serialize(destination=str(ttl_path), format="turtle")
    g2 = Graph().parse(str(ttl_path), format="turtle")

    reified = set(g2.subjects(RDF.type, BASE.Relationship))
    unique_triples = len(triple_domains)
    entities = {ent.get("name") for _, ent in _records(data)}
    reified_ok = len(reified) == unique_triples
    multi_dir = sorted(str(k) for k, v in triple_dirs.items() if len(v) > 1)

    base_shapes = (KIT / "ontology_shapes.ttl").read_text(encoding="utf-8")
    shapes_text = build_shapes(data, base_shapes)
    shapes_path = OUT / "shacl_shapes_final.ttl"
    shapes_path.write_text(shapes_text, encoding="utf-8")
    shapes_graph = Graph().parse(str(shapes_path), format="turtle")

    result, err = run_validation(g2, shapes_graph)
    if result is None:
        report_text = (
            "SHACL validation NOT executed in this environment.\n"
            f"Reason: {err}\n\n"
            "Run on a machine with network access:\n"
            "    pip install pyshacl\n"
            "    python export_final.py\n"
            "Result will overwrite this file.\n"
        )
        (OUT / "shacl_report.txt").write_text(report_text, encoding="utf-8")
        conforms_str = "not run"
    else:
        conforms, report_graph, text = result
        report_graph.serialize(destination=str(OUT / "shacl_report.ttl"), format="turtle")
        (OUT / "shacl_report.txt").write_text(text, encoding="utf-8")
        conforms_str = str(bool(conforms))

    readme = f"""# Final export - AI in Healthcare Business Entity Ontology

## Files
- `ontology_final.ttl` - the ontology as RDF Turtle ({len(g2)} triples)
- `shacl_shapes_final.ttl` - SHACL constraints ({len(shapes_graph)} triples)
- `shacl_report.txt` / `shacl_report.ttl` - validation result (Conforms: {conforms_str})
- `export_report.json` - machine-readable gate results

## Inspect
- Protoge: open `ontology_final.ttl`, add `shacl_shapes_final.ttl` as shapes
- Python: `from rdflib import Graph; g = Graph().parse("ontology_final.ttl")`

## Example query
```sparql
SELECT ?s ?t WHERE {{ ?s <https://example.org/healthcare-ontology/relation/includes> ?t }} LIMIT 10
```

## Re-run export / validation
```bash
python apply_decisions.py
python export_final.py
```
"""
    (OUT / "EXPORT_README.md").write_text(readme, encoding="utf-8")

    export_report = {
        "source": src.name,
        "graph_triples": len(g2),
        "reified_relationships": len(reified),
        "unique_triples_expected": unique_triples,
        "gate_g2_reified_dedup_ok": reified_ok,
        "entities_in_json": len(entities),
        "multi_domain_direction_conflicts": multi_dir,
        "shapes_triples": len(shapes_graph),
        "shacl_conforms": conforms_str,
        "shacl_error": err,
    }
    (OUT / "export_report.json").write_text(
        json.dumps(export_report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"graph triples         : {len(g2)}")
    print(f"reified relationships : {len(reified)} (unique expected {unique_triples}) -> "
          f"G2 {'PASS' if reified_ok else 'FAIL'}")
    print(f"shapes triples        : {len(shapes_graph)}")
    print(f"direction conflicts   : {len(multi_dir)} (canonicalized to 'outgoing')")
    print(f"SHACL conforms        : {conforms_str}" + (f" ({err})" if err else ""))
    print(f"export dir            -> {OUT}")


if __name__ == "__main__":
    main()
