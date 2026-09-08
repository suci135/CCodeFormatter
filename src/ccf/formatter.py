"""Small, template-driven formatter for common C source files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .default_templates import DEFAULT_TEMPLATES
from .lexer import Token, tokenize


SUPPORTED_EXTENSIONS = {".c", ".h"}
CONTROL_WORDS = {"if", "for", "while", "switch"}
MULTI_CHAR_OPERATORS = (
    ">>=", "<<=", "...", "->", "++", "--", "&&", "||", "==", "!=", "<=", ">=",
    "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=", "<<", ">>", "##",
)
OPERATORS = set("=+-*/%&|^!~<>?:") | set(MULTI_CHAR_OPERATORS)
CONTROL_KEYWORDS = {"if", "for", "while", "switch"}
DEFINE_RE = re.compile(
    # C distinguishes a function-like macro by requiring '(' immediately
    # after the macro name.  Allowing whitespace here mistakes values such as
    # ``(1U << 12U)`` for a parameter list.
    r"^\s*#define\s+(?P<name>[A-Za-z_]\w*(?:\([^)]*\))?)(?:[ \t]+(?P<value>\S.*?))?[ \t]*$"
)


def _parse_define(line: str) -> tuple[str, str, int | None] | None:
    """Parse object-like and function-like defines without splitting params."""
    match = DEFINE_RE.match(line)
    if not match:
        return None
    value = match.group("value") or ""
    value_start = match.start("value") if match.group("value") else None
    return match.group("name"), value, value_start


@dataclass(frozen=True)
class TemplateStyle:
    indent_width: int = 4
    macro_value_column: int = 40
    brace_on_new_line: bool = True
    space_before_brace: bool = True
    space_before_control_paren: bool = True
    space_around_operators: bool = True
    space_after_comma: bool = True
    do_while_on_new_line: bool = False

    @classmethod
    def from_template(cls, template_dir: Path | None = None, extension: str | None = None) -> "TemplateStyle":
        """Read style from custom files or the templates bundled in the app."""
        suffix = extension.lower() if extension else ""
        suffixes = [suffix] if suffix in {".c", ".h"} else []
        suffixes.extend(item for item in (".c", ".h") if item not in suffixes)
        template_paths = [template_dir / f"template{item}" for item in suffixes] if template_dir else []
        existing_paths = [path for path in template_paths if path.exists()]
        template_sources = (
            [path.read_text(encoding="utf-8-sig").splitlines() for path in existing_paths]
            if existing_paths
            else [DEFAULT_TEMPLATES[item].splitlines() for item in suffixes]
        )

        macro_columns = []
        indent_width = 4
        indent_candidates = []
        primary_indent = None
        brace_same_line = 0
        brace_next_line = 0
        brace_with_space = 0
        brace_without_space = 0
        control_with_space = 0
        control_without_space = 0
        operator_with_space = 0
        operator_without_space = 0
        comma_with_space = 0
        comma_without_space = 0
        do_while_same_line = 0
        do_while_next_line = 0
        previous_code_line = ""
        for path_index, lines in enumerate(template_sources):
            for line in lines:
                if line.lstrip().startswith("*"):
                    # Do not infer the code indentation from ` *` lines in a
                    # block comment at the top of a template.
                    continue
                match = re.match(r"^( +)\S", line)
                if match:
                    candidate = len(match.group(1))
                    indent_candidates.append(candidate)
                    if path_index == 0:
                        primary_indent = candidate
                    break
            for line in lines:
                code = line.split("//", 1)[0]
                stripped_code = code.strip()
                if stripped_code:
                    if re.search(r"\}\s*while\s*\(", stripped_code):
                        do_while_same_line += 1
                    elif re.match(r"^while\s*\(", stripped_code) and previous_code_line.endswith("}"):
                        do_while_next_line += 1
                    previous_code_line = stripped_code
                if re.search(r"\)\s*\{", code):
                    brace_same_line += 1
                if re.search(r"\)\s+\{", code):
                    brace_with_space += 1
                if re.search(r"\)\{", code):
                    brace_without_space += 1
                if re.search(r"\)\s*$", code):
                    brace_next_line += 1
                if re.search(r"\b(?:if|for|while|switch)\s+\(", code):
                    control_with_space += 1
                if re.search(r"\b(?:if|for|while|switch)\(", code):
                    control_without_space += 1
                if re.search(r"\w\s+=\s+\w", code):
                    operator_with_space += 1
                if re.search(r"\w=(?!=)\w", code):
                    operator_without_space += 1
                if re.search(r",\s+\w", code):
                    comma_with_space += 1
                if re.search(r",\w", code):
                    comma_without_space += 1
                macro = _parse_define(line)
                if macro and macro[2] is not None:
                    macro_columns.append(macro[2])
        if primary_indent is not None:
            indent_width = primary_indent
        elif indent_candidates and not extension:
            indent_width = indent_candidates[0]
        return cls(
            indent_width=indent_width,
            macro_value_column=max(macro_columns, default=40),
            brace_on_new_line=brace_next_line >= brace_same_line,
            space_before_brace=brace_with_space >= brace_without_space,
            space_before_control_paren=control_with_space >= control_without_space,
            space_around_operators=operator_with_space >= operator_without_space,
            space_after_comma=comma_with_space >= comma_without_space,
            do_while_on_new_line=do_while_next_line > do_while_same_line,
        )


def _tokens(line: str) -> list[str]:
    """Tokenize one code line without interpreting comments or literals."""
    result = []
    index = 0
    while index < len(line):
        if line[index].isspace():
            index += 1
            continue
        if line.startswith("//", index):
            result.append(line[index:].rstrip())
            break
        if line.startswith("/*", index):
            end = line.find("*/", index + 2)
            end = len(line) if end < 0 else end + 2
            result.append(line[index:end])
            index = end
            continue
        if line[index] in "\"'":
            quote = line[index]
            end = index + 1
            while end < len(line):
                if line[end] == "\\":
                    end += 2
                    continue
                if line[end] == quote:
                    end += 1
                    break
                end += 1
            result.append(line[index:end])
            index = end
            continue
        operator = next((op for op in MULTI_CHAR_OPERATORS if line.startswith(op, index)), None)
        if operator:
            result.append(operator)
            index += len(operator)
            continue
        if line[index].isalnum() or line[index] == "_":
            end = index + 1
            while end < len(line) and (line[end].isalnum() or line[end] == "_"):
                end += 1
            result.append(line[index:end])
            index = end
            continue
        result.append(line[index])
        index += 1
    return result


def _is_word(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", token)) or token[:1].isdigit()


def _needs_space(previous: str, current: str, style: TemplateStyle) -> bool:
    if not previous:
        return False
    if current in {",", ")", "]", ".", "->"}:
        return False
    if current in {"++", "--"}:
        if previous == ";":
            return False
        return previous in OPERATORS or previous in {"?", ":"}
    if previous in {"(", "[", ".", "->"}:
        return False
    if previous in {"++", "--"}:
        return False
    if current == "(":
        return (
            (previous in CONTROL_WORDS and style.space_before_control_paren)
            or previous == "sizeof"
            or previous in OPERATORS
            or previous == "return"
        )
    if current == ":":
        return False
    if previous == "}" and (_is_word(current) or current in {"else", "while"}):
        return True
    if previous in {"return", "sizeof"} and (current.startswith(('\"', "'")) or _is_word(current)):
        return True
    if previous == "extern" and current.startswith('"'):
        return True
    if previous.startswith("/*") and _is_word(current):
        return True
    if current == "[":
        return False
    if current == "*" and previous in {"(", "=", ",", "return", "!", "&"}:
        return False
    if previous == "*" or previous in {"!", "~"}:
        return False
    if previous == "*" and current in {"*", ")", ";", ","}:
        return False
    if (current in OPERATORS or previous in OPERATORS or current in {"?", ":"} or previous in {"?", ":"}) and style.space_around_operators:
        return True
    return _is_word(previous) and _is_word(current)


def _render_tokens(tokens: list[str], style: TemplateStyle) -> str:
    output = ""
    previous = ""
    for token_index, token in enumerate(tokens):
        if token.startswith("//") or token.startswith("/*"):
            if output and not output.endswith(" "):
                output += " "
            output += token
            previous = token
            continue
        unary_address = previous == "&" and (token_index < 2 or tokens[token_index - 2] in {"(", "=", ",", "return"})
        unary_sign = previous in {"+", "-"} and (
            token_index < 2 or tokens[token_index - 2] in {"=", "(", ",", "return", ":"}
        )
        declarator_parenthesis = token == "(" and token_index + 1 < len(tokens) and tokens[token_index + 1] == "*"
        if _needs_space(previous, token, style) and not unary_address and not unary_sign:
            output += " "
        if declarator_parenthesis and output and not output.endswith(" "):
            output += " "
        output += token
        if token == "," and style.space_after_comma:
            output += " "
        elif token == ";":
            output += " "
        previous = token
    return output.rstrip()


def _line_comment_column(line: str) -> int | None:
    """Find a // comment without mistaking URLs or string contents for one."""
    quote = ""
    escaped = False
    index = 0
    while index < len(line):
        char = line[index]
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = ""
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            index += 1
            continue
        if line.startswith("/*", index):
            end = line.find("*/", index + 2)
            if end < 0:
                return None
            index = end + 2
            continue
        if line.startswith("//", index):
            return index
        index += 1
    return None


