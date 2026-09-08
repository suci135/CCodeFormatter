"""Recovering structural checks for common C syntax mistakes.

The checker deliberately does not pretend to be a full C compiler.  This
parser validates the token structure that can be explained locally and keeps
going after an error so one malformed line does not hide every later issue.
"""

from __future__ import annotations

from dataclasses import dataclass

from .lexer import Token


@dataclass(frozen=True)
class SyntaxIssue:
    line: int
    column: int
    end_line: int
    end_column: int
    rule_id: str
    message: str
    expected: str = ""


_MATCHING = {"(": ")", "[": "]", "{": "}"}
_OPENERS = set(_MATCHING)
_CLOSERS = set(_MATCHING.values())


def _issue(token: Token, rule_id: str, message: str, expected: str = "") -> SyntaxIssue:
    return SyntaxIssue(token.line, token.column, token.end_line, token.end_column, rule_id, message, expected)


def _code_tokens(tokens: list[Token]) -> list[Token]:
    return [token for token in tokens if token.kind not in {"COMMENT", "EOF"}]


def _is_function_definition(tokens: list[Token], index: int) -> bool:
    """Recognize `name(...) {` without confusing ordinary function calls."""
    token = tokens[index]
    if token.kind != "IDENTIFIER" or index + 1 >= len(tokens) or tokens[index + 1].value != "(":
        return False
    depth = 0
    close_index = None
    for cursor in range(index + 1, len(tokens)):
        value = tokens[cursor].value
        if value == "(":
            depth += 1
        elif value == ")":
            depth -= 1
            if depth == 0:
                close_index = cursor
                break
    return close_index is not None and close_index + 1 < len(tokens) and tokens[close_index + 1].value == "{"


def _block_kind(tokens: list[Token], index: int) -> str:
    if index > 0 and tokens[index - 1].value == ")":
        parentheses = 0
        for cursor in range(index - 1, -1, -1):
            value = tokens[cursor].value
            if value == ")":
                parentheses += 1
            elif value == "(":
                parentheses -= 1
                if parentheses == 0:
                    name_index = cursor - 1
                    if name_index >= 0 and tokens[name_index].kind == "IDENTIFIER":
                        return "function"
                    break
    values: list[str] = []
    parentheses = 0
    for cursor in range(index - 1, max(-1, index - 64), -1):
        value = tokens[cursor].value
        if value == ")":
            parentheses += 1
        elif value == "(":
            parentheses = max(0, parentheses - 1)
        if parentheses == 0 and value in {";", "{", "}"}:
            break
        values.append(value)
    if "switch" in values:
        return "switch"
    if any(keyword in values for keyword in {"for", "while", "do"}):
        return "loop"
    return "block"


