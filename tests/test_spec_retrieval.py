from fluentvibe.authoring.spec_retrieval import retrieval_context, similar_specs

CORPUS = (
    {"name": "ligseq", "family": "ngs-library-prep", "title": "Nanopore Genomic Ligation",
     "description": "ONT ligation sequencing library prep with AMPure XP bead clean-ups",
     "sample_count": 8,
     "steps": [{"op": "add", "location": "deck", "text": "End prep", "volume_ul": 15.0},
               {"op": "bead_cleanup", "location": "deck", "text": "AMPure clean-up",
                "volume_ul": 60.0, "washes": 2, "wash_ul": 150.0, "elute_ul": 61.0}]},
    {"name": "bca", "family": "protein-assay", "title": "BCA protein assay",
     "description": "Standard curve serial dilution and BCA working reagent",
     "steps": [{"op": "serial_dilution", "location": "deck", "text": "Standards"},
               {"op": "incubate", "location": "off_deck", "text": "37 C", "minutes": [30.0]}]},
)


def test_similar_specs_ranks_by_content():
    hits = similar_specs("Rapid barcoding nanopore library, AMPure XP beads, ligation", k=2, corpus=CORPUS)
    assert hits[0][1]["name"] == "ligseq"


def test_retrieval_context_renders_outline_and_warning():
    block = retrieval_context("nanopore ampure ligation", k=1, corpus=CORPUS)
    assert "NOT this protocol" in block
    assert "bead_cleanup, beads 60 µl, washes 2, wash 150 µl, elute 61 µl" in block
    assert "BCA" not in block


def test_no_overlap_gives_nothing():
    assert retrieval_context("zzzz qqqq", corpus=CORPUS) is None
    assert retrieval_context("anything", corpus=()) is None
