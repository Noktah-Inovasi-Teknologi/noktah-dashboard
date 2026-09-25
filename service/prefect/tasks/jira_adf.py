"""
Atlassian Document Format (ADF) sanitisation for Jira issue payloads.

Why this module exists
----------------------
Jira's `description` is an ADF document, not a string, and its text nodes are
validated far more strictly than a spreadsheet cell. Two rules break content
plans routinely, and both are invisible to a human reviewing the sheet:

1. **A text node cannot contain a newline.** `Shoot Guide` / `Caption` cells are
   authored as multi-line text, so pasting the cell straight into
   `{"type": "text", "text": ...}` produces a document Jira refuses. Line breaks
   must be expressed as sibling `hardBreak` nodes.
2. **A text node cannot be empty.** `{"type": "text", "text": ""}` is rejected --
   which is exactly what an unfilled column produces.

The Jira bulk-create endpoint reports this as an opaque `INVALID_INPUT` against
the issue, so a single stray newline silently costs one content item. Telling a
content planner to "watch the formatting" cannot fix it: the sheet is a text
editor, and invisible characters (NBSP from a copy-paste, U+2028 from Docs, a
zero-width space from a browser) do not show up. This module fixes it at the
boundary instead.

Everything here is a **pure function** -- no Prefect task, no I/O -- so it can be
unit-tested directly and reused by any producer of Jira payloads.
"""
from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any, Dict, List, Optional

# Jira's own limits. Exceeding either is a rejection, not a truncation.
MAX_SUMMARY_CHARS = 255
MAX_DOCUMENT_CHARS = 32_000
ELLIPSIS = "\u2026"

# --- character classes that break ADF, JSON, or Jira rendering ---------------
#
# Written as codepoint ranges rather than literal characters on purpose: every
# character in this section is invisible in an editor, so a literal one here
# would be unreviewable and impossible to edit safely.


def _char_class(*ranges):
    """Compile a regex character class from (first, last) codepoint pairs."""
    return re.compile("[" + "".join(
        chr(lo) if lo == hi else "%s-%s" % (chr(lo), chr(hi)) for lo, hi in ranges
    ) + "]")


# Every separator Google Sheets / Docs / Word can emit, normalised to "\n".
# U+2028/U+2029 are the nastiest: they survive a `.strip()`, look like nothing,
# and are still newlines to Jira's validator.
_LINE_BREAKS = re.compile("\r\n|" + _char_class(
    (0x0B, 0x0D),    # VT, FF, CR
    (0x85, 0x85),    # NEL
    (0x2028, 0x2029),  # LINE SEPARATOR, PARAGRAPH SEPARATOR
).pattern)

# Zero-width and bidi controls: invisible, carried in by copy-paste, and enough
# on their own to make a "blank" cell non-empty (so it is neither dropped as
# empty nor renderable as text).
_ZERO_WIDTH = _char_class(
    (0x200B, 0x200F),  # ZWSP, ZWNJ, ZWJ, LRM, RLM
    (0x202A, 0x202E),  # bidi embedding/override
    (0x2060, 0x2064),  # word joiner, invisible operators
    (0x2066, 0x2069),  # bidi isolates
    (0xFEFF, 0xFEFF),  # BOM / zero-width no-break space
)

# Exotic spaces (NBSP first -- the most common) folded to a plain space.
_UNICODE_SPACE = _char_class(
    (0x00A0, 0x00A0),  # NBSP
    (0x1680, 0x1680),
    (0x2000, 0x200A),  # en/em/thin/hair spaces
    (0x202F, 0x202F),  # narrow NBSP
    (0x205F, 0x205F),
    (0x3000, 0x3000),  # ideographic space
)

# C0/C1 controls, minus the "\n" and "\t" handled above.
_CONTROL = _char_class((0x00, 0x08), (0x0E, 0x1F), (0x7F, 0x9F))

# Lone surrogates survive some sheet exports and break JSON encoding outright.
_SURROGATE = _char_class((0xD800, 0xDFFF))

# Only space *before a newline* is removed here. Trimming the ends of the whole
# string is `clean_text`'s job, so that a deliberate trailing space -- the one in
# a bold "Label: " run, which is what separates it from the value beside it --
# survives the repair pass.
_LINE_TRAILING_SPACE = re.compile(r"[ \t]+(?=\n)")
_BLANK_RUN = re.compile(r"\n{3,}")
_SPACE_RUN = re.compile(r" {2,}")

