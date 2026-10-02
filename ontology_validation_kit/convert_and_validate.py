#!/usr/bin/env python3
"""Convert the user's JSON ontology to RDF Turtle and run structural/SHACL checks.

Usage:
  python convert_and_validate.py business_entity_ontology.json

Install dependencies first:
  pip install -r requirements.txt
"""
import json, re, sys, hashlib
from pathlib import Path
from collections import Counter, defaultdict

from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS, XSD
from rdflib.namespace import SH
from pyshacl import validate

BASE = Namespace("https://example.org/healthcare-ontology/")


def slug(text):
    s = re.sub(r"[^a-zA-Z0-9]+", "-", str(text).strip().lower()).strip("-")
    return s or "unnamed"


def uri_for_entity(name):
    # Slug plus a stable suffix avoids collisions between names that slug alike.
    key = str(name).strip()
    suffix = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return URIRef(str(BASE) + "entity/" + slug(key) + "-" + suffix)


def uri_for_domain(name):
    key = str(name).strip()
    suffix = hashlib.sha1(key.encode("utf-8")).hexdigest()[:8]
    return URIRef(str(BASE) + "domain/" + slug(key) + "-" + suffix)


def pred_for_relation(name):
    return URIRef(str(BASE) + "relation/" + slug(name))


def audit_json(data):
    errors, warnings = [], []
    if not isinstance(data, dict): errors.append("Top-level JSON value must be an object."); return errors, warnings, {}
    for key in ("ontology_name", "businesses"):
        if key not in data: errors.append(f"Missing top-level key: {key}")
    domains = data.get("businesses", [])
    if not isinstance(domains, list): errors.append("'businesses' must be a list."); return errors, warnings, {}
    declared_entity_pairs, entity_names = set(), set()
    entities_by_name = defaultdict(set)
    global_entity_names = set()
    relationship_occurrences = []
    domain_names = set()
    for di, domain in enumerate(domains):
        if not isinstance(domain, dict): errors.append(f"businesses[{di}] must be an object."); continue
        domain_name = domain.get("business")
        if not isinstance(domain_name, str) or not domain_name.strip(): errors.append(f"businesses[{di}].business must be a non-empty string.")
        else: domain_names.add(domain_name)
        entities = domain.get("entities", [])
        if not isinstance(entities, list): errors.append(f"Domain {domain_name!r}: entities must be a list."); continue
        if domain.get("entity_count") != len(entities): warnings.append(f"Domain {domain_name!r}: entity_count={domain.get('entity_count')!r}, actual={len(entities)}.")
        for ei, ent in enumerate(entities):
            loc = f"domain {domain_name!r}, entities[{ei}]"
            if not isinstance(ent, dict): errors.append(f"{loc} must be an object."); continue
            name, typ = ent.get("name"), ent.get("type")
            if not isinstance(name, str) or not name.strip(): errors.append(f"{loc}: missing/invalid name."); continue
            if not isinstance(typ, str) or not typ.strip(): errors.append(f"{loc}, entity {name!r}: missing/invalid type.")
            global_entity_names.add(name)
            entities_by_name[name].add(typ)
            declared_entity_pairs.add((name, typ))
            rels = ent.get("relationships", [])
            if not isinstance(rels, list): errors.append(f"{loc}, entity {name!r}: relationships must be a list."); continue
            for ri, rel in enumerate(rels):
                rloc = f"{loc}, entity {name!r}, relationships[{ri}]"
                if not isinstance(rel, dict): errors.append(f"{rloc} must be an object."); continue
                rtype, direction = rel.get("type"), rel.get("direction")
                if not isinstance(rtype, str) or not rtype.strip(): errors.append(f"{rloc}: missing/invalid type.")
                if direction not in ("incoming", "outgoing"): errors.append(f"{rloc}: direction must be incoming/outgoing.")
                if direction == "outgoing": source, target = name, rel.get("target")
                elif direction == "incoming": source, target = rel.get("source"), name
                else: source, target = rel.get("source"), rel.get("target")
                if not isinstance(source, str) or not source.strip(): errors.append(f"{rloc}: missing/invalid source after direction normalization.")
                if not isinstance(target, str) or not target.strip(): errors.append(f"{rloc}: missing/invalid target after direction normalization.")
                if isinstance(source, str) and isinstance(target, str) and isinstance(rtype, str):
                    relationship_occurrences.append((source, rtype, target, domain_name, name, direction))
    for source, rtype, target, domain_name, name, direction in relationship_occurrences:
        if source not in global_entity_names: warnings.append(f"Relationship source {source!r} is not declared as an entity (domain {domain_name!r}).")
        if target not in global_entity_names: warnings.append(f"Relationship target {target!r} is not declared as an entity (domain {domain_name!r}).")
    multi_types = {name: sorted(types) for name, types in entities_by_name.items() if len(types) > 1}
    if multi_types: warnings.append(f"{len(multi_types)} entity names have multiple types; review identity/type modeling.")
    triples = {(s, p, o) for s, p, o, *_ in relationship_occurrences}
    duplicate_occurrences = len(relationship_occurrences) - len(triples)
    if duplicate_occurrences: warnings.append(f"{duplicate_occurrences} repeated relationship occurrences after normalizing direction; some may be mirrored or duplicated.")
    declared_stats = data.get("statistics", {}) if isinstance(data.get("statistics", {}), dict) else {}
    actual_pairs, actual_triples = len(declared_entity_pairs), len(triples)
    if "unique_entities" in declared_stats and declared_stats["unique_entities"] != actual_pairs:
        warnings.append(f"statistics.unique_entities declares {declared_stats['unique_entities']}; computed unique (name,type) pairs={actual_pairs}.")
    if "unique_relationships" in declared_stats and declared_stats["unique_relationships"] != actual_triples:
        warnings.append(f"statistics.unique_relationships declares {declared_stats['unique_relationships']}; computed normalized unique triples={actual_triples}.")
    summary = {
        "domains": len(domains), "entity_occurrences": sum(len(d.get("entities", [])) for d in domains if isinstance(d, dict) and isinstance(d.get("entities", []), list)),
        "unique_entity_names": len(global_entity_names), "unique_name_type_pairs": actual_pairs,
        "relationship_occurrences": len(relationship_occurrences), "unique_normalized_relationship_triples": actual_triples,
        "duplicate_relationship_occurrences": duplicate_occurrences, "multi_typed_entity_names_count": len(multi_types),
        "multi_typed_entity_names_examples": dict(list(multi_types.items())[:50]),
        "declared_statistics": declared_stats,
    }
    return errors, warnings, summary


