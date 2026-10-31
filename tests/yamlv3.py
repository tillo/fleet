"""Byte-exact model of the bytes Harvester writes when it re-marshals a values string.

Harvester's setting syncers read `spec.valuesContent` of the rke2-ingress-nginx
HelmChartConfig, change one key, and write the whole string back with gopkg.in/yaml.v3
v3.0.1: `yaml.Unmarshal` into map[string]interface{}, then `yaml.Marshal`. That output is
canonical: 4-space indent, keys in yaml.v3's natural sort order, every comment dropped,
quoting chosen by the emitter. This module reproduces it for the subset of YAML a values
file uses, so a test can check that git already holds those exact bytes.

It models v3.0.1's resolve.go and decode.go (Unmarshal into interface{}) and its
encode.go, emitterc.go and sorter.go (Marshal). It was checked byte for byte against the
real library: a Go program linking gopkg.in/yaml.v3 v3.0.1 ran on the live string and on
fuzzed values covering every quoting and indent branch below.

⛔ It FAILS CLOSED. Anything it does not model raises Unsupported rather than guessing:
anchors and aliases, explicit tags, merge keys, non-string or complex keys,
timestamp-shaped scalars, line breaks other than \\n, keep-chomped (`|+`) literals,
literals starting with a tab, integers outside int64/uint64, key sets yaml.v3's Less does
not totally order (its result then depends on Go's sort algorithm), and a second
document. If you hit one, reshape the value, or extend the model and re-check it against
the library. Never catch the exception.
"""

from __future__ import annotations

import decimal
import functools
import math
import re
import unicodedata

import yaml


class Unsupported(ValueError):
    """The input uses something this model does not reproduce exactly."""


# ── resolve.go: what an untagged plain scalar decodes to ──────────────────────────

_RESOLVE_MAP: dict[str, object] = {}
for _v, _spellings in (
    (True, ("true", "True", "TRUE")),
    (False, ("false", "False", "FALSE")),
    (None, ("", "~", "null", "Null", "NULL")),
    (math.nan, (".nan", ".NaN", ".NAN")),
    (math.inf, (".inf", ".Inf", ".INF", "+.inf", "+.Inf", "+.INF")),
    (-math.inf, ("-.inf", "-.Inf", "-.INF")),
):
    for _s in _spellings:
        _RESOLVE_MAP[_s] = _v

# resolveTable: the first character picks how a plain scalar may resolve.
_HINT = {"+": "S", "-": "S", ".": "."}
_HINT.update({c: "D" for c in "0123456789"})
_HINT.update({c: "M" for c in "yYnNtTfFoO~"})

_YAML_STYLE_FLOAT = re.compile(r"[-+]?(\.[0-9]+|[0-9]+(\.[0-9]*)?)([eE][-+]?[0-9]+)?")
# strconv.ParseFloat on a '.'-first string; its underscoreOK allows _ between digits
_DOT_FLOAT = re.compile(r"\.[0-9]+(?:_[0-9]+)*(?:[eE][-+]?[0-9]+(?:_[0-9]+)*)?")
_TIMESTAMP_SHAPED = re.compile(r"[0-9]{4}-[0-9]")  # parseTimestamp then tries time.Parse
_BASE60_FLOAT = re.compile(r"[-+]?[0-9][0-9_]*(?::[0-5]?[0-9])+(?:\.[0-9_]*)?")
_OLD_BOOLS = frozenset(
    "y Y yes Yes YES on On ON n N no No NO off Off OFF".split())

_INT64_MAX = (1 << 63) - 1
_UINT64_MAX = (1 << 64) - 1


def _parse_uint(s: str) -> int | None:
    """strconv.ParseUint(s, 0, 64), underscores already removed."""
    if not s:
        return None
    base = 10
    if s[0] == "0":
        prefix = s[1:2].lower()
        if len(s) >= 3 and prefix in ("b", "o", "x"):
            base, s = {"b": 2, "o": 8, "x": 16}[prefix], s[2:]
        else:
            base, s = 8, s[1:]
    n = 0
    for c in s:
        if not c.isascii():
            return None
        if "0" <= c <= "9":
            d = ord(c) - ord("0")
        elif "a" <= c.lower() <= "z":
            d = ord(c.lower()) - ord("a") + 10
        else:
            return None
        if d >= base:
            return None
        n = n * base + d
        if n > _UINT64_MAX:
            return None
    return n