def _format_macro(line: str, value_column: int, comment_column: int | None = None) -> str:
    original_comment_column = _line_comment_column(line)
    code_line = line if original_comment_column is None else line[:original_comment_column].rstrip()
    parsed = _parse_define(code_line)
    if parsed is None:
        return line.strip()
    name, value, _value_start = parsed
    prefix = f"#define {name}"
    formatted = prefix
    if value:
        spaces = max(1, value_column - len(prefix))
        formatted += " " * spaces + value
    if original_comment_column is not None:
        target = original_comment_column if comment_column is None else comment_column
        formatted += " " * max(2, target - len(formatted)) + line[original_comment_column:]
    return formatted


def _has_line_continuation(line: str) -> bool:
    """Return whether a physical preprocessor line ends in an odd backslash run."""
    body = line.rstrip("\r\n")
    trailing_backslashes = len(body) - len(body.rstrip("\\"))
    return bool(trailing_backslashes % 2)


def _complete_control_braces(source: str) -> str:
    """Wrap a braceless one-statement control body in braces.

    This intentionally handles only an unambiguous statement ending in ';'.
    It leaves malformed or multi-line compound constructs for the checker.
    """
    tokens, _ = tokenize(source)
    code = [token for token in tokens if token.kind not in {"COMMENT", "PREPROCESSOR", "EOF"}]
    insertions: dict[int, str] = {}
    line_offsets = [0]
    for line in source.splitlines(keepends=True):
        line_offsets.append(line_offsets[-1] + len(line))

    def token_offset(token: Token, end: bool = False) -> int:
        """Convert a 1-based lexer position using precomputed line offsets."""
        line = token.end_line if end else token.line
        column = token.end_column if end else token.column
        if line <= 0 or line > len(line_offsets):
            return len(source)
        return line_offsets[line - 1] + column - 1

    def add_insertion(offset: int, text: str) -> None:
        insertions[offset] = insertions.get(offset, "") + text

    def matching_parenthesis(start: int) -> int | None:
        if start >= len(code) or code[start].value != "(":
            return None
        depth = 0
        for index in range(start, len(code)):
            value = code[index].value
            if value == "(":
                depth += 1
            elif value == ")":
                depth -= 1
                if depth == 0:
                    return index
        return None

    def matching_brace(start: int) -> int | None:
        if start >= len(code) or code[start].value != "{":
            return None
        depth = 0
        for index in range(start, len(code)):
            value = code[index].value
            if value == "{":
                depth += 1
            elif value == "}":
                depth -= 1
                if depth == 0:
                    return index
        return None

    def control_statement_end(start: int) -> int | None:
        value = code[start].value
        if value not in CONTROL_KEYWORDS:
            return None
        close = matching_parenthesis(start + 1)
        if close is None:
            return None
        body_start = close + 1
        body_end = statement_end(body_start)
        if body_end is None:
            return None
        if value == "if" and body_end + 1 < len(code) and code[body_end + 1].value == "else":
            return statement_end(body_end + 1)
        return body_end

    def statement_end(start: int) -> int | None:
        if start >= len(code):
            return None
        if code[start].value in CONTROL_KEYWORDS:
            return control_statement_end(start)
        if code[start].value == "do":
            body_end = statement_end(start + 1)
            if body_end is None:
                return None
            if body_end + 1 < len(code) and code[body_end + 1].value == "while":
                return statement_end(body_end + 1)
            return body_end
        if code[start].value == "{":
            return matching_brace(start)
        paren_depth = 0
        bracket_depth = 0
        for index in range(start, len(code)):
            value = code[index].value
            if value == "(":
                paren_depth += 1
            elif value == ")":
                paren_depth = max(0, paren_depth - 1)
            elif value == "[":
                bracket_depth += 1
            elif value == "]":
                bracket_depth = max(0, bracket_depth - 1)
            elif value == ";" and not paren_depth and not bracket_depth:
                return index
        return None

    for index, token in enumerate(code):
        value = token.value
        if value in CONTROL_KEYWORDS:
            close = matching_parenthesis(index + 1)
            if close is None:
                continue
            next_index = close + 1
            if next_index >= len(code) or code[next_index].value in {"{", ";"}:
                continue
            if value == "while" and index and code[index - 1].value in {"}", "do"}:
                continue
            end = statement_end(next_index)
            if end is None:
                continue
            add_insertion(token_offset(code[close], end=True), "{")
            add_insertion(token_offset(code[end], end=True), "}")
        elif value == "do":
            body_start = index + 1
            if body_start >= len(code) or code[body_start].value in {"{", ";"}:
                continue
            end = statement_end(body_start)
            if end is None:
                continue
            add_insertion(token_offset(code[body_start]), "{")
            add_insertion(token_offset(code[end], end=True), "}")
        elif value == "else":
            next_index = index + 1
            if next_index >= len(code) or code[next_index].value in {"{", ";", "if"}:
                continue
            end = statement_end(next_index)
            if end is None:
                continue
            add_insertion(token_offset(token, end=True), "{")
            add_insertion(token_offset(code[end], end=True), "}")

    for offset, text in sorted(insertions.items(), reverse=True):
        source = source[:offset] + text + source[offset:]
    return source


