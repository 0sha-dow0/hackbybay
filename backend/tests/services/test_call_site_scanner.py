"""Tests for Unit 15: call-site scanner (binding-aware, aliased imports).

Binds to the coder's actual API and traced behavior in
``backend/services/call_site_scanner.py``:

* ``scan_call_sites(files, target_package) -> Result[tuple[CallSite, ...], IngestError]``.
* Result is always ``Ok(...)`` for text input (the regex/lexer scanner never
  crashes on one malformed file and never returns ``Err`` in the current
  implementation).
* Output is sorted by ``(file_path, line, symbol)`` and deduped on that key.
* Binding-aware: ``axios`` appearing only in a comment / string literal (with no
  import binding) is NOT a call site.
* Import sites are emitted for every matched import/require of the target.
  - Aliased require (``const http = require('axios')``) -> import site
    ``symbol == "http"`` with ``is_aliased=True``, ``alias="http"``; alias usages
    ``http.get(...)`` / ``http.post(...)`` -> ``symbol == "http.get"`` /
    ``"http.post"`` with the same alias flags, even though the usage lines do not
    contain the literal ``"axios"``.
  - Direct ESM default import (``import axios from 'axios'``) -> import site
    ``symbol == "axios"`` with ``is_aliased=False``, ``alias=None``; usage
    ``axios.get(...)`` -> ``symbol == "axios.get"`` (also not aliased).
* Documented out-of-scope patterns (destructured-member calls) are asserted as
  NOT detected, never asserted as required.
"""

from __future__ import annotations

from backend.domain.errors import Err, IngestError, Ok, Result
from backend.domain.models import CallSite, FileContent
from backend.services.call_site_scanner import scan_call_sites

_TARGET = "axios"


def _fc(path: str, text: str) -> FileContent:
    return FileContent(path=path, text=text)


def _unwrap(
    result: Result[tuple[CallSite, ...], IngestError],
) -> tuple[CallSite, ...]:
    assert isinstance(result, Ok), f"expected Ok, got {result!r}"
    return result.value


def _keys(sites: tuple[CallSite, ...]) -> list[tuple[str, int, str]]:
    return [(s.file_path, s.line, s.symbol) for s in sites]


def _by_symbol(sites: tuple[CallSite, ...], symbol: str) -> CallSite:
    matches = [s for s in sites if s.symbol == symbol]
    assert len(matches) == 1, f"expected exactly one {symbol!r}, got {matches!r}"
    return matches[0]


# ---------------------------------------------------------------------------
# Case 1: THE FLEX — aliased require + alias-usage call sites.
# ---------------------------------------------------------------------------


def test_flex_aliased_require_detects_alias_usages() -> None:
    text = "const http = require('axios');\nhttp.get('/x');\nhttp.post('/y');\n"
    sites = _unwrap(scan_call_sites([_fc("app.js", text)], _TARGET))

    # Alias usages are returned even though usage lines lack the literal "axios".
    assert "axios" not in "http.get('/x');"
    assert "axios" not in "http.post('/y');"

    get = _by_symbol(sites, "http.get")
    post = _by_symbol(sites, "http.post")
    for usage in (get, post):
        assert usage.is_aliased is True, usage
        assert usage.alias == "http", usage
    assert get.line == 2
    assert post.line == 3

    # Import site for the aliased binding.
    import_site = _by_symbol(sites, "http")
    assert import_site.line == 1
    assert import_site.is_aliased is True
    assert import_site.alias == "http"
    assert import_site.snippet == "const http = require('axios');"

    # Full ordered/deduped view.
    assert _keys(sites) == [
        ("app.js", 1, "http"),
        ("app.js", 2, "http.get"),
        ("app.js", 3, "http.post"),
    ]


def test_flex_every_aliased_site_has_alias_invariant() -> None:
    # Invariant (§12.10): every returned CallSite for an alias binding has
    # is_aliased=True and a non-null alias.
    text = "const http = require('axios');\nhttp.get('/x');\n"
    sites = _unwrap(scan_call_sites([_fc("a.js", text)], _TARGET))
    assert len(sites) == 2
    for site in sites:
        assert site.is_aliased is True
        assert site.alias is not None
        assert site.alias == "http"


# ---------------------------------------------------------------------------
# Case 2: direct ESM default import + member call -> not aliased.
# ---------------------------------------------------------------------------


def test_direct_import_member_call_not_aliased() -> None:
    text = "import axios from 'axios';\naxios.get('/data');\n"
    sites = _unwrap(scan_call_sites([_fc("client.js", text)], _TARGET))

    assert _keys(sites) == [
        ("client.js", 1, "axios"),
        ("client.js", 2, "axios.get"),
    ]
    for site in sites:
        assert site.is_aliased is False
        assert site.alias is None