def _parse_int(s: str) -> int | None:
    """strconv.ParseInt(s, 0, 64)."""
    neg = s[:1] == "-"
    if s[:1] in ("+", "-"):
        s = s[1:]
    un = _parse_uint(s)
    if un is None:
        return None
    if neg:
        return -un if un <= _INT64_MAX + 1 else None
    return un if un <= _INT64_MAX else None


def resolve_plain(s: str):
    """The Go value yaml.v3 decodes the untagged plain scalar `s` to inside an
    interface{}: str, bool, int, float or None."""
    if s == "":
        return None
    hint = _HINT.get(s[0])
    if hint is None:
        return s
    if s in _RESOLVE_MAP:
        return _RESOLVE_MAP[s]
    if hint == "M":
        return s
    if hint == ".":
        if _DOT_FLOAT.fullmatch(s) and not math.isinf(float(s.replace("_", ""))):
            return float(s.replace("_", ""))
        return s
    if _TIMESTAMP_SHAPED.match(s):
        raise Unsupported(f"timestamp-shaped scalar {s!r}: yaml.v3 tries time.Parse on it")
    plain = s.replace("_", "")
    v = _parse_int(plain)
    if v is None:
        v = _parse_uint(plain)
    if v is not None:
        return v
    if _YAML_STYLE_FLOAT.fullmatch(plain) and not math.isinf(float(plain)):
        return float(plain)
    if re.match(r"-?0[bo]", plain):
        raise Unsupported(f"{s!r}: yaml.v3's 0b/0o fallback parsers are not modelled")
    return s


# ── Unmarshal ─────────────────────────────────────────────────────────────────────

def load(text: str) -> dict:
    """yaml.Unmarshal(text, &map[string]interface{}{}) as Harvester calls it."""
    events = iter(yaml.parse(text, Loader=yaml.SafeLoader))
    next(events)  # StreamStart
    ev = next(events)
    if isinstance(ev, yaml.StreamEndEvent):
        return {}
    root = _node(next(events), events)
    if not isinstance(next(events), yaml.DocumentEndEvent):
        raise Unsupported("unexpected event after the root node")
    if not isinstance(next(events), yaml.StreamEndEvent):
        raise Unsupported("more than one document: yaml.Unmarshal reads only the first")
    if root is None:
        return {}
    if not isinstance(root, dict):
        raise Unsupported("the root node is not a mapping: Unmarshal into a map fails")
    return root


def _node(ev, events):
    if isinstance(ev, yaml.AliasEvent) or getattr(ev, "anchor", None):
        raise Unsupported(f"anchor/alias at {ev.start_mark}")
    if getattr(ev, "tag", None) is not None:
        raise Unsupported(f"explicit tag {ev.tag!r} at {ev.start_mark}")
    if isinstance(ev, yaml.ScalarEvent):
        return resolve_plain(ev.value) if ev.style is None else ev.value
    if isinstance(ev, yaml.SequenceStartEvent):
        out = []
        for item in events:
            if isinstance(item, yaml.SequenceEndEvent):
                return out
            out.append(_node(item, events))
    if isinstance(ev, yaml.MappingStartEvent):
        out = {}
        for kev in events:
            if isinstance(kev, yaml.MappingEndEvent):
                return out
            key = _node(kev, events)
            if not isinstance(key, str):
                raise Unsupported(f"non-string key {key!r} at {kev.start_mark}")
            if key == "<<" and kev.style is None:
                raise Unsupported(f"merge key at {kev.start_mark}")
            if key in out:
                # yaml.v3 refuses duplicates, so Harvester's syncer would fail outright
                raise Unsupported(f"duplicate key {key!r} at {kev.start_mark}")
            out[key] = _node(next(events), events)
    raise Unsupported(f"unexpected event {ev}")


# ── sorter.go: keyList.Less, string keys only ─────────────────────────────────────

def _is_letter(c: str) -> bool:
    return unicodedata.category(c).startswith("L")


def _is_digit(c: str) -> bool:
    return unicodedata.category(c) == "Nd"