def convert(data, ttl_path):
    g = Graph()
    g.bind("ex", BASE); g.bind("rdf", RDF); g.bind("rdfs", RDFS); g.bind("xsd", XSD)
    ontology = URIRef(str(BASE) + "ontology")
    g.add((ontology, RDF.type, URIRef("http://www.w3.org/2002/07/owl#Ontology")))
    g.add((ontology, RDFS.label, Literal(data.get("ontology_name", "Imported ontology"))))
    g.add((ontology, RDFS.comment, Literal(data.get("description", "Converted from JSON; source semantics preserved where possible."))))

    all_entity_types = defaultdict(set)
    entity_domains = defaultdict(set)
    all_relationships = []
    domain_uris = {}
    for domain in data.get("businesses", []):
        dname = domain.get("business", "Unnamed domain")
        du = uri_for_domain(dname); domain_uris[dname] = du
        g.add((du, RDF.type, BASE.BusinessDomain)); g.add((du, RDFS.label, Literal(dname)))
        for ent in domain.get("entities", []):
            name, typ = ent.get("name"), ent.get("type")
            if not isinstance(name, str) or not name.strip(): continue
            eu = uri_for_entity(name)
            g.add((eu, RDF.type, BASE.Entity)); g.add((eu, RDFS.label, Literal(name)))
            if isinstance(typ, str) and typ.strip():
                g.add((eu, BASE.entityType, Literal(typ)))
                all_entity_types[name].add(typ)
            g.add((eu, BASE.inDomain, du)); entity_domains[name].add(dname)
            for rel in ent.get("relationships", []):
                direction = rel.get("direction")
                rtype = rel.get("type")
                if not isinstance(rtype, str) or not rtype.strip(): continue
                if direction == "outgoing": source, target = name, rel.get("target")
                elif direction == "incoming": source, target = rel.get("source"), name
                else: continue
                if not isinstance(source, str) or not isinstance(target, str): continue
                all_relationships.append((source, rtype, target, direction, dname))
    # Reify each relationship for SHACL validation and retain a direct RDF triple for graph querying.
    for idx, (source, rtype, target, direction, dname) in enumerate(all_relationships):
        su, tu = uri_for_entity(source), uri_for_entity(target)
        predicate = pred_for_relation(rtype)
        g.add((su, predicate, tu))
        rid = URIRef(str(BASE) + "relationship/" + hashlib.sha1(f"{source}|{rtype}|{target}|{idx}".encode()).hexdigest())
        g.add((rid, RDF.type, BASE.Relationship))
        g.add((rid, BASE.source, su)); g.add((rid, BASE.target, tu))
        g.add((rid, BASE.relationshipType, Literal(rtype)))
        g.add((rid, BASE.direction, Literal(direction)))
        if dname in domain_uris: g.add((rid, BASE.observedInDomain, domain_uris[dname]))
    g.serialize(destination=str(ttl_path), format="turtle")
    return g


