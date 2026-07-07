"""Tests for backend.services.recipes.RecipeMemory (Unit 29).

Verifies the recipe-memory contract against the injected RecordStore:

* recall on an absent pair is Ok(None) (absence is not an error);
* remember then recall round-trips the stored recipe;
* remember is idempotent by library_pair (last write wins, no duplicate);
* blank/whitespace library_pair on recall and on remember is Err(RecordStoreError);
* recall/remember delegate to the injected store (observed via store state);
* the key is blank-checked but never trimmed;
* recall is side-effect-free and deterministic across repeated calls.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TypeVar

import pytest

from backend.adapters.fake.fake_record_store import FakeRecordStore
from backend.domain.determinism import FixedClock
from backend.domain.errors import Err, Ok, RecordStoreError, Result
from backend.domain.models import Recipe
from backend.services.recipes import RecipeMemory

_T = TypeVar("_T")

_BLANK_PAIRS: tuple[str, ...] = ("", " ", "   ", "\t", "\n", "\r", " \t\n ")
_PAIR: str = "axios->fetch"


def _store() -> FakeRecordStore:
    return FakeRecordStore(FixedClock(datetime(2026, 1, 1, tzinfo=timezone.utc), 1.0))


def _memory() -> tuple[RecipeMemory, FakeRecordStore]:
    store = _store()
    return RecipeMemory(store), store


def _recipe(
    pair: str = _PAIR,
    *,
    recipe_id: str = "recipe-0001",
    confirmed_fix: str = "wrap fetch, throw on non-2xx",
    known_gaps: tuple[str, ...] = (),
) -> Recipe:
    return Recipe(
        id=recipe_id,
        library_pair=pair,
        wrapper_pattern="const res = await fetch(url); if (!res.ok) throw ...",
        known_gaps=known_gaps,
        confirmed_fix=confirmed_fix,
    )


def _ok_value(result: Result[_T, RecordStoreError]) -> _T:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _err(result: Result[_T, RecordStoreError]) -> RecordStoreError:
    assert isinstance(result, Err), f"expected Err, got {result!r}"
    return result.error


def test_recall_unknown_pair_on_empty_store_is_ok_none() -> None:
    memory, _ = _memory()

    result = memory.recall(_PAIR)

    assert isinstance(result, Ok)
    assert result.value is None


def test_remember_then_recall_returns_recipe() -> None:
    memory, _ = _memory()
    recipe = _recipe()

    remembered = memory.remember(recipe)
    recalled = memory.recall(recipe.library_pair)

    assert _ok_value(remembered) == recipe
    assert _ok_value(recalled) == recipe


def test_remember_returns_the_stored_recipe_unchanged() -> None:
    memory, _ = _memory()
    recipe = _recipe()

    remembered = memory.remember(recipe)

    assert _ok_value(remembered) is recipe


def test_remember_twice_same_pair_different_content_recalls_latest() -> None:
    memory, _ = _memory()
    first = _recipe(recipe_id="recipe-0001", confirmed_fix="old fix", known_gaps=("g1",))
    second = _recipe(recipe_id="recipe-0002", confirmed_fix="new fix", known_gaps=("g2",))
    assert first != second and first.library_pair == second.library_pair

    memory.remember(first)
    memory.remember(second)

    recalled = _ok_value(memory.recall(_PAIR))
    assert recalled == second
    assert recalled != first


def test_remember_twice_same_pair_is_idempotent_no_duplicate() -> None:
    memory, store = _memory()
    recipe = _recipe()

    first = memory.remember(recipe)
    second = memory.remember(recipe)

    assert _ok_value(first) == recipe
    assert _ok_value(second) == recipe
    assert _ok_value(store.find_recipe(_PAIR)) == recipe


@pytest.mark.parametrize("pair", _BLANK_PAIRS)
def test_recall_blank_pair_is_err(pair: str) -> None:
    memory, _ = _memory()

    result = memory.recall(pair)

    error = _err(result)
    assert isinstance(error, RecordStoreError)


@pytest.mark.parametrize("pair", _BLANK_PAIRS)
def test_remember_blank_pair_is_err(pair: str) -> None:
    memory, _ = _memory()

    result = memory.remember(_recipe(pair=pair))

    error = _err(result)
    assert isinstance(error, RecordStoreError)


def test_recall_blank_err_carries_offending_pair_in_context() -> None:
    memory, _ = _memory()

    error = _err(memory.recall("   "))

    assert error.message != ""
    assert error.context.get("library_pair") == "   "


def test_remember_blank_err_carries_offending_pair_in_context() -> None:
    memory, _ = _memory()

    error = _err(memory.remember(_recipe(pair="")))

    assert error.message != ""
    assert error.context.get("library_pair") == ""


def test_recall_blank_pair_does_not_touch_store() -> None:
    memory, store = _memory()
    stored = _recipe()
    store.upsert_recipe(stored)

    memory.recall("   ")

    assert _ok_value(store.find_recipe(_PAIR)) == stored


def test_remember_blank_pair_persists_nothing() -> None:
    memory, store = _memory()

    memory.remember(_recipe(pair="  "))

    assert _ok_value(store.find_recipe("  ")) is None
    assert _ok_value(store.find_recipe("")) is None


def test_recall_delegates_to_store_find_recipe() -> None:
    memory, store = _memory()
    recipe = _recipe()
    store.upsert_recipe(recipe)

    recalled = memory.recall(_PAIR)

    assert _ok_value(recalled) is recipe


def test_remember_delegates_to_store_upsert_recipe() -> None:
    memory, store = _memory()
    recipe = _recipe()

    memory.remember(recipe)

    assert _ok_value(store.find_recipe(_PAIR)) is recipe


def test_key_is_not_trimmed_padded_recall_is_unknown() -> None:
    memory, _ = _memory()
    memory.remember(_recipe(pair=_PAIR))

    padded = memory.recall(f" {_PAIR} ")

    assert isinstance(padded, Ok)
    assert padded.value is None


def test_key_is_not_trimmed_padded_key_round_trips_verbatim() -> None:
    memory, _ = _memory()
    padded_pair = " react->preact "
    recipe = _recipe(pair=padded_pair)

    memory.remember(recipe)

    assert _ok_value(memory.recall(padded_pair)) == recipe
    assert _ok_value(memory.recall("react->preact")) is None


def test_recall_isolates_distinct_pairs() -> None:
    memory, _ = _memory()
    memory.remember(_recipe(pair="a->b", recipe_id="recipe-a"))

    other = memory.recall("c->d")

    assert isinstance(other, Ok)
    assert other.value is None


def test_recall_is_side_effect_free_and_repeatable_for_unknown() -> None:
    memory, store = _memory()

    first = memory.recall(_PAIR)
    second = memory.recall(_PAIR)
    third = memory.recall(_PAIR)

    assert first == second == third == Ok(None)
    assert _ok_value(store.find_recipe(_PAIR)) is None


def test_recall_is_repeatable_for_known() -> None:
    memory, _ = _memory()
    recipe = _recipe()
    memory.remember(recipe)

    results = [memory.recall(_PAIR) for _ in range(5)]

    assert all(r == Ok(recipe) for r in results)


def test_two_independent_memories_are_deterministic() -> None:
    recipe = _recipe()
    memory_a, _ = _memory()
    memory_b, _ = _memory()

    memory_a.remember(recipe)
    memory_b.remember(recipe)

    assert memory_a.recall(_PAIR) == memory_b.recall(_PAIR)


def test_non_ascii_and_long_pair_round_trip() -> None:
    memory, _ = _memory()
    weird_pair = "axios→fetch-" + "x" * 10000
    recipe = _recipe(pair=weird_pair, recipe_id="recipe-unicode")

    memory.remember(recipe)

    assert _ok_value(memory.recall(weird_pair)) == recipe
