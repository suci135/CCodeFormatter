"""Conservative, source-only checks derived from the supplied C rules."""

from __future__ import annotations

import re
from pathlib import Path

from .lexer import tokenize
from .models import Diagnostic
from .parser import analyze_syntax


_CONTROL_RE = re.compile(r"\b(if|for|while|switch)\s*\(")
_DO_RE = re.compile(r"^\s*do\b")
_ASSIGNMENT_IN_CONDITION_RE = re.compile(r"\b(if|while)\s*\([^)]*(?<![=!<>])=(?!=)[^)]*\)")
_DIRECT_CONDITION_RE = re.compile(r"\b(if|while)\s*\(\s*[A-Za-z_]\w*\s*\)")
_RETURN_POINTER_RE = re.compile(r"^\s*(?:static\s+)?[A-Za-z_]\w*\s*\*+\s*[A-Za-z_]\w*\s*\(")
_SCALAR_DECL_RE = re.compile(
    r"^\s*(?:(?:static|const|volatile|extern)\s+)*(int|float)\s+([A-Za-z_]\w*)\s*(=|;|\[)"
)
_DECL_RE = re.compile(
    r"^\s*(?:(?:static|const|volatile|extern)\s+)*(?:u?int\d+_t|uint_least\d+_t|int_least\d+_t|char|short|long|float|double|bool|size_t)\s+([A-Za-z_]\w*)\s*(=|;|\[)"
)
_FUNCTION_RE = re.compile(
    r"^\s*(?:(?:static|inline|extern)\s+)*[A-Za-z_]\w*(?:\s*\*)?\s+[A-Za-z_]\w*\s*\([^;]*\)\s*$"
)
_FLOAT_DECL_RE = re.compile(r"\b(?:float|double)\s+([A-Za-z_]\w*)(?!\s*\()")
_FLOAT_COMPARE_RE = re.compile(r"\b([A-Za-z_]\w*)\b\s*(?:==|!=)|(?:==|!=)\s*\b([A-Za-z_]\w*)\b")
_MULTI_POINTER_RE = re.compile(r"\b(?:const\s+)?[A-Za-z_]\w*(?:\s+[A-Za-z_]\w*)*\s+\*\s*\*")
_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else",
    "enum", "extern", "float", "for", "goto", "if", "int", "long", "register", "return", "short",
    "signed", "sizeof", "static", "struct", "switch", "typedef", "union", "unsigned", "void", "volatile",
    "while", "inline", "restrict", "_Bool", "_Complex", "_Imaginary",
}
SAFE_FIX_RULES = frozenset({
    "indentation.no_tabs",
    "indentation.width",
    "control.braces.required",
})


def _diagnostic(path: Path, line: int, column: int, severity: str, rule_id: str, message: str, expected: str = "") -> Diagnostic:
    return Diagnostic(
        path,
        line,
        column,
        line,
        max(column + 1, column),
        severity,
        rule_id,
        message,
        expected,
        rule_id in SAFE_FIX_RULES,
    )


def _mask_code(source: str) -> str:
    """Blank comments and literals while preserving positions and newlines."""
    result: list[str] = []
    i = 0
    state = "code"
    while i < len(source):
        char = source[i]
        next_char = source[i + 1] if i + 1 < len(source) else ""
        if state == "code":
            if char == "/" and next_char == "/":
                result.extend("  ")
                i += 2
                state = "line_comment"
            elif char == "/" and next_char == "*":
                result.extend("  ")
                i += 2
                state = "block_comment"
            elif char in {'"', "'"}:
                result.append(" ")
                i += 1
                state = "string" if char == '"' else "char"
            else:
                result.append(char)
                i += 1
        elif state == "line_comment":
            if char == "\n":
                result.append("\n")
                i += 1
                state = "code"
            else:
                result.append(" ")
                i += 1
        elif state == "block_comment":
            if char == "*" and next_char == "/":
                result.extend("  ")
                i += 2
                state = "code"
            else:
                result.append("\n" if char == "\n" else " ")
                i += 1
        else:
            if char == "\\" and i + 1 < len(source):
                result.extend("  ")
                i += 2
            elif (state == "string" and char == '"') or (state == "char" and char == "'"):
                result.append(" ")
                i += 1
                state = "code"
            else:
                result.append("\n" if char == "\n" else " ")
                i += 1
    return "".join(result)


