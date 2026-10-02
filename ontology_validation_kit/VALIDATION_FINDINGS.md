# Validation findings for `business_entity_ontology.json`

## What was actually checked in this environment

- JSON parsed successfully.
- The custom JSON audit found **0 structural errors**.
- The file contains **8 domains**, **911 entity occurrences**, **474 distinct entity names**, and **504 distinct (name, type) pairs**.
- The audit normalized relationship direction into source–predicate–target triples: **1,738 relationship occurrences** reduce to **388 distinct triples**.
- The JSON declares **389 unique relationships**, so the declared count differs from the computed normalized count by one. Check how the extraction pipeline defines “unique relationship” before changing the stored statistic.
- **28 entity names have more than one extracted type**. This may be valid polyhierarchy/multi-typing or inconsistent extraction; review each case instead of automatically deleting types.
- There are **1,350 repeated relationship occurrences** after normalizing direction. Many may be repeated across domain views or mirrored incoming/outgoing entries. Deduplicate only after confirming intended semantics.
- Converted JSON to RDF Turtle and re-parsed the Turtle file: both graph parses contain **13,159 triples**.
- The SHACL shapes file parsed successfully and contains **54 RDF triples**.

## What has not been verified yet

The `pyshacl` package could not be installed in this execution environment because package-index network access failed. Therefore **the SHACL constraints have not been executed against the generated graph here**. Run the commands in `README.md` on a machine where `rdflib` and `pyshacl` can be installed. The script will create a SHACL report and conformance result.

Also not automatically proven by these checks:

1. Whether each relationship is factually supported by the source PDF.
2. Whether the relationship verb is semantically appropriate for its source and target.
3. Whether the ontology is complete relative to the source document.
4. Whether ambiguous entity names should be merged or kept separate.
5. OWL reasoning consistency or domain-specific constraints beyond the supplied SHACL shapes.

## Recommended review order

1. Run the script locally with SHACL enabled.
2. Inspect all SHACL violations and decide whether they are real data issues or constraints that need adjustment.
3. Review the 28 multi-typed names, starting with names assigned broad types such as `Concept` plus a more specific type.
4. Inspect repeated normalized triples; preserve legitimate repeats only if they carry domain/provenance meaning. Consider adding source page/evidence metadata if available.
5. Check every extracted relation against its PDF evidence and document a correction log.
6. Add domain-specific SHACL rules only when the intended rule is clear (for example, which relationship types may connect which entity types).