def test_direct_require_default_binding_not_aliased() -> None:
    # Local binding equal to canonical name -> not aliased.
    text = "const axios = require('axios');\naxios('/x');\n"
    sites = _unwrap(scan_call_sites([_fc("r.js", text)], _TARGET))
    assert _keys(sites) == [
        ("r.js", 1, "axios"),
        ("r.js", 2, "axios"),
    ]
    for site in sites:
        assert site.is_aliased is False
        assert site.alias is None


# ---------------------------------------------------------------------------
# Case 3: binding-aware negative — axios only in comment / string / no import.
# ---------------------------------------------------------------------------


def test_axios_in_comment_and_string_only_is_not_a_call_site() -> None:
    text = (
        "// axios is bad\n"
        'const s = "axios";\n'
        "/* axios.get() would be nice */\n"
        "console.log(s);\n"
    )
    sites = _unwrap(scan_call_sites([_fc("noise.js", text)], _TARGET))
    assert sites == ()


def test_require_and_import_inside_comment_or_string_are_ignored() -> None:
    text = (
        "// const http = require('axios')\n"
        "/* import axios from 'axios' */\n"
        "const s = \"require('axios')\";\n"
        "const t = `import axios from 'axios'`;\n"
    )
    sites = _unwrap(scan_call_sites([_fc("masked.js", text)], _TARGET))
    assert sites == ()


def test_alias_usage_inside_string_or_comment_is_masked() -> None:
    # Binding exists, but the alias usages live in a comment / string / template
    # and must be masked out by the lexer; only the real code usage counts.
    text = (
        "const http = require('axios');\n"
        "// http.get('/comment');\n"
        'const s = "http.post(\'/string\')";\n'
        "const t = `http.put('/template')`;\n"
        "http.get('/real');\n"
    )
    sites = _unwrap(scan_call_sites([_fc("mask2.js", text)], _TARGET))
    assert _keys(sites) == [
        ("mask2.js", 1, "http"),
        ("mask2.js", 5, "http.get"),
    ]
    # The masked usages must not appear.
    assert not any(s.line in (2, 3, 4) for s in sites)


# ---------------------------------------------------------------------------
# Case 4: non-target package imports ignored.
# ---------------------------------------------------------------------------


def test_non_target_package_ignored() -> None:
    text = "const _ = require('lodash');\n_.map([], (x) => x);\n"
    sites = _unwrap(scan_call_sites([_fc("lo.js", text)], _TARGET))
    assert sites == ()


def test_target_selected_among_multiple_imports() -> None:
    text = (
        "const _ = require('lodash');\n"
        "const http = require('axios');\n"
        "_.map([], (x) => x);\n"
        "http.get('/x');\n"
    )
    sites = _unwrap(scan_call_sites([_fc("mix.js", text)], _TARGET))
    assert _keys(sites) == [
        ("mix.js", 2, "http"),
        ("mix.js", 4, "http.get"),
    ]


# ---------------------------------------------------------------------------
# Case 5: empty file list, and import-only files still yield the import site.
# ---------------------------------------------------------------------------


def test_empty_file_list_is_ok_empty() -> None:
    result = scan_call_sites([], _TARGET)
    assert isinstance(result, Ok)
    assert result.value == ()


def test_empty_text_file_yields_nothing() -> None:
    sites = _unwrap(scan_call_sites([_fc("blank.js", "")], _TARGET))
    assert sites == ()


def test_import_without_calls_still_yields_import_site() -> None:
    text = "import axios from 'axios';\n"
    sites = _unwrap(scan_call_sites([_fc("imp.js", text)], _TARGET))
    assert _keys(sites) == [("imp.js", 1, "axios")]
    assert sites[0].is_aliased is False
    assert sites[0].alias is None


def test_aliased_require_without_calls_still_yields_import_site() -> None:
    text = "const http = require('axios');\n"
    sites = _unwrap(scan_call_sites([_fc("imp2.js", text)], _TARGET))
    assert _keys(sites) == [("imp2.js", 1, "http")]
    assert sites[0].is_aliased is True
    assert sites[0].alias == "http"


def test_bare_import_and_standalone_require_yield_canonical_import_site() -> None:
    bare = _unwrap(scan_call_sites([_fc("bare.js", "import 'axios';\n")], _TARGET))
    assert _keys(bare) == [("bare.js", 1, "axios")]
    assert bare[0].is_aliased is False and bare[0].alias is None

    standalone = _unwrap(
        scan_call_sites([_fc("st.js", "require('axios');\n")], _TARGET)
    )
    assert _keys(standalone) == [("st.js", 1, "axios")]
    assert standalone[0].is_aliased is False and standalone[0].alias is None