def safe_repair_c_code(source: str, indent_width: int = 4) -> str:
    """Apply only repairs explicitly marked safe by the checker.

    This deliberately does not normalize operators, commas, comments, or
    declarations. It only expands leading tabs, rounds malformed leading
    indentation up to the requested width, and inserts unambiguous control
    braces. The result still has to go through the normal comparison flow.
    """
    indent_width = max(1, int(indent_width))
    repaired = _complete_control_braces(source)
    output: list[str] = []
    for raw_line in repaired.splitlines(keepends=True):
        body = raw_line.rstrip("\r\n")
        newline = raw_line[len(body):]
        if body.lstrip().startswith("#"):
            output.append(body + newline)
            continue
        match = re.match(r"^[ \t]*", body)
        leading = match.group(0) if match else ""
        content = body[len(leading):]
        if not content:
            output.append(body + newline)
            continue
        spaces = len(leading.expandtabs(indent_width))
        if spaces % indent_width:
            spaces = ((spaces // indent_width) + 1) * indent_width
        output.append(" " * spaces + content + newline)
    return "".join(output)


def format_c_code(source: str, style: TemplateStyle | None = None) -> str:
    """Format common C syntax while leaving literals and comment text intact."""
    style = style or TemplateStyle.from_template()
    source = _complete_control_braces(source)
    output: list[str] = []
    current = ""
    indent = 0
    paren_depth = 0
    block_comment = False
    block_comment_source_indent = 0
    brace_stack: list[str] = []
    switch_case_indents: list[tuple[int, int | None]] = []
    last_closed_kind: str | None = None
    preprocessor_continuation = False

    def effective_indent() -> int:
        """Include the virtual indentation occupied by a switch case label."""
        if switch_case_indents and switch_case_indents[-1][1] is not None:
            return max(indent, switch_case_indents[-1][1])
        return indent

    def flush(line_indent: int | None = None, leading_spaces: int | None = None) -> None:
        nonlocal current
        if current.strip():
            if leading_spaces is None:
                line_indent = effective_indent() if line_indent is None else line_indent
                leading_spaces = line_indent * style.indent_width
            output.append(" " * leading_spaces + current)
        current = ""

    for raw_line in source.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        stripped = raw_line.strip()
        if preprocessor_continuation:
            flush()
            # Macro continuation lines are part of one logical directive.
            # Preserve them verbatim so formatting cannot change macro meaning
            # or the user's deliberate alignment.
            output.append(raw_line.rstrip())
            preprocessor_continuation = _has_line_continuation(raw_line)
            continue
        if not stripped:
            if current:
                flush()
            if block_comment:
                leading = raw_line[: len(raw_line) - len(raw_line.lstrip(" \t"))]
                comment_body = raw_line
                if block_comment_source_indent and len(leading) >= block_comment_source_indent:
                    comment_body = raw_line[block_comment_source_indent:]
                output.append(" " * (effective_indent() * style.indent_width) + comment_body)
                continue
            if output and output[-1] != "":
                output.append("")
            continue
        if stripped.startswith("#"):
            flush()
            # Preprocessor directives are line-oriented and conventionally
            # stay at column zero, even when they surround nested code.
            output.append(stripped)
            preprocessor_continuation = _has_line_continuation(raw_line)
            continue
        if block_comment:
            flush()
            leading = raw_line[: len(raw_line) - len(raw_line.lstrip(" \t"))]
            comment_body = raw_line
            if block_comment_source_indent and len(leading) >= block_comment_source_indent:
                # Rebase the comment to the formatter's code indentation, but
                # keep every character inside the comment.  In particular,
                # trailing spaces are meaningful in user-authored templates
                # that use fixed-width comment headers or tables.
                comment_body = raw_line[block_comment_source_indent:]
            end = comment_body.find("*/")
            comment_line = comment_body if end < 0 else comment_body[:end + 2]
            output.append(" " * (effective_indent() * style.indent_width) + comment_line)
            if end < 0:
                continue
            block_comment = False
            block_comment_source_indent = 0
            trailing = comment_body[end + 2:].strip()
            if not trailing:
                continue
            raw_line = trailing
            stripped = trailing

        comment_column = _line_comment_column(raw_line)
        if comment_column is not None and current:
            # A line comment terminates the physical line.  Do not let an
            # unfinished multi-line declaration absorb the next line's
            # comment; the comment column is part of the user's layout.
            flush()
        source_leading = raw_line[: len(raw_line) - len(raw_line.lstrip(" \t"))]
        source_leading_spaces = len(source_leading.expandtabs(style.indent_width))
        tokens = _tokens(raw_line)
        case_label_line = bool(re.match(r"^(case|default)\b", stripped))
        label_line = case_label_line or bool(re.match(r"^[A-Za-z_]\w*:\s*$", stripped))
        case_label_active = case_label_line
        if case_label_line and switch_case_indents:
            # A case/default label belongs to the switch body. Statements
            # below it are one level deeper than the label itself.
            switch_body_indent, _ = switch_case_indents[-1]
            switch_case_indents[-1] = (switch_body_indent, switch_body_indent + 1)
        for token_index, token in enumerate(tokens):
            if last_closed_kind == "do" and current.rstrip() == "}" and token == "while":
                if style.do_while_on_new_line:
                    flush()
                    current = "while"
                else:
                    current = _render_tokens(_tokens(current + " " + token), style)
                last_closed_kind = None
                continue
            if (
                last_closed_kind in {"block", "switch"}
                and current.rstrip() == "}"
                and token not in {"else", ";", ",", ")", "]"}
                and not token.startswith(("//", "/*"))
            ):
                flush()
            last_closed_kind = None
            if token in {"case", "default"} and not current.strip() and switch_case_indents:
                case_label_active = True
                switch_body_indent, _ = switch_case_indents[-1]
                switch_case_indents[-1] = (switch_body_indent, switch_body_indent + 1)
            if token.startswith("/*") and "*/" not in token:
                comment_indent = len(raw_line) - len(raw_line.lstrip(" \t"))
                if current:
                    flush()
                    comment_indent = 0
                output.append(" " * (indent * style.indent_width) + token)
                block_comment = True
                block_comment_source_indent = comment_indent
                continue
            if token == "(":
                paren_depth += 1
            elif token == ")":
                paren_depth = max(0, paren_depth - 1)
            if token == "{" and paren_depth == 0 and not current.rstrip().endswith("="):
                header = current.rstrip()
                opening_indent = effective_indent()
                is_control = (
                    bool(re.match(r"^(if|for|while|switch)\b", header))
                    or header.endswith(")")
                    or header.startswith("} else")
                )
                if re.match(r"^switch\b", header):
                    brace_kind = "switch"
                    switch_case_indents.append((opening_indent + 1, None))
                else:
                    if re.match(r"^do\b", header):
                        brace_kind = "do"
                    else:
                        brace_kind = "type" if re.search(r"\b(struct|union|enum)\b", header) else "block"
                brace_stack.append(brace_kind)
                if is_control:
                    if style.brace_on_new_line:
                        current = header
                        flush(opening_indent)
                        output.append(" " * (opening_indent * style.indent_width) + "{")
                    else:
                        current = header + (" " if style.space_before_brace else "") + "{"
                        flush()
                    indent = max(indent, opening_indent) + 1
                else:
                    current = header + ((" " if style.space_before_brace else "") if header else "") + "{" if not style.brace_on_new_line else header
                    if style.brace_on_new_line:
                        flush(opening_indent)
                        output.append(" " * (opening_indent * style.indent_width) + "{")
                    else:
                        flush()
                    indent = max(indent, opening_indent) + 1
                continue
            if token == "{" and paren_depth == 0 and current.rstrip().endswith("="):
                brace_stack.append("initializer")
                current = current.rstrip() + "{"
                continue
            if token == "}":
                if brace_stack and brace_stack[-1] == "initializer":
                    brace_stack.pop()
                    current = _render_tokens(_tokens(current + "}"), style)
                    continue
                closed_kind = brace_stack.pop() if brace_stack else None
                if current:
                    flush()
                indent = max(0, indent - 1)
                if closed_kind == "switch" and switch_case_indents:
                    switch_body_indent, _ = switch_case_indents[-1]
                    switch_case_indents.pop()
                    indent = max(0, switch_body_indent - 1)
                current = "}"
                last_closed_kind = closed_kind
                continue
            if token == "else" and current.rstrip() == "}" and style.brace_on_new_line:
                flush()
                current = "else"
                continue
            if token == ":" and case_label_active and "?" not in current:
                current = _render_tokens(_tokens(current), style) + ":"
                flush(switch_case_indents[-1][0] if switch_case_indents else indent)
                current = ""
                case_label_active = False
                continue
            if token == ";" and paren_depth == 0:
                current = _render_tokens(_tokens(current), style) + ";"
                has_comment_after = any(item.startswith(("//", "/*")) for item in tokens[token_index + 1:])
                if not has_comment_after:
                    flush()
                continue
            if token.startswith("//"):
                current = _render_tokens(_tokens(current), style)
                if current:
                    target_column = comment_column if comment_column is not None else 0
                    logical_code_column = effective_indent() * style.indent_width
                    # A closing brace has already reduced the logical block
                    # depth above.  Do not re-introduce the source line's old
                    # indentation just because the brace has a trailing //
                    # comment; that made `} // comment` drift right.
                    code_column = (
                        logical_code_column
                        if current.strip().startswith("}")
                        else max(logical_code_column, source_leading_spaces)
                    )
                    current += " " * max(1, target_column - code_column - len(current))
                    current += raw_line[target_column:] if comment_column is not None else token
                    flush(leading_spaces=code_column)
                else:
                    current = raw_line[comment_column:] if comment_column is not None else token
                    flush(leading_spaces=max(effective_indent() * style.indent_width, source_leading_spaces, comment_column or 0))
                continue
            current = _render_tokens(_tokens(current + " " + token), style)

        type_member = brace_stack and brace_stack[-1] == "type" and current.rstrip().endswith(",")
        if current and (label_line or type_member or any(token.startswith(("//", "/*")) for token in tokens)):
            if case_label_line:
                flush(switch_case_indents[-1][0] if switch_case_indents else indent)
            else:
                flush(max(0, indent - 1) if label_line else None)

    flush()
    while output and output[-1] == "":
        output.pop()
    formatted = "\n".join(output)
    formatted_lines = formatted.splitlines()
    macro_lines = []
    for line_index, line in enumerate(formatted_lines):
        if not line.lstrip().startswith("#define"):
            continue
        comment_column = _line_comment_column(line)
        code_line = line if comment_column is None else line[:comment_column].rstrip()
        macro_lines.append((line_index, line, _parse_define(code_line)))

    macro_prefix_lengths = [
        len(f"#define {parsed[0]}")
        for _line_index, _line, parsed in macro_lines
        if parsed is not None
    ]
    macro_value_column = max(style.macro_value_column, max((length + 1 for length in macro_prefix_lengths), default=0))

    # Keep comments close to their definitions while aligning each contiguous
    # macro group.  Using the template's historical absolute comment column
    # makes short definitions inherit hundreds of spaces from long lines.
    macro_comment_columns: dict[int, int | None] = {}
    group: list[tuple[int, str, tuple[str, str, int | None]]] = []

    def flush_macro_group() -> None:
        if not group:
            return
        code_lengths = [
            len(f"#define {parsed[0]}")
            + (max(1, macro_value_column - len(f"#define {parsed[0]}")) if parsed[1] else 0)
            + len(parsed[1])
            for _line_index, _line, parsed in group
        ]
        comment_column = max(code_lengths) + 2 if code_lengths else None
        for line_index, line, _parsed in group:
            macro_comment_columns[line_index] = comment_column if _line_comment_column(line) is not None else None
        group.clear()

    previous_index = None
    for line_index, line, parsed in macro_lines:
        if parsed is None:
            flush_macro_group()
            previous_index = None
            continue
        if previous_index is None or line_index != previous_index + 1:
            flush_macro_group()
        group.append((line_index, line, parsed))
        previous_index = line_index
    flush_macro_group()

    formatted = "\n".join(
        _format_macro(line, macro_value_column, macro_comment_columns.get(line_index))
        if line.lstrip().startswith("#define")
        else line
        for line_index, line in enumerate(formatted_lines)
    )
    return formatted + "\n"