def _key_less(a: str, b: str) -> bool:
    digits = False
    for i in range(min(len(a), len(b))):
        if a[i] == b[i]:
            digits = _is_digit(a[i])
            continue
        al, bl = _is_letter(a[i]), _is_letter(b[i])
        if al and bl:
            return a[i] < b[i]
        if al or bl:
            return al if digits else bl
        an = bn = 0
        if a[i] == "0" or b[i] == "0":
            j = i - 1
            while j >= 0 and _is_digit(a[j]):
                if a[j] != "0":
                    an = bn = 1
                    break
                j -= 1
        ai = i
        while ai < len(a) and _is_digit(a[ai]):
            an = an * 10 + ord(a[ai]) - ord("0")
            ai += 1
        bi = i
        while bi < len(b) and _is_digit(b[bi]):
            bn = bn * 10 + ord(b[bi]) - ord("0")
            bi += 1
        if max(ai, bi) - i > 18:
            raise Unsupported(f"digit run in keys {a!r}/{b!r} would overflow int64")
        if an != bn:
            return an < bn
        if ai != bi:
            return ai < bi
        return a[i] < b[i]
    return len(a) < len(b)


def _sorted_keys(d: dict) -> list[str]:
    keys = sorted(d, key=functools.cmp_to_key(
        lambda a, b: -1 if _key_less(a, b) else (1 if _key_less(b, a) else 0)))
    # Less can cycle (punctuation vs digit runs), and then the order is whatever Go's
    # pdqsort happens to compare: every pair must agree, or the order is not modelled
    for i, a in enumerate(keys):
        for b in keys[i + 1:]:
            if not _key_less(a, b) or _key_less(b, a):
                raise Unsupported(f"keys {a!r}/{b!r}: keyList.Less is not a total order here")
    return keys


# ── emitterc.go: scalar analysis and style ────────────────────────────────────────

_BREAKS = "\n"  # the only break modelled; the others are refused in _check_str
_FOREIGN_BREAKS = "\r\x85  "
_PLAIN, _SINGLE, _DOUBLE, _LITERAL = "plain", "single", "double", "literal"
_BEST_INDENT = 4


def _printable(c: str) -> bool:
    o = ord(c)
    return (o == 0x0A or 0x20 <= o <= 0x7E or 0xA0 <= o <= 0xD7FF
            or (0xE000 <= o <= 0xFFFD and o != 0xFEFF))


def _check_str(s: str) -> None:
    if any(c in _FOREIGN_BREAKS for c in s):
        raise Unsupported(f"{s!r}: line breaks other than \\n are not modelled")


def _analyze(v: str) -> dict:
    """yaml_emitter_analyze_scalar."""
    if not v:
        return dict(multiline=False, block_plain=True, single=True, block=False)
    block_ind = line_breaks = special = tabs = False
    leading_space = leading_break = trailing_space = trailing_break = False
    break_space = space_break = prev_space = prev_break = False
    if v.startswith(("---", "...")):
        block_ind = True
    preceded_ws = True
    n = len(v)
    for i, c in enumerate(v):
        followed_ws = i + 1 >= n or v[i + 1] in " \t"
        # flow indicators are not tracked: no non-empty node is ever in flow context
        if i == 0:
            if c in "#,[]{}&*!|>'\"%@`":
                block_ind = True
            elif c in "?:-" and followed_ws:
                block_ind = True
        elif c == ":" and followed_ws or c == "#" and preceded_ws:
            block_ind = True
        if c == "\t":
            tabs = True
        elif not _printable(c):
            special = True
        if c == " ":
            leading_space = leading_space or i == 0
            trailing_space = trailing_space or i + 1 == n
            break_space = break_space or prev_break
            prev_space, prev_break = True, False
        elif c in _BREAKS:
            line_breaks = True
            leading_break = leading_break or i == 0
            trailing_break = trailing_break or i + 1 == n
            space_break = space_break or prev_space
            prev_space, prev_break = False, True
        else:
            prev_space = prev_break = False
        preceded_ws = c in " \t\n\x00"

    block_plain = single = block = True
    if leading_space or leading_break or trailing_space or trailing_break:
        block_plain = False
    if trailing_space:
        block = False
    if break_space:
        block_plain = single = False
    if space_break or tabs or special:
        block_plain = single = False
    if space_break or special:
        block = False
    if line_breaks:
        block_plain = False
    if block_ind:
        block_plain = False
    return dict(multiline=line_breaks, block_plain=block_plain, single=single,
                block=block)