def analyze_syntax(tokens: list[Token]) -> list[SyntaxIssue]:
    """Analyze delimiters, context-sensitive statements, and preprocessor pairs."""
    code = _code_tokens(tokens)
    issues: list[SyntaxIssue] = []
    delimiter_stack: list[Token] = []
    block_stack: list[str] = []
    preprocessor_stack: list[tuple[Token, bool]] = []
    function_depth = 0
    loop_depth = 0
    switch_depth = 0

    for index, token in enumerate(code):
        if token.kind == "UNKNOWN":
            continue
        if token.kind == "PREPROCESSOR":
            directive = token.value.lstrip()[1:].strip().split(None, 1)[0] if token.value.lstrip().startswith("#") else ""
            if directive in {"if", "ifdef", "ifndef"}:
                preprocessor_stack.append((token, False))
            elif directive in {"else", "elif"}:
                if not preprocessor_stack:
                    issues.append(_issue(token, "syntax.preprocessor_branch", f"检测到没有对应条件开始指令的 #{directive}。", f"#{directive} 应位于 #if/#ifdef/#ifndef 之后。"))
                else:
                    opener, saw_else = preprocessor_stack[-1]
                    if saw_else:
                        issues.append(_issue(token, "syntax.preprocessor_branch", f"#{directive} 出现在同一条件编译块的 #else 之后。", f"#{directive} 应位于 #{opener.value.lstrip()[1:].split(None, 1)[0]} 和 #else 之间。"))
                    elif directive == "else":
                        preprocessor_stack[-1] = (opener, True)
            elif directive == "endif":
                if preprocessor_stack:
                    preprocessor_stack.pop()
                else:
                    issues.append(_issue(token, "syntax.unmatched_endif", "检测到没有对应条件开始指令的 #endif。", "每个 #endif 都应对应一个 #if/#ifdef/#ifndef。"))
            continue

        value = token.value
        if value in _OPENERS:
            delimiter_stack.append(token)
            if value == "{":
                block_kind = _block_kind(code, index)
                block_stack.append(block_kind)
                if block_stack[-1] == "function":
                    function_depth += 1
                elif block_stack[-1] == "loop":
                    loop_depth += 1
                elif block_stack[-1] == "switch":
                    switch_depth += 1
            continue
        if value in _CLOSERS:
            expected_open = {value: opener for opener, closer in _MATCHING.items() for value in [closer]}[value]
            popped_opener = None
            if not delimiter_stack:
                issues.append(_issue(token, "syntax.unexpected_closing_delimiter", f"检测到多余的 {value}。", f"这里不应出现 {value}。"))
            elif delimiter_stack[-1].value != expected_open:
                opener = delimiter_stack[-1]
                issues.append(_issue(token, "syntax.mismatched_delimiter", f"{value} 与前面的 {opener.value} 不匹配。", f"应使用 { _MATCHING[opener.value] } 闭合。"))
                popped_opener = delimiter_stack.pop()
            else:
                popped_opener = delimiter_stack.pop()
            # Only consume a block context when the recovered opener was a
            # brace. For input such as `([}`, a stray `}` must not erase the
            # function/loop context belonging to an earlier `{`.
            if popped_opener is not None and popped_opener.value == "{" and block_stack:
                block = block_stack.pop()
                if block == "function":
                    function_depth = max(0, function_depth - 1)
                elif block == "loop":
                    loop_depth = max(0, loop_depth - 1)
                elif block == "switch":
                    switch_depth = max(0, switch_depth - 1)
            continue

        if token.kind == "KEYWORD":
            if value == "break" and loop_depth == 0 and switch_depth == 0:
                issues.append(_issue(token, "syntax.break_context", "break 不在循环或 switch 语句内。", "break 只能出现在循环或 switch 代码块中。"))
            elif value == "continue" and loop_depth == 0:
                issues.append(_issue(token, "syntax.continue_context", "continue 不在循环内。", "continue 只能出现在 for、while 或 do-while 循环中。"))
            elif value in {"case", "default"} and switch_depth == 0:
                issues.append(_issue(token, "syntax.case_context", f"{value} 不在 switch 语句内。", "case/default 只能出现在 switch 代码块中。"))
            elif value == "return" and function_depth == 0:
                issues.append(_issue(token, "syntax.return_context", "return 不在函数定义内。", "return 只能出现在函数体中。"))
        if value in {"return", "break", "continue"}:
            cursor = index + 1
            while cursor < len(code) and code[cursor].value not in {";", "}"}:
                cursor += 1
            if cursor < len(code) and code[cursor].value == "}":
                issues.append(_issue(code[cursor], "syntax.missing_semicolon", f"{value} 语句缺少分号。", "语句末尾应使用分号。"))

    if delimiter_stack:
        for opener in reversed(delimiter_stack):
            issues.append(_issue(opener, "syntax.unclosed_delimiter", f"{opener.value} 没有找到匹配的闭合符号。", f"应补充 { _MATCHING[opener.value] }。"))
    for opener, _saw_else in preprocessor_stack:
        issues.append(_issue(opener, "syntax.unclosed_preprocessor", "条件编译指令没有对应的 #endif。", "为每个 #if/#ifdef/#ifndef 补充 #endif。"))
    return issues
