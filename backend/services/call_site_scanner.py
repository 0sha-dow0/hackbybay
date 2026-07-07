import bisect
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum, auto

from backend.domain.errors import IngestError, Ok, Result
from backend.domain.models import CallSite, FileContent

_IDENTIFIER_HEAD = "_$"
_ASSIGNMENT_PRECURSORS = "=!<>+-*/%&|^~"
_IMPORT_TYPE_KEYWORDS = frozenset({"type", "typeof"})

_REQUIRE_RE = re.compile(
    r"(?P<kw>\brequire)\s*\(\s*(?P<q>['\"])(?P<spec>[^'\"]+)(?P=q)\s*\)"
)
_ESM_FROM_RE = re.compile(
    r"(?P<kw>\bimport\b)(?P<clause>[^;\n]*?)\bfrom\b\s*(?P<q>['\"])(?P<spec>[^'\"]+)(?P=q)"
)
_ESM_BARE_RE = re.compile(r"(?P<kw>\bimport\b)\s*(?P<q>['\"])(?P<spec>[^'\"]+)(?P=q)")
_NAMESPACE_RE = re.compile(r"\*\s*as\s+([A-Za-z_$][\w$]*)")
_LEADING_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][\w$]*")


class _LexState(Enum):
    CODE = auto()
    SINGLE_STRING = auto()
    DOUBLE_STRING = auto()
    TEMPLATE = auto()
    LINE_COMMENT = auto()
    BLOCK_COMMENT = auto()


@dataclass
class _Frame:
    state: _LexState
    brace_depth: int


@dataclass(frozen=True)
class _Binding:
    name: str
    is_aliased: bool


def _is_identifier_char(char: str) -> bool:
    return char.isalnum() or char in _IDENTIFIER_HEAD


def _is_identifier_start(char: str) -> bool:
    return char.isalpha() or char in _IDENTIFIER_HEAD


def _step_code(
    text: str,
    is_code: list[bool],
    stack: list[_Frame],
    frame: _Frame,
    index: int,
    char: str,
    following: str,
) -> int:
    if char == "/" and following == "/":
        stack.append(_Frame(_LexState.LINE_COMMENT, 0))
        return index + 2
    if char == "/" and following == "*":
        stack.append(_Frame(_LexState.BLOCK_COMMENT, 0))
        return index + 2
    if char == "'":
        stack.append(_Frame(_LexState.SINGLE_STRING, 0))
        return index + 1
    if char == '"':
        stack.append(_Frame(_LexState.DOUBLE_STRING, 0))
        return index + 1
    if char == "`":
        stack.append(_Frame(_LexState.TEMPLATE, 0))
        return index + 1
    if char == "{":
        frame.brace_depth += 1
        is_code[index] = True
        return index + 1
    if char == "}":
        if len(stack) > 1 and frame.brace_depth == 0:
            stack.pop()
            return index + 1
        if frame.brace_depth > 0:
            frame.brace_depth -= 1
        is_code[index] = True
        return index + 1
    is_code[index] = True
    return index + 1


def _step_quoted(stack: list[_Frame], index: int, char: str, closer: str) -> int:
    if char == "\\":
        return index + 2
    if char == closer or char == "\n":
        stack.pop()
    return index + 1


def _step_template(stack: list[_Frame], index: int, char: str, following: str) -> int:
    if char == "\\":
        return index + 2
    if char == "`":
        stack.pop()
        return index + 1
    if char == "$" and following == "{":
        stack.append(_Frame(_LexState.CODE, 0))
        return index + 2
    return index + 1


def _step_line_comment(stack: list[_Frame], index: int, char: str) -> int:
    if char == "\n":
        stack.pop()
    return index + 1


def _step_block_comment(
    stack: list[_Frame], index: int, char: str, following: str
) -> int:
    if char == "*" and following == "/":
        stack.pop()
        return index + 2
    return index + 1