def main():
    if len(sys.argv) < 2:
        print("Usage: python convert_and_validate.py business_entity_ontology.json"); sys.exit(2)
    source_path = Path(sys.argv[1]).resolve()
    out_dir = Path("ontology_validation_output"); out_dir.mkdir(exist_ok=True)
    data = json.loads(source_path.read_text(encoding="utf-8"))
    errors, warnings, summary = audit_json(data)
    (out_dir / "json_audit_report.json").write_text(json.dumps({"errors": errors, "warnings": warnings, "summary": summary}, indent=2, ensure_ascii=False), encoding="utf-8")
    if errors:
        print(f"JSON structural audit: FAILED ({len(errors)} errors). See {out_dir/'json_audit_report.json'}")
        for e in errors[:30]: print("ERROR:", e)
        sys.exit(1)
    ttl_path = out_dir / "ontology.ttl"
    data_graph = convert(data, ttl_path)
    shapes_path = Path(__file__).with_name("ontology_shapes.ttl")
    shapes_graph = Graph().parse(str(shapes_path), format="turtle")
    conforms, report_graph, report_text = validate(data_graph, shacl_graph=shapes_graph, inference="rdfs", abort_on_first=False, allow_infos=True, allow_warnings=True)
    report_graph.serialize(destination=str(out_dir / "shacl_report.ttl"), format="turtle")
    (out_dir / "shacl_report.txt").write_text(report_text, encoding="utf-8")
    summary.update({"json_structural_errors": len(errors), "json_warnings": len(warnings), "rdf_triples": len(data_graph), "shacl_conforms": bool(conforms), "shacl_results": sum(1 for _ in report_graph.subjects(RDF.type, SH.ValidationResult))})
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"output_directory": str(out_dir), "json_errors": len(errors), "json_warnings": len(warnings), "rdf_triples": len(data_graph), "shacl_conforms": bool(conforms), "shacl_report": str(out_dir / 'shacl_report.txt')}, indent=2))
    if warnings:
        print("\nFirst warnings:")
        for w in warnings[:15]: print("-", w)

if __name__ == "__main__":
    main()