# ---------------------------------------------------------------------------
# Case 6: determinism, ordering, and dedup.
# ---------------------------------------------------------------------------


def test_output_sorted_across_files_lines_and_symbols() -> None:
    file_b = _fc(
        "b.js",
        "const http = require('axios');\nhttp.post('/p');\nhttp.get('/g');\n",
    )
    file_a = _fc(
        "a.js",
        "import axios from 'axios';\naxios.get('/g');\n",
    )
    # Provide files out of sorted order.
    sites = _unwrap(scan_call_sites([file_b, file_a], _TARGET))
    assert _keys(sites) == [
        ("a.js", 1, "axios"),
        ("a.js", 2, "axios.get"),
        ("b.js", 1, "http"),
        ("b.js", 2, "http.post"),
        ("b.js", 3, "http.get"),
    ]
    # Explicitly verify the sort key ordering is non-decreasing.
    keys = _keys(sites)
    assert keys == sorted(keys)


def test_duplicate_symbol_same_line_is_deduped() -> None:
    text = "const http = require('axios');\nhttp.get('/a'); http.get('/b');\n"
    sites = _unwrap(scan_call_sites([_fc("dup.js", text)], _TARGET))
    assert _keys(sites) == [
        ("dup.js", 1, "http"),
        ("dup.js", 2, "http.get"),
    ]
    # No duplicate (file_path, line, symbol) keys.
    keys = _keys(sites)
    assert len(keys) == len(set(keys))


def test_determinism_repeated_runs_identical() -> None:
    files = [
        _fc("z.js", "const http = require('axios');\nhttp.get('/g');\n"),
        _fc("a.js", "import axios from 'axios';\naxios.post('/p');\n"),
    ]
    first = _unwrap(scan_call_sites(files, _TARGET))
    second = _unwrap(scan_call_sites(files, _TARGET))
    assert first == second
    assert _keys(first) == _keys(second)


# ---------------------------------------------------------------------------
# Case 7: malformed file alongside a good file — no crash, good file kept.
# ---------------------------------------------------------------------------


def test_malformed_file_does_not_crash_or_drop_good_file() -> None:
    malformed = _fc(
        "malformed.js",
        (
            "const a = `template ${ unterminated\n"
            "function weird({[(\n"
            "require('axios'\n"
            'const b = "unterminated\n'
        ),
    )
    good = _fc(
        "good.js",
        "import axios from 'axios';\naxios.get('/ok');\n",
    )
    result = scan_call_sites([malformed, good], _TARGET)
    assert isinstance(result, Ok)
    sites = result.value
    # Good file's call sites are preserved.
    assert ("good.js", 1, "axios") in _keys(sites)
    assert ("good.js", 2, "axios.get") in _keys(sites)
    # The malformed file contributed no partial/garbage site.
    assert all(s.file_path == "good.js" for s in sites)


def test_malformed_file_alone_returns_ok_empty() -> None:
    malformed = _fc("only.js", "`${{{ \n /* unclosed comment \n require('axios'")
    result = scan_call_sites([malformed], _TARGET)
    assert isinstance(result, Ok)
    assert result.value == ()


# ---------------------------------------------------------------------------
# Documented out-of-scope: destructured-member calls are NOT detected.
# (Asserting the out-of-scope pattern is absent, not asserting it as required.)
# ---------------------------------------------------------------------------


def test_named_import_yields_only_canonical_import_site() -> None:
    text = "import { get, post } from 'axios';\nget('/x');\npost('/y');\n"
    sites = _unwrap(scan_call_sites([_fc("named.js", text)], _TARGET))
    # Only the import site (canonical symbol); destructured-member calls are
    # documented out-of-scope and must not be reported.
    assert _keys(sites) == [("named.js", 1, "axios")]
    assert sites[0].is_aliased is False
    assert sites[0].alias is None
    assert all(s.symbol not in {"get", "post"} for s in sites)


# ---------------------------------------------------------------------------
# Structural / typing conformance of the public signature.
# ---------------------------------------------------------------------------


def test_signature_returns_result_type() -> None:
    typed: Result[tuple[CallSite, ...], IngestError] = scan_call_sites([], _TARGET)
    # Both arms of the Result union are importable and usable.
    if isinstance(typed, Err):
        assert isinstance(typed.error, IngestError)
    else:
        assert isinstance(typed, Ok)
        assert isinstance(typed.value, tuple)
