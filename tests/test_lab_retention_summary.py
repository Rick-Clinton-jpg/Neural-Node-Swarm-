from neural_node_swarm.lab.retention_summary import (
    BenchmarkSummary,
    PairCounts,
    summarize_paired_cases,
)


def test_paired_case_counts():
    assert summarize_paired_cases() == BenchmarkSummary(
        grounded=PairCounts(
            total=2,
            both_pass=1,
            primary_only_pass=0,
            control_only_pass=1,
            both_fail=0,
        ),
        relay=PairCounts(
            total=2,
            both_pass=1,
            primary_only_pass=0,
            control_only_pass=1,
            both_fail=0,
        ),
    )


def test_summary_is_repeatable_and_counts_every_case():
    first = summarize_paired_cases()
    assert first == summarize_paired_cases()
    for counts in (first.grounded, first.relay):
        assert counts.total == (
            counts.both_pass
            + counts.primary_only_pass
            + counts.control_only_pass
            + counts.both_fail
        )