def _next_code_line(lines: list[str], index: int) -> str:
    for candidate in lines[index + 1 : index + 3]:
        if candidate.strip():
            return candidate.strip()
    return ""


def check_source(path: Path, source: str) -> list[Diagnostic]:
    """Run the safe, local checks that can be explained with a source location."""
    diagnostics: list[Diagnostic] = []
    tokens, lexical_issues = tokenize(source)
    for issue in lexical_issues:
        diagnostics.append(Diagnostic(
            path,
            issue.line,
            issue.column,
            issue.end_line,
            issue.end_column,
            "error",
            issue.rule_id,
            issue.message,
            issue.expected,
        ))
    for issue in analyze_syntax(tokens):
        diagnostics.append(Diagnostic(
            path,
            issue.line,
            issue.column,
            issue.end_line,
            issue.end_column,
            "error",
            issue.rule_id,
            issue.message,
            issue.expected,
        ))
    lines = source.splitlines() or [""]
    masked_lines = _mask_code(source).splitlines() or [""]
    suffix = path.suffix.lower()
    brace_depth = 0
    float_variables: set[str] = set()
    included_headers: set[str] = set()
    preprocessor_continuation = False

    for line_number, (raw, masked) in enumerate(zip(lines, masked_lines), start=1):
        # A continued #define is one logical preprocessor line. Its
        # continuation lines may contain C-looking text, but must not produce
        # diagnostics for the surrounding source file.
        is_preprocessor = preprocessor_continuation or bool(re.match(r"^\s*#", raw))
        preprocessor_continuation = bool(is_preprocessor and re.search(r"\\\s*$", masked))
        if "\t" in raw:
            diagnostics.append(_diagnostic(path, line_number, raw.index("\t") + 1, "error", "indentation.no_tabs", "缩进中使用了 Tab 字符。", "使用 4 个空格表示一级缩进。"))
        if masked.strip() and not raw.startswith("\t"):
            spaces = len(raw) - len(raw.lstrip(" "))
            if spaces % 4:
                diagnostics.append(_diagnostic(path, line_number, 1, "warning", "indentation.width", "缩进空格数不是 4 的倍数。", "每一级缩进使用 4 个空格。"))
        non_ascii = next((index for index, char in enumerate(masked) if ord(char) > 127), None)
        if non_ascii is not None:
            diagnostics.append(_diagnostic(path, line_number, non_ascii + 1, "error", "identifier.ascii_only", "代码中出现了非 ASCII 字符。", "变量、函数和各类定义名称只能使用字母、数字和下划线。"))
        if re.match(r"^\s*#include\s*[<\"](?:[A-Za-z]:[\\/]|/|\\\\)", raw):
            diagnostics.append(_diagnostic(path, line_number, raw.find("#include") + 1, "error", "include.relative_path", "头文件引用使用了绝对路径。", "头文件引用必须使用相对路径。"))
        include_match = re.match(r'^\s*#include\s*([<\"][^>\"]+[>\"])', raw)
        if include_match:
            header_name = include_match.group(1)
            if header_name in included_headers:
                diagnostics.append(_diagnostic(path, line_number, max(1, masked.find("#include") + 1), "warning", "include.duplicate", f"头文件 {header_name} 被重复引用。", "同一个头文件只保留一次 include。"))
            included_headers.add(header_name)
        if not is_preprocessor and re.match(r"^\s*goto\b", masked):
            diagnostics.append(_diagnostic(path, line_number, masked.find("goto") + 1, "warning", "control.avoid_goto", "检测到标号跳转。", "尽量使用结构化控制流，减少 goto。"))
        macro = re.match(r"^\s*#define\s+([A-Za-z_]\w*)", masked)
        if macro and brace_depth > 0:
            diagnostics.append(_diagnostic(path, line_number, max(1, masked.find("#define") + 1), "error", "macro.global_only", "宏定义出现在函数作用域内。", "所有宏定义应放在全局作用域。"))
        if macro and macro.group(1) != macro.group(1).upper():
            diagnostics.append(_diagnostic(path, line_number, max(1, masked.find(macro.group(1)) + 1), "warning", "macro.uppercase", "宏定义名称没有全部使用大写字母。", "宏定义名称应全部使用大写字符和下划线。"))
        if not is_preprocessor and _ASSIGNMENT_IN_CONDITION_RE.search(masked):
            diagnostics.append(_diagnostic(path, line_number, max(1, masked.find("=") + 1), "error", "condition.no_assignment", "条件表达式中进行了赋值。", "条件中应使用比较运算，赋值请放到独立语句。"))
        if not is_preprocessor and _DIRECT_CONDITION_RE.search(masked):
            diagnostics.append(_diagnostic(path, line_number, max(1, masked.find("(") + 1), "warning", "condition.explicit_logic", "条件语句直接判断变量。", "请明确写出比较或逻辑表达式。"))
        if not is_preprocessor:
            for float_match in _FLOAT_DECL_RE.finditer(masked):
                float_variables.add(float_match.group(1))
        if not is_preprocessor and float_variables and _FLOAT_COMPARE_RE.search(masked) and any(
            re.search(rf"\b{re.escape(name)}\b\s*(?:==|!=)|(?:==|!=)\s*\b{re.escape(name)}\b", masked)
            for name in float_variables
        ):
            diagnostics.append(_diagnostic(path, line_number, max(1, masked.find("==") + 1 if "==" in masked else masked.find("!=") + 1), "warning", "condition.float_compare", "浮点数使用了直接相等比较。", "请使用允许误差的差值比较，而不是直接使用 == 或 !=。"))
        if not is_preprocessor and _MULTI_POINTER_RE.search(masked):
            diagnostics.append(_diagnostic(path, line_number, max(1, masked.find("*") + 1), "warning", "pointer.max_level", "检测到二级或更高层级指针。", "按规范应避免使用二级及以上指针，优先改用结构体或明确的数据接口。"))
        if not is_preprocessor and _RETURN_POINTER_RE.match(masked):
            diagnostics.append(_diagnostic(path, line_number, 1, "error", "function.no_pointer_return", "函数返回值声明为指针。", "按规范，函数不得直接返回指针类型。"))
        scalar = None if is_preprocessor else _SCALAR_DECL_RE.match(masked)
        if scalar:
            type_name, variable_name, terminator = scalar.groups()
            if type_name == "int":
                diagnostics.append(_diagnostic(path, line_number, 1, "warning", "type.fixed_width", f"变量 {variable_name} 使用了无显性长度的 {type_name} 类型。", "优先使用 stdint.h 中的定长类型。"))
            if terminator == ";":
                diagnostics.append(_diagnostic(path, line_number, max(1, masked.find(variable_name) + 1), "warning", "variable.initialized", f"变量 {variable_name} 定义时没有设置初始值。", "变量定义时应同时设置初始值。"))
        declaration = None if is_preprocessor else _DECL_RE.match(masked)
        if declaration:
            variable_name = declaration.group(1)
            if variable_name in {"I", "l", "O", "o"} or variable_name in _KEYWORDS:
                diagnostics.append(_diagnostic(path, line_number, max(1, masked.find(variable_name) + 1), "error", "name.forbidden", f"名称 {variable_name} 不符合命名限制。", "禁止使用 I、l、O、o 或 C 语言关键字作为名称。"))
        control = None if is_preprocessor else _CONTROL_RE.search(masked)
        if control and not re.search(r"}\s*while\b", masked):
            position = masked.find(")", control.end())
            if position >= 0 and "{" not in masked[position:] and not _next_code_line(masked_lines, line_number - 1).startswith("{"):
                diagnostics.append(_diagnostic(path, line_number, control.start() + 1, "error", "control.braces.required", "条件或循环语句没有使用大括号包围作用域。", "if、else、for、switch、while 的代码块必须使用大括号。"))
        if not is_preprocessor and _DO_RE.match(masked) and "{" not in masked and not _next_code_line(masked_lines, line_number - 1).startswith("{"):
            diagnostics.append(_diagnostic(path, line_number, max(1, masked.find("do") + 1), "error", "control.braces.required", "do 循环没有使用大括号包围作用域。", "do-while 的循环体必须使用大括号。"))
        if not is_preprocessor and re.match(r"^\s*else\b", masked) and "{" not in masked and not _next_code_line(masked_lines, line_number - 1).startswith("{"):
            diagnostics.append(_diagnostic(path, line_number, max(1, masked.find("else") + 1), "error", "control.braces.required", "else 分支没有使用大括号。", "else 的代码块必须使用大括号。"))
        if not is_preprocessor and _FUNCTION_RE.match(masked) and not masked.lstrip().startswith(("if", "for", "while", "switch")):
            previous = lines[line_number - 2].strip() if line_number > 1 else ""
            has_comment = previous.startswith(("//", "/*", "*")) or previous.endswith(("*/",))
            if not previous or not has_comment:
                diagnostics.append(_diagnostic(path, line_number, 1, "warning", "function.comment.required", "函数定义前没有找到注释。", "注释应说明函数输入参数、返回值和主要功能。"))
        if not is_preprocessor:
            brace_depth += masked.count("{") - masked.count("}")

    if suffix == ".c":
        include_lines = [index for index, line in enumerate(masked_lines) if re.match(r"^\s*#include\b", line)]
        if include_lines:
            first_include = include_lines[0]
            for index, line in enumerate(masked_lines[:first_include]):
                if line.strip() and not line.lstrip().startswith("#"):
                    diagnostics.append(_diagnostic(path, index + 1, 1, "error", "include.header_first", "在头文件引用之前出现了定义或代码。", "头文件应在代码文件开头引用。"))
                    break
        header_path = path.with_suffix(".h")
        if not header_path.exists():
            diagnostics.append(_diagnostic(path, 1, 1, "warning", "file.matching_header", "没有找到同名头文件。", "若无特殊需求，代码文件应有对应的头文件。"))

    if suffix == ".h":
        significant = [
            (index + 1, masked_line.strip(), raw_line.strip())
            for index, (masked_line, raw_line) in enumerate(zip(masked_lines, lines))
            if masked_line.strip()
        ]
        guard_name = None
        if not significant or not significant[0][1].startswith("#ifndef"):
            diagnostics.append(_diagnostic(path, 1, 1, "error", "header.include_guard", "头文件开头没有 include guard。", "文件开头应使用 #ifndef 和 #define 防止重复包含。"))
        else:
            match = re.match(r"#ifndef\s+([A-Za-z_]\w*)", significant[0][1])
            guard_name = match.group(1) if match else None
            define_matches = (
                len(significant) >= 2
                and re.match(rf"#define\s+{re.escape(guard_name)}\b", significant[1][1])
            ) if guard_name else False
            if not define_matches:
                diagnostics.append(_diagnostic(path, significant[0][0], 1, "error", "header.include_guard", "include guard 的 #ifndef/#define 不匹配。", "#define 后应使用与 #ifndef 相同的宏名。"))
        guard_comment = (
            re.search(
                r"#endif\s*(?://\s*([A-Za-z_]\w*)|/\*\s*([A-Za-z_]\w*)\s*\*/)$",
                significant[-1][2],
            )
            if significant
            else None
        )
        comment_guard_name = (
            (guard_comment.group(1) or guard_comment.group(2))
            if guard_comment
            else None
        )
        if significant and (
            guard_comment is None
            or (guard_name is not None and comment_guard_name != guard_name)
        ):
            diagnostics.append(_diagnostic(path, significant[-1][0], 1, "warning", "header.guard_comment", "文件末尾的 #endif 没有注释对应的 guard 宏。", "末尾应写成 #endif // GUARD_NAME。"))

    return diagnostics
