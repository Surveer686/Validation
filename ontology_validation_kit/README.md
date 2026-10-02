# JSON ontology → Turtle → SHACL validation

This kit is tailored to `business_entity_ontology.json`, which has top-level `ontology_name`, `source`, `description`, `statistics`, and `businesses`; each business/domain has `business`, `entity_count`, and `entities`.

## Run it

Keep these files together with `business_entity_ontology.json`:

```bash
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
python -m pip install -r requirements.txt
python convert_and_validate.py business_entity_ontology.json
```

Outputs are written to `ontology_validation_output/`:
- `ontology.ttl`: RDF graph serialized as Turtle.
- `json_audit_report.json`: JSON-level structural, count, duplicate, and type warnings.
- `shacl_report.txt` and `shacl_report.ttl`: SHACL validation findings.
- `summary.json`: concise counts and SHACL conformance status.

## How the pieces fit

- **JSON** is the current extraction/export format.
- **RDF** represents facts as graph triples.
- **Turtle (`.ttl`)** is a readable text syntax for an RDF graph.
- **SHACL (`ontology_shapes.ttl`)** declares constraints over that graph. The provided shapes check that each entity has a label, type, and domain; each relationship has exactly one declared source and target; relationship types are text; and directions are valid.

## Important limits

SHACL only tests the constraints that are written. These starter shapes do not prove that an extracted claim is true, that a relation type is semantically appropriate, or that the ontology is complete. Review type ambiguity, duplicate/mirrored relations, domain-crossing edges, and source evidence separately. The converter creates one entity node per exact entity name; the same name with multiple extracted types becomes one RDF node with multiple `ex:entityType` values, which is reported for review rather than automatically treated as an error.
