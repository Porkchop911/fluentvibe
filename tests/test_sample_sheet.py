"""Per-sample normalisation from a sample sheet (CSV or chat)."""

from fluentvibe.authoring.sample_sheet import normalisation, parse_concentrations, parse_sample_sheet


def test_sheets_in_common_shapes():
    assert parse_sample_sheet("Well,Sample,Conc (ng/ul)\nA1,s1,45\nB1,s2,10\n") == {"A1": 45.0, "B1": 10.0}
    # semicolons, decimal commas, sample numbers counted column-major (9 = A2)
    assert parse_sample_sheet("Sample;Qubit ng/µl\n1;100\n9;60,5\n") == {"A1": 100.0, "A2": 60.5}
    assert parse_sample_sheet("A1\t30\nB1\t80\n") == {"A1": 30.0, "B1": 80.0}
    assert parse_concentrations("A1 45, A2: 30.5, B1=12 and H12 7") == {"A1": 45.0, "A2": 30.5, "B1": 12.0,
                                                                        "H12": 7.0}


def test_volumes_follow_the_ont_table():
    # 50 ng in 9 ul: 45 ng/ul -> 1.11 + 7.89; 4.5 ng/ul -> all 9 ul; 100 ng/ul -> 2 + 34 ul, then 9 ul of that
    plan = normalisation({"A1": 45, "C1": 4.5, "D1": 100}, 50, 9)
    assert plan.sample_ul == {"A1": 1.11, "C1": 9} and plan.diluent_ul == {"A1": 7.89}
    assert plan.predilute == {"D1": (2.0, 34.0)} and not plan.problems