def _classify(text: str) -> list[bool]:
    length = len(text)
    is_code = [False] * length
    stack: list[_Frame] = [_Frame(_LexState.CODE, 0)]
    index = 0
    while index < length:
        frame = stack[-1]
        char = text[index]
        following = text[index + 1] if index + 1 < length else ""
        if frame.state is _LexState.CODE:
            index = _step_code(text, is_code, stack, frame, index, char, following)
        elif frame.state is _LexState.SINGLE_STRING:
            index = _step_quoted(stack, index, char, "'")
        elif frame.state is _LexState.DOUBLE_STRING:
            index = _step_quoted(stack, index, char, '"')
        elif frame.state is _LexState.TEMPLATE:
            index = _step_template(stack, index, char, following)
        elif frame.state is _LexState.LINE_COMMENT:
            index = _step_line_comment(stack, index, char)
        else:
            index = _step_block_comment(stack, index, char, following)
    return is_code


def _build_code_text(text: str, is_code: list[bool]) -> str:
    parts: list[str] = []
    for index, char in enumerate(text):
        if is_code[index]:
            parts.append(char)
        elif char == "\n":
            parts.append("\n")
        else:
            parts.append(" ")
    return "".join(parts)


def _newline_offsets(text: str) -> list[int]:
    return [index for index, char in enumerate(text) if char == "\n"]


def _line_number(offsets: list[int], offset: int) -> int:
    return bisect.bisect_left(offsets, offset) + 1


def _snippet(lines: list[str], line: int) -> str:
    if 0 <= line - 1 < len(lines):
        return lines[line - 1].strip()
    return ""


def _canonical_name(package: str) -> str:
    if package.startswith("@") and "/" in package:
        return package.rsplit("/", 1)[-1]
    return package


def _require_binding(text: str, is_code: list[bool], keyword_index: int) -> str | None:
    cursor = keyword_index - 1
    while cursor >= 0 and text[cursor] in " \t":
        cursor -= 1
    if cursor < 0 or text[cursor] != "=":
        return None
    if cursor > 0 and text[cursor - 1] in _ASSIGNMENT_PRECURSORS:
        return None
    if cursor + 1 < len(text) and text[cursor + 1] in "=>":
        return None
    cursor -= 1
    while cursor >= 0 and text[cursor] in " \t":
        cursor -= 1
    end = cursor + 1
    while cursor >= 0 and _is_identifier_char(text[cursor]):
        cursor -= 1
    start = cursor + 1
    if start >= end:
        return None
    name = text[start:end]
    if not _is_identifier_start(name[0]) or not is_code[start]:
        return None
    prefix = start - 1
    while prefix >= 0 and text[prefix] in " \t":
        prefix -= 1
    if prefix >= 0 and text[prefix] == ".":
        return None
    return name


def _parse_import_clause(clause: str) -> tuple[str | None, str | None]:
    namespace_match = _NAMESPACE_RE.search(clause)
    namespace: str | None = namespace_match.group(1) if namespace_match else None
    stripped = clause.lstrip()
    default: str | None = None
    if stripped and stripped[0] not in "{*":
        default_match = _LEADING_IDENTIFIER_RE.match(stripped)
        if default_match is not None:
            candidate = default_match.group(0)
            if candidate not in _IMPORT_TYPE_KEYWORDS:
                default = candidate
    return default, namespace


def _import_site(
    path: str, line: int, snippet: str, binding: str | None, canonical: str
) -> CallSite:
    if binding is None:
        return CallSite(
            file_path=path,
            line=line,
            symbol=canonical,
            is_aliased=False,
            alias=None,
            snippet=snippet,
        )
    aliased = binding != canonical
    return CallSite(
        file_path=path,
        line=line,
        symbol=binding,
        is_aliased=aliased,
        alias=binding if aliased else None,
        snippet=snippet,
    )


def _unique_bindings(bindings: list[_Binding]) -> list[_Binding]:
    seen: dict[str, _Binding] = {}
    for binding in bindings:
        if binding.name not in seen:
            seen[binding.name] = binding
    return list(seen.values())