_ANY_SPACE_RUN = re.compile(r"\s+")

# Block nodes that ADF requires to be non-empty. A `paragraph` is deliberately
# NOT here: an empty paragraph is valid ADF, and is how a blank line is spelled.
_REQUIRES_CONTENT = frozenset({
    "heading", "bulletList", "orderedList", "listItem", "blockquote",
    "panel", "table", "tableRow", "tableCell", "tableHeader", "mediaGroup",
})


# =============================================================================
# TEXT CLEANING
# =============================================================================

def scrub(value: Any) -> str:
    """
    Normalise a raw cell value into text that is safe inside an ADF text node,
    apart from newlines (which the node builders turn into `hardBreak`s).

    Leading/trailing whitespace is preserved so a label like "Caption: " keeps
    its trailing space; use `clean_text` for field values.
    """
    if value is None:
        return ""
    # pandas leaves NaN in empty numeric-looking cells, and str(nan) == "nan".
    if isinstance(value, float) and value != value:
        return ""

    text = value if isinstance(value, str) else str(value)

    text = _SURROGATE.sub("", text)          # before normalize(): NFC can raise on these
    text = unicodedata.normalize("NFC", text)
    text = _LINE_BREAKS.sub("\n", text)
    text = _ZERO_WIDTH.sub("", text)
    text = _UNICODE_SPACE.sub(" ", text)
    text = text.replace("\t", " ")
    text = _CONTROL.sub("", text)
    text = _SPACE_RUN.sub(" ", text)
    text = _LINE_TRAILING_SPACE.sub("", text)
    text = _BLANK_RUN.sub("\n\n", text)
    return text


def clean_text(value: Any) -> str:
    """`scrub` plus outer trimming -- the form used for field values."""
    return scrub(value).strip()


def truncate(text: str, limit: int) -> str:
    """Cut to `limit` characters including an ellipsis, on a word boundary if one is near."""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[: limit - 1].rstrip()
    space = cut.rfind(" ")
    if space > limit * 0.6:  # only snap to a word boundary when it is not a drastic cut
        cut = cut[:space].rstrip()
    return cut + ELLIPSIS


def clean_summary(value: Any, max_chars: int = MAX_SUMMARY_CHARS) -> str:
    """
    Flatten a value into a Jira summary: one line, no runs of whitespace,
    truncated to Jira's 255-character limit.

    A summary containing a newline is rejected outright, and one over the limit
    fails with "Summary must be less than 255 characters" -- both of which the
    `Topik` column can produce.
    """
    text = _ANY_SPACE_RUN.sub(" ", scrub(value)).strip()
    return truncate(text, max_chars)


# =============================================================================
# NODE BUILDERS
# =============================================================================

