from typing import Final

from backend.domain.errors import Err, RecordStoreError, Result
from backend.domain.models import Recipe
from backend.ports.record_store import RecordStore

_BLANK_PAIR_MESSAGE: Final[str] = "library_pair must be a non-blank identifier"
_CONTEXT_LIBRARY_PAIR: Final[str] = "library_pair"


def _is_blank(value: str) -> bool:
    return value.strip() == ""


class RecipeMemory:
    def __init__(self, store: RecordStore) -> None:
        self._store: RecordStore = store

    def recall(self, library_pair: str) -> Result[Recipe | None, RecordStoreError]:
        if _is_blank(library_pair):
            return Err(
                RecordStoreError(
                    _BLANK_PAIR_MESSAGE,
                    {_CONTEXT_LIBRARY_PAIR: library_pair},
                )
            )
        return self._store.find_recipe(library_pair)

    def remember(self, recipe: Recipe) -> Result[Recipe, RecordStoreError]:
        if _is_blank(recipe.library_pair):
            return Err(
                RecordStoreError(
                    _BLANK_PAIR_MESSAGE,
                    {_CONTEXT_LIBRARY_PAIR: recipe.library_pair},
                )
            )
        return self._store.upsert_recipe(recipe)


__all__ = ("RecipeMemory",)