def _call_sites_for_binding(
    path: str,
    code_text: str,
    offsets: list[int],
    lines: list[str],
    binding: _Binding,
) -> list[CallSite]:
    aliased = binding.is_aliased
    alias = binding.name if aliased else None
    escaped = re.escape(binding.name)
    member_pattern = re.compile(
        r"(?<![\w$.])" + escaped + r"\s*\.\s*([A-Za-z_$][\w$]*)\s*\("
    )
    direct_pattern = re.compile(r"(?<![\w$.])" + escaped + r"\s*\(")
    results: list[CallSite] = []
    for match in member_pattern.finditer(code_text):
        member: str = match.group(1)
        line = _line_number(offsets, match.start())
        results.append(
            CallSite(
                file_path=path,
                line=line,
                symbol=f"{binding.name}.{member}",
                is_aliased=aliased,
                alias=alias,
                snippet=_snippet(lines, line),
            )
        )
    for match in direct_pattern.finditer(code_text):
        line = _line_number(offsets, match.start())
        results.append(
            CallSite(
                file_path=path,
                line=line,
                symbol=binding.name,
                is_aliased=aliased,
                alias=alias,
                snippet=_snippet(lines, line),
            )
        )
    return results


def _scan_file(file: FileContent, target_package: str, canonical: str) -> list[CallSite]:
    text = file.text
    is_code = _classify(text)
    code_text = _build_code_text(text, is_code)
    offsets = _newline_offsets(text)
    lines = text.split("\n")
    sites: list[CallSite] = []
    bindings: list[_Binding] = []

    for match in _REQUIRE_RE.finditer(text):
        keyword_index = match.start("kw")
        if not is_code[keyword_index]:
            continue
        specifier: str = match.group("spec")
        if specifier != target_package:
            continue
        binding = _require_binding(text, is_code, keyword_index)
        line = _line_number(offsets, keyword_index)
        snippet = _snippet(lines, line)
        sites.append(_import_site(file.path, line, snippet, binding, canonical))
        if binding is not None:
            bindings.append(_Binding(binding, binding != canonical))

    for match in _ESM_FROM_RE.finditer(text):
        keyword_index = match.start("kw")
        if not is_code[keyword_index]:
            continue
        specifier = match.group("spec")
        if specifier != target_package:
            continue
        default_name, namespace_name = _parse_import_clause(match.group("clause"))
        names = [name for name in (default_name, namespace_name) if name is not None]
        line = _line_number(offsets, keyword_index)
        snippet = _snippet(lines, line)
        primary = names[0] if names else None
        sites.append(_import_site(file.path, line, snippet, primary, canonical))
        for name in names:
            bindings.append(_Binding(name, name != canonical))

    for match in _ESM_BARE_RE.finditer(text):
        keyword_index = match.start("kw")
        if not is_code[keyword_index]:
            continue
        specifier = match.group("spec")
        if specifier != target_package:
            continue
        line = _line_number(offsets, keyword_index)
        snippet = _snippet(lines, line)
        sites.append(_import_site(file.path, line, snippet, None, canonical))

    for binding_entry in _unique_bindings(bindings):
        sites.extend(
            _call_sites_for_binding(file.path, code_text, offsets, lines, binding_entry)
        )

    return sites


def _dedupe_and_sort(sites: list[CallSite]) -> tuple[CallSite, ...]:
    ordered = sorted(sites, key=lambda site: (site.file_path, site.line, site.symbol))
    seen: set[tuple[str, int, str]] = set()
    unique: list[CallSite] = []
    for site in ordered:
        key = (site.file_path, site.line, site.symbol)
        if key in seen:
            continue
        seen.add(key)
        unique.append(site)
    return tuple(unique)


def scan_call_sites(
    files: Sequence[FileContent], target_package: str
) -> Result[tuple[CallSite, ...], IngestError]:
    canonical = _canonical_name(target_package)
    collected: list[CallSite] = []
    for file in files:
        collected.extend(_scan_file(file, target_package, canonical))
    return Ok(_dedupe_and_sort(collected))