def text_node(text: str, marks: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    node: Dict[str, Any] = {"type": "text", "text": text}
    if marks:
        node["marks"] = marks
    return node


def inline_nodes(
    value: Any,
    marks: Optional[List[Dict[str, Any]]] = None,
    strip: bool = True,
) -> List[Dict[str, Any]]:
    """
    Turn a value into inline ADF nodes: text runs separated by `hardBreak`.

    Returns `[]` for an empty value -- never a `{"text": ""}` node.
    """
    text = clean_text(value) if strip else scrub(value)
    if not text:
        return []

    nodes: List[Dict[str, Any]] = []
    for index, line in enumerate(text.split("\n")):
        if index:
            nodes.append({"type": "hardBreak"})
        if line:
            nodes.append(text_node(line, marks))
    return nodes


def paragraph(content: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    return {"type": "paragraph", "content": list(content or [])}


def paragraphs_from_text(value: Any) -> List[Dict[str, Any]]:
    """
    Render multi-line text as one paragraph per blank-line-separated block, with
    `hardBreak`s between the lines inside a block. Empty input yields no
    paragraphs at all.
    """
    text = clean_text(value)
    if not text:
        return []
    return [paragraph(inline_nodes(block)) for block in text.split("\n\n") if clean_text(block)]


def labelled_paragraph(label: str, value: Any) -> Dict[str, Any]:
    """`**Label:** value` on one paragraph; the label alone when the value is empty."""
    content = [text_node(f"{clean_text(label)}: ", marks=[{"type": "strong"}])]
    content.extend(inline_nodes(value))
    return paragraph(content)


def labelled_block(label: str, value: Any) -> List[Dict[str, Any]]:
    """A bold label paragraph followed by the value's own paragraphs (multi-line fields)."""
    blocks = [paragraph([text_node(f"{clean_text(label)}: ", marks=[{"type": "strong"}])])]
    blocks.extend(paragraphs_from_text(value))
    return blocks


def document(blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Wrap block nodes in an ADF doc, sanitised."""
    return sanitize_document({"type": "doc", "version": 1, "content": list(blocks)})


# =============================================================================
# REPAIR PASS (for documents this module did not build)
# =============================================================================

def sanitize_nodes(node: Any) -> List[Dict[str, Any]]:
    """
    Repair one node, returning zero or more replacements.

    A text node carrying newlines expands into several siblings, which is why
    this returns a list rather than a node.
    """
    if not isinstance(node, dict):
        return []

    node_type = node.get("type")
    if not isinstance(node_type, str) or not node_type:
        return []

    if node_type == "text":
        marks = node.get("marks")
        marks = (
            [m for m in marks if isinstance(m, dict) and m.get("type")]
            if isinstance(marks, list) else None
        )
        # strip=False: a deliberate "Label: " trailing space must survive.
        return inline_nodes(node.get("text"), marks=marks, strip=False)

    repaired = {k: v for k, v in node.items() if k != "content"}
    if "content" in node:
        children: List[Dict[str, Any]] = []
        for child in node.get("content") or []:
            children.extend(sanitize_nodes(child))
        # A hardBreak at either edge of a block renders as a stray blank line.
        while children and children[0].get("type") == "hardBreak":
            children.pop(0)
        while children and children[-1].get("type") == "hardBreak":
            children.pop()
        if not children and node_type in _REQUIRES_CONTENT:
            return []
        repaired["content"] = children

    return [repaired]


def sanitize_document(doc: Any) -> Dict[str, Any]:
    """
    Make any ADF document safe to send: no newline-bearing text nodes, no empty
    text nodes, no empty nodes that require content, within Jira's size limit.

    Idempotent -- running it over an already-clean document changes nothing.
    """
    if not isinstance(doc, dict) or doc.get("type") != "doc":
        return {"type": "doc", "version": 1, "content": [paragraph()]}

    content: List[Dict[str, Any]] = []
    for block in doc.get("content") or []:
        content.extend(sanitize_nodes(block))

    _apply_budget(content, MAX_DOCUMENT_CHARS)

    # An ADF doc needs at least one block node, and `content` may be empty here.
    return {"type": "doc", "version": 1, "content": content or [paragraph()]}


def _apply_budget(nodes: List[Dict[str, Any]], budget: int) -> int:
    """Truncate the document in place once its cumulative text exceeds `budget`."""
    for index, node in enumerate(list(nodes)):
        if budget <= 0:
            del nodes[index:]
            return 0
        if node.get("type") == "text":
            text = node["text"]
            if len(text) > budget:
                node["text"] = truncate(text, budget)
                del nodes[index + 1:]
                return 0
            budget -= len(text)
        elif isinstance(node.get("content"), list):
            budget = _apply_budget(node["content"], budget)
    return budget


# =============================================================================
# ISSUE-LEVEL CLEANING
# =============================================================================

def sanitize_issue(issue: Dict[str, Any]) -> Dict[str, Any]:
    """
    Clean a whole `{"fields": {...}}` Jira issue payload: the summary, the ADF
    description, and any other plain-string field value.

    Returns a new object; the input is not mutated. Safe on payloads produced
    before this module existed (e.g. JSON files on disk from an earlier run).
    """
    if not isinstance(issue, dict):
        return issue

    cleaned = copy.deepcopy(issue)
    fields = cleaned.get("fields")
    if not isinstance(fields, dict):
        return cleaned

    if "summary" in fields:
        fields["summary"] = clean_summary(fields.get("summary"))

    description = fields.get("description")
    if isinstance(description, dict):
        fields["description"] = sanitize_document(description)
    elif isinstance(description, str):
        # A plain string in an ADF field is rejected; promote it to a document.
        fields["description"] = document(paragraphs_from_text(description))

    for key, value in fields.items():
        if key not in ("summary", "description") and isinstance(value, str):
            fields[key] = clean_text(value)

    return cleaned


# =============================================================================
# VALIDATION (report, do not repair)
# =============================================================================

def find_adf_problems(node: Any, path: str = "description") -> List[str]:
    """
    List everything Jira would reject in a document, as human-readable strings.

    Used by the validate-only path so a run says *which* row is malformed and
    why, instead of surfacing Jira's opaque `INVALID_INPUT`.
    """
    problems: List[str] = []

    if not isinstance(node, dict):
        return [f"{path}: expected an object, got {type(node).__name__}"]

    node_type = node.get("type")
    if not node_type:
        return [f"{path}: node has no 'type'"]

    if node_type == "text":
        text = node.get("text")
        if not isinstance(text, str):
            return [f"{path}: text node 'text' must be a string"]
        if text == "":
            problems.append(f"{path}: empty text node (Jira requires at least one character)")
        if "\n" in text or _LINE_BREAKS.search(text):
            problems.append(f"{path}: text node contains a line break (use a hardBreak node)")
        if _CONTROL.search(text):
            problems.append(f"{path}: text node contains a control character")
        if _SURROGATE.search(text):
            problems.append(f"{path}: text node contains an unpaired surrogate")
        return problems

    children = node.get("content")
    if children is None:
        if node_type in _REQUIRES_CONTENT:
            problems.append(f"{path}: '{node_type}' requires content")
        return problems

    if not isinstance(children, list):
        return [f"{path}: 'content' must be a list"]

    if not children and node_type in _REQUIRES_CONTENT:
        problems.append(f"{path}: '{node_type}' has empty content")

    for index, child in enumerate(children):
        problems.extend(find_adf_problems(child, f"{path}.content[{index}]"))

    return problems


def find_fatal_problems(issue: Dict[str, Any]) -> List[str]:
    """
    Only the problems Jira actually rejects the issue over.

    Measured against 21 production runs in `service/prefect/data/`: of 675 issues
    submitted, exactly 10 were rejected, and **all 10** were
    `"The summary is invalid because it contains newline characters."` In the same
    corpus 1,461 of 1,477 issues carried a newline inside a description text node
    and all 1,477 carried an empty description text node -- and Jira accepted every
    one of them. So the description rules are enforced far more loosely than the
    ADF spec implies, and the summary rules are enforced strictly.

    Keep that asymmetry in this function. Folding the description findings back in
    here would mark ~100% of rows as "would have been rejected", and the one class
    that genuinely loses content would be lost in the noise.
    """
    fields = issue.get("fields") if isinstance(issue, dict) else None
    if not isinstance(fields, dict):
        return ["issue has no 'fields' object"]

    problems: List[str] = []

    summary = fields.get("summary")
    if not isinstance(summary, str):
        if summary is not None:
            problems.append(f"summary is a {type(summary).__name__}, not a string")
    else:
        if "\n" in summary or _LINE_BREAKS.search(summary):
            problems.append("summary contains a line break")
        if len(summary) > MAX_SUMMARY_CHARS:
            problems.append(f"summary is {len(summary)} characters (limit {MAX_SUMMARY_CHARS})")
        if _CONTROL.search(summary) or _SURROGATE.search(summary):
            problems.append("summary contains a control character")
        if not summary.strip():
            problems.append("summary is empty")

    if isinstance(fields.get("description"), str):
        problems.append("description is a plain string, not an ADF document")

    return problems


def find_issue_problems(issue: Dict[str, Any]) -> List[str]:
    """
    Every formatting problem, fatal or not -- the diagnostic view.

    The description findings here are **rendering** defects, not rejections: a
    newline inside a text node collapses the line break away, and an empty text
    node is dead weight. Use `find_fatal_problems` to decide whether an issue
    would have been lost.
    """
    fields = issue.get("fields") if isinstance(issue, dict) else None
    if not isinstance(fields, dict):
        return ["issue has no 'fields' object"]

    problems = find_fatal_problems(issue)

    description = fields.get("description")
    if isinstance(description, dict):
        problems.extend(find_adf_problems(description))

    return problems
