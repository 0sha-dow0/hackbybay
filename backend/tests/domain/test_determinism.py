"""Tests for backend.domain.determinism (Unit 5: determinism primitives).

Covers the FixedClock/SystemClock/SequentialIdGenerator contract: exact and
reproducible clock sequences, frozen (zero-step) clocks, strictly monotonic
per-prefix id counters, UTC-aware system time, precondition guards, and
structural conformance to the Clock / IdGenerator protocols.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.domain.determinism import (
    Clock,
    FixedClock,
    IdGenerator,
    SequentialIdGenerator,
    SystemClock,
)

UTC_START: datetime = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


# --- Structural-typing probes (acceptance criterion 7) ---------------------
# The annotated call-site parameters below force mypy to prove that each
# concrete type satisfies the corresponding Protocol structurally.


def _read_clock(clock: Clock) -> datetime:
    return clock.now()


def _issue_id(generator: IdGenerator, prefix: str) -> str:
    return generator.new_id(prefix)


def test_system_clock_is_structural_clock() -> None:
    clock: Clock = SystemClock()
    assert isinstance(_read_clock(clock), datetime)


def test_fixed_clock_is_structural_clock() -> None:
    clock: Clock = FixedClock(UTC_START, 1.0)
    assert _read_clock(clock) == UTC_START


def test_sequential_generator_is_structural_id_generator() -> None:
    generator: IdGenerator = SequentialIdGenerator()
    assert _issue_id(generator, "p") == "p-00000000"


# --- Criterion 1: identical + exact FixedClock sequences -------------------


def test_fixed_clock_two_instances_identical_and_exact() -> None:
    step_s = 0.25
    step = timedelta(seconds=step_s)
    left = FixedClock(UTC_START, step_s)
    right = FixedClock(UTC_START, step_s)
    for k in range(200):
        got_left = left.now()
        got_right = right.now()
        expected = UTC_START + step * k
        assert got_left == expected, f"left call {k}: {got_left} != {expected}"
        assert got_right == expected, f"right call {k}: {got_right} != {expected}"
        assert got_left == got_right, f"call {k}: {got_left} != {got_right}"


def test_fixed_clock_first_call_returns_start() -> None:
    start = datetime(1999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    clock = FixedClock(start, 3.0)
    assert clock.now() == start


def test_fixed_clock_no_drift_over_many_calls() -> None:
    step_s = 0.1  # not exactly representable; exercises accumulation exactness
    step = timedelta(seconds=step_s)
    clock = FixedClock(UTC_START, step_s)
    for k in range(10_000):
        got = clock.now()
        expected = UTC_START + step * k
        assert got == expected, f"call {k}: {got} != {expected}"


def test_fixed_clock_accepts_non_utc_aware_start() -> None:
    tz = timezone(timedelta(hours=5, minutes=30))
    start = datetime(2020, 1, 1, tzinfo=tz)
    clock = FixedClock(start, 2.0)
    assert clock.now() == start
    assert clock.now() == start + timedelta(seconds=2.0)


# --- Criterion 2: zero-step FixedClock is frozen ---------------------------


def test_fixed_clock_zero_step_is_frozen() -> None:
    clock = FixedClock(UTC_START, 0.0)
    for _ in range(500):
        assert clock.now() == UTC_START


def test_fixed_clock_accepts_negative_zero_step() -> None:
    clock = FixedClock(UTC_START, -0.0)
    assert clock.now() == UTC_START
    assert clock.now() == UTC_START


# --- Criterion 3: SequentialIdGenerator single-prefix behaviour ------------


def test_sequential_ids_unique_and_increasing_single_prefix() -> None:
    generator = SequentialIdGenerator()
    prefix = "tx"
    seen: set[str] = set()
    previous = -1
    for _ in range(10_000):
        new_id = generator.new_id(prefix)
        assert new_id not in seen, f"duplicate id {new_id!r}"
        seen.add(new_id)
        head, sep, tail = new_id.partition("-")
        assert sep == "-", f"missing separator in {new_id!r}"
        assert head == prefix, f"head {head!r} != prefix {prefix!r}"
        assert len(tail) >= 8, f"counter {tail!r} narrower than 8 digits"
        counter = int(tail)
        assert counter == previous + 1, (
            f"counter {counter} not strictly following {previous}"
        )
        previous = counter
    assert len(seen) == 10_000


def test_sequential_first_id_with_default_seed() -> None:
    assert SequentialIdGenerator().new_id("job") == "job-00000000"
    assert SequentialIdGenerator(seed=0).new_id("job") == "job-00000000"


def test_sequential_reproducible_across_instances() -> None:
    seed = 42
    first = SequentialIdGenerator(seed=seed)
    second = SequentialIdGenerator(seed=seed)
    seq_first: list[str] = [first.new_id("a") for _ in range(1_000)]
    seq_second: list[str] = [second.new_id("a") for _ in range(1_000)]
    assert seq_first == seq_second
    assert seq_first[0] == f"a-{seed:08d}"


def test_sequential_padding_is_minimum_width_not_truncation() -> None:
    generator = SequentialIdGenerator(seed=99_999_999)
    assert generator.new_id("p") == "p-99999999"  # exactly 8 digits
    assert generator.new_id("p") == "p-100000000"  # 9 digits, never truncated


# --- Criterion 4: per-prefix independence ----------------------------------


def test_per_prefix_counters_are_independent() -> None:
    generator = SequentialIdGenerator()
    assert generator.new_id("a") == "a-00000000"
    assert generator.new_id("b") == "b-00000000"
    assert generator.new_id("a") == "a-00000001"
    assert generator.new_id("b") == "b-00000001"


def test_cross_prefix_no_collision() -> None:
    generator = SequentialIdGenerator()
    ids: set[str] = set()
    for _ in range(1_000):
        for prefix in ("a", "b", "c"):
            new_id = generator.new_id(prefix)
            assert new_id not in ids, f"collision on {new_id!r}"
            ids.add(new_id)
    assert len(ids) == 3_000


# --- Criterion 5: SystemClock is UTC-aware and non-decreasing --------------


def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_system_clock_successive_calls_non_decreasing() -> None:
    clock = SystemClock()
    previous = clock.now()
    for _ in range(100):
        current = clock.now()
        assert current >= previous, f"{current} < {previous}"
        previous = current


# --- Criterion 6: precondition guards raise ValueError ---------------------


def test_fixed_clock_rejects_naive_start() -> None:
    with pytest.raises(ValueError):
        FixedClock(datetime(2020, 1, 1), 1.0)


def test_fixed_clock_rejects_negative_step() -> None:
    with pytest.raises(ValueError):
        FixedClock(UTC_START, -1.0)


def test_fixed_clock_rejects_nan_step() -> None:
    with pytest.raises(ValueError):
        FixedClock(UTC_START, float("nan"))


def test_fixed_clock_rejects_positive_inf_step() -> None:
    with pytest.raises(ValueError):
        FixedClock(UTC_START, float("inf"))


def test_fixed_clock_rejects_negative_inf_step() -> None:
    with pytest.raises(ValueError):
        FixedClock(UTC_START, float("-inf"))


def test_sequential_rejects_empty_prefix() -> None:
    with pytest.raises(ValueError):
        SequentialIdGenerator().new_id("")


def test_sequential_empty_prefix_does_not_disturb_other_counters() -> None:
    generator = SequentialIdGenerator()
    assert generator.new_id("a") == "a-00000000"
    with pytest.raises(ValueError):
        generator.new_id("")
    # The failed call must not consume or reset the "a" counter.
    assert generator.new_id("a") == "a-00000001"