def _can_use_plain(s: str) -> bool:
    """encode.go stringv: would `s` unquoted read back as this same string?"""
    if not isinstance(resolve_plain(s), str):
        return False
    if s and (s[0] in "+-" or "0" <= s[0] <= "9") and ":" in s and _BASE60_FLOAT.fullmatch(s):
        return False
    return s not in _OLD_BOOLS


def _string_style(s: str, simple_key: bool) -> str:
    """encode.go stringv + yaml_emitter_select_scalar_style for a string."""
    if "\n" in s:
        style = _LITERAL
    elif _can_use_plain(s):
        style = _PLAIN
    else:
        style = _DOUBLE
    a = _analyze(s)
    if simple_key and a["multiline"]:
        style = _DOUBLE
    if style == _PLAIN:
        if not a["block_plain"]:
            style = _SINGLE
        if not s and simple_key:
            style = _SINGLE
    if style == _SINGLE and not a["single"]:
        style = _DOUBLE
    if style == _LITERAL and (not a["block"] or simple_key):
        style = _DOUBLE
    return style


def _go_float(f: float) -> str:
    """strconv.FormatFloat(f, 'g', -1, 64) as encode.go floatv post-processes it."""
    if math.isnan(f):
        return ".nan"
    if math.isinf(f):
        return ".inf" if f > 0 else "-.inf"
    if f == 0:
        return "-0" if math.copysign(1.0, f) < 0 else "0"
    _, digits, exp = decimal.Decimal(repr(abs(f))).as_tuple()
    digits = list(digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exp += 1
    ds = "".join(map(str, digits))
    nd, dp = len(ds), len(ds) + exp
    if dp - 1 < -4 or dp - 1 >= 6:  # %e; shortest formatting uses eprec 6
        x = dp - 1
        s = ds[0] + ("." + ds[1:] if nd > 1 else "") + "e" + ("-" if x < 0 else "+") + f"{abs(x):02d}"
    elif dp <= 0:
        s = "0." + "0" * -dp + ds
    elif dp >= nd:
        s = ds + "0" * (dp - nd)
    else:
        s = ds[:dp] + "." + ds[dp:]
    return ("-" if f < 0 else "") + s


def _escape_double(v: str) -> str:
    table = {0x00: "0", 0x07: "a", 0x08: "b", 0x09: "t", 0x0A: "n", 0x0B: "v",
             0x0C: "f", 0x0D: "r", 0x1B: "e", 0x22: '"', 0x5C: "\\", 0x85: "N",
             0xA0: "_", 0x2028: "L", 0x2029: "P"}
    out = []
    for c in v:
        o = ord(c)
        if not _printable(c) or o == 0xFEFF or c in _BREAKS or c in '"\\':
            if o in table:
                out.append("\\" + table[o])
            elif o <= 0xFF:
                out.append(f"\\x{o:02X}")
            elif o <= 0xFFFF:
                out.append(f"\\u{o:04X}")
            else:
                out.append(f"\\U{o:08X}")
        else:
            out.append(c)
    return "".join(out)


# ── Marshal ───────────────────────────────────────────────────────────────────────

class _Emitter:
    """The parts of yaml_emitter_t that decide bytes in block context."""

    def __init__(self) -> None:
        self.out: list[str] = []
        self.column = 0
        self.whitespace = True
        self.indention = True
        self.indent = -1

    def put(self, s: str) -> None:
        self.out.append(s)
        self.column += len(s)

    def put_break(self) -> None:
        self.out.append("\n")
        self.column = 0
        self.indention = True

    def write_indent(self) -> None:
        indent = max(self.indent, 0)
        if (not self.indention or self.column > indent
                or (self.column == indent and not self.whitespace)):
            self.put_break()
        if self.column < indent:
            self.put(" " * (indent - self.column))
        self.whitespace = True

    def indicator(self, s: str, need_ws: bool, is_ws: bool, is_indention: bool) -> None:
        if need_ws and not self.whitespace:
            self.put(" ")
        self.put(s)
        self.whitespace = is_ws
        self.indention = self.indention and is_indention

    def increase_indent(self, in_seq_item: bool, flow: bool = False) -> int:
        old = self.indent
        if self.indent < 0:
            self.indent = _BEST_INDENT if flow else 0
        elif in_seq_item:
            self.indent += 2  # the first indent inside a sequence skips "- "
        else:
            self.indent = _BEST_INDENT * ((self.indent + _BEST_INDENT) // _BEST_INDENT)
        return old

    # node emitters; in_seq_item = the parent pushed BLOCK_SEQUENCE_ITEM_STATE
    def node(self, v, in_seq_item: bool) -> None:
        if isinstance(v, dict):
            if not v:
                self.indicator("{", True, True, False)
                self.indicator("}", False, False, False)
                return
            old = self.increase_indent(in_seq_item)
            for k in _sorted_keys(v):
                self.write_indent()
                self.key(k)
                self.indicator(":", False, False, False)
                self.node(v[k], False)
            self.indent = old
        elif isinstance(v, list):
            if not v:
                self.indicator("[", True, True, False)
                self.indicator("]", False, False, False)
                return
            old = self.increase_indent(in_seq_item)
            for item in v:
                self.write_indent()
                self.indicator("-", True, False, True)
                self.node(item, True)
            self.indent = old
        else:
            old = self.increase_indent(in_seq_item, flow=True)
            self.scalar(v, simple_key=False)
            self.indent = old

    def key(self, k) -> None:
        if not isinstance(k, str):
            raise Unsupported(f"non-string key {k!r}")
        _check_str(k)
        if "\n" in k or len(k.encode()) > 128:
            raise Unsupported(f"key {k!r} is not a simple key: yaml.v3 writes it as `? `")
        old = self.increase_indent(False, flow=True)
        self.scalar(k, simple_key=True)
        self.indent = old

    def scalar(self, v, simple_key: bool) -> None:
        if v is None:
            self.plain("null")
        elif isinstance(v, bool):
            self.plain("true" if v else "false")
        elif isinstance(v, int):
            if not -(_INT64_MAX + 1) <= v <= _UINT64_MAX:
                raise Unsupported(f"integer {v} is outside int64/uint64")
            self.plain(str(v))
        elif isinstance(v, float):
            self.plain(_go_float(v))
        elif isinstance(v, str):
            _check_str(v)
            style = _string_style(v, simple_key)
            {_PLAIN: self.plain, _SINGLE: self.single, _DOUBLE: self.double,
             _LITERAL: self.literal}[style](v)
        else:
            raise Unsupported(f"value of type {type(v).__name__} is not modelled")

    def plain(self, v: str) -> None:
        if v and not self.whitespace:
            self.put(" ")
        self.put(v)
        self.whitespace = False
        self.indention = False

    def single(self, v: str) -> None:
        self.indicator("'", True, False, False)
        self.put(v.replace("'", "''"))
        self.indicator("'", False, False, False)

    def double(self, v: str) -> None:
        self.indicator('"', True, False, False)
        self.put(_escape_double(v))
        self.indicator('"', False, False, False)

    def literal(self, v: str) -> None:
        if v[0] == "\t":
            # yaml.v3 emits it, then its own scanner refuses the tab while it is
            # still detecting the block's indentation: not a round-trip at all
            raise Unsupported(f"{v!r}: a literal starting with a tab does not re-read in yaml.v3")
        self.indicator("|", True, False, False)
        if v[0] in " \n":
            self.indicator(str(_BEST_INDENT), False, False, False)
        if not v.endswith("\n"):
            self.indicator("-", False, False, False)
        elif v == "\n" or v.endswith("\n\n"):
            raise Unsupported(f"{v!r} needs keep chomping (`|+`), which is not modelled")
        self.whitespace = True
        breaks = True
        for c in v:
            if c == "\n":
                self.put_break()
                breaks = True
            else:
                if breaks:
                    self.write_indent()
                self.put(c)
                self.indention = False
                breaks = False


def dump(values: dict) -> str:
    """yaml.Marshal(values) for a map[string]interface{}."""
    if not isinstance(values, dict):
        raise Unsupported("the root must be a mapping")
    e = _Emitter()
    e.node(values, False)
    e.indent = -1
    e.write_indent()  # yaml_emitter_emit_document_end
    return "".join(e.out)
