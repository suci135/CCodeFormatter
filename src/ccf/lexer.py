"""A small, position-preserving lexer for the C source used by the checker.

This is intentionally not a preprocessor or compiler.  It keeps comments and
preprocessor lines as tokens so later formatting and rule layers can make
different decisions without having to scan raw text again.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    column: int
    end_line: int
    end_column: int


@dataclass(frozen=True)
class LexicalIssue:
    line: int
    column: int
    end_line: int
    end_column: int
    rule_id: str
    message: str
    expected: str = ""


_KEYWORDS = {
    "auto", "break", "case", "char", "const", "continue", "default", "do", "double", "else",
    "enum", "extern", "float", "for", "goto", "if", "inline", "int", "long", "register",
    "restrict", "return", "short", "signed", "sizeof", "static", "struct", "switch", "typedef",
    "union", "unsigned", "void", "volatile", "while", "_Alignas", "_Alignof", "_Atomic",
    "_Bool", "_Complex", "_Generic", "_Imaginary", "_Noreturn", "_Static_assert", "_Thread_local",
}

_NUMBER_RE = re.compile(
    r"(?:0[xX][0-9A-Fa-f]+|(?:\d+\.\d*|\.\d+|\d+)(?:[eE][+-]?\d+)?)(?:[uUlLfF]*)"
)

_OPERATORS = tuple(sorted(
    {
        ">>>=", "<<=", ">>=", "...", "->*", "##", "&&", "||", "++", "--", "->", "+=", "-=",
        "*=", "/=", "%=", "&=", "|=", "^=", "==", "!=", "<=", ">=", "<<", ">>", "::", "##",
        "=>", "<:", ":>", "<%", "%>", "%:", "%:%", "=", "+", "-", "*", "/", "%", "&", "|",
        "^", "~", "!", "<", ">", "?", ":", ";", ",", ".", "(", ")", "[", "]", "{", "}",
        "#",
    }, key=len, reverse=True
))


class Lexer:
    def __init__(self, source: str):
        self.source = source
        self.index = 0
        self.line = 1
        self.column = 1
        self.tokens: list[Token] = []
        self.issues: list[LexicalIssue] = []

    def tokenize(self) -> list[Token]:
        while self.index < len(self.source):
            char = self.source[self.index]
            if char in " \t\r\n":
                self._advance()
                continue
            if self._at_line_start() and char == "#":
                self._scan_preprocessor()
            elif self.source.startswith("//", self.index):
                self._scan_line_comment()
            elif self.source.startswith("/*", self.index):
                self._scan_block_comment()
            elif char == '"' or char == "'":
                self._scan_literal(char)
            elif char.isalpha() or char == "_":
                self._scan_identifier()
            elif char.isdigit() or (char == "." and self._peek(1).isdigit()):
                self._scan_number()
            else:
                self._scan_operator_or_unknown()
        self.tokens.append(Token("EOF", "", self.line, self.column, self.line, self.column))
        return self.tokens

    def _peek(self, offset: int = 0) -> str:
        position = self.index + offset
        return self.source[position] if position < len(self.source) else ""

    def _advance(self) -> str:
        char = self.source[self.index]
        self.index += 1
        if char == "\n":
            self.line += 1
            self.column = 1
        else:
            self.column += 1
        return char

    def _at_line_start(self) -> bool:
        position = self.index - 1
        while position >= 0 and self.source[position] in " \t\r":
            position -= 1
        return position < 0 or self.source[position] == "\n"

    def _emit(self, kind: str, start_line: int, start_column: int, value: str) -> None:
        self.tokens.append(Token(kind, value, start_line, start_column, self.line, self.column))

    def _consume_until_newline(self) -> str:
        start = self.index
        while self.index < len(self.source) and self._peek() != "\n":
            self._advance()
        return self.source[start:self.index]

    def _scan_preprocessor(self) -> None:
        start_line, start_column = self.line, self.column
        parts: list[str] = []
        while True:
            part = self._consume_until_newline()
            parts.append(part)
            # A preprocessor line continues only when the final backslash is
            # not itself escaped. Keep the newline in the token value so the
            # token still represents the complete directive.
            backslashes = len(part) - len(part.rstrip("\\"))
            if backslashes % 2 == 0 or self.index >= len(self.source):
                break
            parts.append(self._advance())
        value = "".join(parts)
        self._emit("PREPROCESSOR", start_line, start_column, value)

    def _scan_line_comment(self) -> None:
        start_line, start_column = self.line, self.column
        start = self.index
        while self.index < len(self.source) and self._peek() != "\n":
            self._advance()
        self._emit("COMMENT", start_line, start_column, self.source[start:self.index])

    def _scan_block_comment(self) -> None:
        start_line, start_column = self.line, self.column
        start = self.index
        self._advance()
        self._advance()
        closed = False
        while self.index < len(self.source):
            if self.source.startswith("*/", self.index):
                self._advance()
                self._advance()
                closed = True
                break
            self._advance()
        value = self.source[start:self.index]
        self._emit("COMMENT", start_line, start_column, value)
        if not closed:
            self.issues.append(LexicalIssue(
                start_line, start_column, self.line, self.column,
                "lexical.unterminated_comment", "块注释没有正确结束。", "使用 */ 结束块注释。"
            ))

    def _scan_literal(self, quote: str) -> None:
        start_line, start_column = self.line, self.column
        start = self.index
        self._advance()
        closed = False
        while self.index < len(self.source):
            char = self._advance()
            if char == "\\" and self.index < len(self.source):
                self._advance()
            elif char == quote:
                closed = True
                break
            elif char == "\n":
                break
        kind = "STRING" if quote == '"' else "CHAR"
        self._emit(kind, start_line, start_column, self.source[start:self.index])
        if not closed:
            self.issues.append(LexicalIssue(
                start_line, start_column, self.line, self.column,
                "lexical.unterminated_literal", "字符串或字符常量没有正确结束。", f"使用 {quote} 结束字面量。"
            ))

    def _scan_identifier(self) -> None:
        start_line, start_column = self.line, self.column
        start = self.index
        while self.index < len(self.source) and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        value = self.source[start:self.index]
        self._emit("KEYWORD" if value in _KEYWORDS else "IDENTIFIER", start_line, start_column, value)

    def _scan_number(self) -> None:
        start_line, start_column = self.line, self.column
        match = _NUMBER_RE.match(self.source, self.index)
        if not match:
            self._scan_operator_or_unknown()
            return
        value = match.group(0)
        for _ in value:
            self._advance()
        self._emit("NUMBER", start_line, start_column, value)

    def _scan_operator_or_unknown(self) -> None:
        start_line, start_column = self.line, self.column
        operator = next((item for item in _OPERATORS if self.source.startswith(item, self.index)), None)
        if operator:
            for _ in operator:
                self._advance()
            self._emit("OPERATOR", start_line, start_column, operator)
            return
        value = self._advance()
        self._emit("UNKNOWN", start_line, start_column, value)
        self.issues.append(LexicalIssue(
            start_line, start_column, start_line, start_column + 1,
            "lexical.unknown_character", f"无法识别的字符：{value}", "请使用合法的 C 语言字符或运算符。"
        ))


def tokenize(source: str) -> tuple[list[Token], list[LexicalIssue]]:
    """Return tokens and recoverable lexical issues for *source*."""
    lexer = Lexer(source)
    return lexer.tokenize(), lexer.issues


lex = tokenize
