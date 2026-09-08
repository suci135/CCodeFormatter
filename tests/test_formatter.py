import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from ccf.files import (
    format_file,
    overwrite_file,
    read_source,
    read_source_with_metadata,
    save_source,
    source_digest,
    SourceMetadata,
    write_source_preserving,
)
from ccf.checker import check_source
from ccf.diff import diff_opcodes, unified_diff
from ccf.formatter import TemplateStyle, format_c_code, safe_repair_c_code
from ccf.lexer import tokenize
from ccf.parser import analyze_syntax
from ccf.templates import load_project_profile, load_template, save_project_config, save_templates, template_dir_for_use
from ccf.default_templates import DEFAULT_TEMPLATES
from ccf.models import Diagnostic
from ccf.ui import BatchFormatThread, _diagnostic_matches_query, _scan_folder_code_files


class FormatterTests(unittest.TestCase):
    def test_control_braces_and_indentation(self):
        source = 'int main(){if(value==1){return 0;}else{return 1;}}\n'
        result = format_c_code(source, TemplateStyle())
        self.assertIn('int main()\n{', result)
        self.assertIn('    if (value == 1)\n    {', result)
        self.assertIn('        return 0;', result)

    def test_formatter_completes_single_statement_control_braces(self):
        source = 'int main(){if(value)return 0;else return 1;}\n'
        result = format_c_code(source, TemplateStyle())
        self.assertIn('    if (value)', result)
        self.assertIn('    {\n        return 0;', result)
        self.assertIn('    }\n    else\n    {', result)

    def test_formatter_puts_statement_after_block_on_new_line(self):
        source = 'uint16_t Read(void){if(error)return 0U;return value;}\n'
        result = format_c_code(source, TemplateStyle())
        self.assertIn('        return 0U;\n    }\n    return value;', result)

    def test_ordinary_while_after_a_block_starts_on_new_line(self):
        source = 'void f(void){if(error){return;}while(ready){work();}}\n'
        result = format_c_code(source, TemplateStyle())
        self.assertIn('        return;\n    }\n    while (ready)\n    {', result)

    def test_closing_brace_with_trailing_comment_keeps_block_alignment(self):
        source = (
            "if (char1 >= 'a' && char1 <= 'z')\n"
            "{\n"
            "    char1 -= 32;\n"
            "    } // 'a' - 'A'\n"
            "if (char2 >= 'a' && char2 <= 'z')\n"
            "{\n"
            "    char2 -= 32;\n"
            "    } // 'a' - 'A'\n"
        )
        result = format_c_code(source, TemplateStyle())
        lines = result.splitlines()
        self.assertEqual(lines[0].index("if"), lines[1].index("{"))
        self.assertEqual(lines[0].index("if"), lines[3].index("}"))
        self.assertEqual(lines[4].index("if"), lines[5].index("{"))
        self.assertEqual(lines[4].index("if"), lines[7].index("}"))

    def test_formatter_completes_do_while_body_braces(self):
        result = format_c_code("int f(void){do value++;while(ready);return 0;}\n", TemplateStyle())
        self.assertIn("    do\n    {\n        value++;\n    } while (ready);", result)

    def test_do_while_uses_template_newline_style(self):
        style = TemplateStyle(do_while_on_new_line=True)
        result = format_c_code("int f(void){do{value++;}while(ready);}\n", style)
        self.assertIn("    do\n    {\n        value++;\n    }\n    while (ready);", result)

    def test_template_infers_do_while_newline_style(self):
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            save_templates(
                "int f(void)\n{\n    do\n    {\n        value++;\n    }\n    while (ready);\n}\n",
                "#ifndef SAMPLE_H\n#define SAMPLE_H\n#endif\n",
                template_dir,
            )
            style = TemplateStyle.from_template(template_dir, ".c")
            self.assertTrue(style.do_while_on_new_line)

    def test_safe_repair_only_expands_indent_and_adds_control_braces(self):
        source = 'int main(){\n\tif(value==1)\n\t\treturn 0;\n}\n'
        result = safe_repair_c_code(source)
        self.assertIn('value==1', result)
        self.assertNotIn('value == 1', result)
        self.assertNotIn('\t', result)
        self.assertGreaterEqual(result.count('{'), 2)

    def test_switch_cases_are_indented_inside_switch(self):
        source = 'int main(void){switch(vector){case COMM_SCI_VECTOR_WAKE:sciNotification(vector);break;default:break;}}\n'
        result = format_c_code(source, TemplateStyle())
        self.assertIn('    switch (vector)\n    {', result)
        self.assertIn('        case COMM_SCI_VECTOR_WAKE:', result)
        self.assertIn('            sciNotification(vector);', result)
        self.assertIn('            break;', result)
        self.assertIn('        default:', result)

    def test_control_blocks_inside_switch_cases_keep_nested_indentation(self):
        source = 'int main(void){switch(vector){default:if(high_level){sci->FLR=(~value);}else{sci->FLR=other;}break;}}\n'
        result = format_c_code(source, TemplateStyle())
        self.assertIn('        default:', result)
        self.assertIn('            if (high_level)\n            {', result)
        self.assertIn('                sci->FLR = (~value);', result)
        self.assertIn('            else\n            {', result)

    def test_nested_switch_cases_keep_their_own_indentation(self):
        source = 'void f(int x,int y){switch(x){case 1:switch(y){case 2:break;default:break;}break;default:break;}}\n'
        result = format_c_code(source, TemplateStyle())
        self.assertIn('        case 1:', result)
        self.assertIn('            switch (y)\n            {', result)
        self.assertIn('                case 2:', result)
        self.assertIn('        default:\n            break;', result)

    def test_formatter_keeps_all_braces_for_nested_controls(self):
        source = 'int Read(int x,int y){if(x)if(y)return 1;else return 2;return 0;}\n'
        result = format_c_code(source, TemplateStyle())
        self.assertTrue(result.rstrip().endswith('}'))
        self.assertIn('        }\n    }\n    return 0;', result)

    def test_literals_and_comments_are_kept(self):
        source = 'const char *text="a  b"; // keep  spaces\nchar c=\' \';\n'
        result = format_c_code(source, TemplateStyle())
        self.assertIn('"a  b"', result)
        self.assertIn("' '", result)
        self.assertIn('// keep  spaces', result)

    def test_block_comment_stars_keep_readable_spacing(self):
        result = format_c_code("/*\n * details\n */\nint value = 0;\n", TemplateStyle())
        self.assertIn(" * details\n */", result)

    def test_multiline_comment_keeps_block_indent_and_formats_following_code(self):
        source = "void f(){/* first\n * second\n */if(x){y();}}\n"
        result = format_c_code(source, TemplateStyle())
        self.assertIn("    /* first\n     * second\n     */\n    if (x)", result)
        self.assertIn("        y();", result)

    def test_inline_block_comment_keeps_space_before_following_code(self):
        result = format_c_code("/* note */int value=0;\n", TemplateStyle())
        self.assertIn("/* note */ int value = 0;", result)

    def test_function_parameter_comments_keep_their_comment_column(self):
        source = "void write(\n    uint8_t *rx;                         // 传输一个SPI字节\n    int32_t data;                        // 写入32位数据\n);\n"
        result = format_c_code(source, TemplateStyle())
        lines = result.splitlines()
        self.assertEqual(lines[1].index("//"), source.splitlines()[1].index("//"))
        self.assertEqual(lines[2].index("//"), source.splitlines()[2].index("//"))

    def test_block_comment_preserves_alignment_and_boundary_stars(self):
        source = "/********************************\n\nuint16_t    first       description\n            second\n\n********************************/\n"
        result = format_c_code(source, TemplateStyle())
        self.assertIn("uint16_t    first       description", result)
        self.assertIn("            second", result)
        self.assertIn("\n********************************/\n", result)

    def test_multiline_comment_preserves_template_whitespace(self):
        source = "/*\n * Name                 \n *\n * Value       Description\n */\nint value=0;\n"
        result = format_c_code(source, TemplateStyle())
        self.assertIn(" * Name                 \n", result)
        self.assertIn(" * Value       Description\n", result)

    def test_file_output_does_not_overwrite_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.c'
            source.write_text('int main(){return 0;}\n', encoding='utf-8')
            output = format_file(source, Path(directory))
            self.assertEqual(output.name, 'sample.formatted.c')
            self.assertEqual(source.read_text(encoding='utf-8'), 'int main(){return 0;}\n')
            self.assertIn('return 0;', output.read_text(encoding='utf-8'))

    def test_macro_alignment_uses_template_column(self):
        source = '#define SHORT 1\n#define LONG_NAME 2\n'
        result = format_c_code(source, TemplateStyle(macro_value_column=20))
        self.assertEqual(result.splitlines(), ['#define SHORT       1', '#define LONG_NAME   2'])

    def test_macro_alignment_keeps_spaces_inside_parameter_list(self):
        source = '#define Comm_CAN_1_Write_32(address, data) TCAN_AHB_Write32(address, data)\n'
        result = format_c_code(source, TemplateStyle(macro_value_column=45))
        self.assertEqual(
            result.splitlines(),
            ['#define Comm_CAN_1_Write_32(address, data)   TCAN_AHB_Write32(address, data)'],
        )

    def test_long_macros_expand_the_template_column_instead_of_collapsing(self):
        source = (
            '#define SHORT_PORT hetPORT1 // port\n'
            '#define VERY_LONG_CONTROLLER_CHANNEL_MASK (1U << 12U) // mask\n'
        )
        result = format_c_code(source, TemplateStyle(macro_value_column=20))
        lines = result.splitlines()
        self.assertEqual(lines[0].index("hetPORT1"), lines[1].index("(1U"))
        self.assertEqual(lines[0].index("//"), lines[1].index("//"))

    def test_macro_comments_use_local_group_distance(self):
        source = (
            '#define SHORT 1' + ' ' * 80 + '// short\n'
            '#define LONG_CONTROLLER_MASK (1U << 12U)' + ' ' * 60 + '// long\n'
            '\n'
            '#define NEXT 2' + ' ' * 80 + '// next\n'
        )
        result = format_c_code(source, TemplateStyle(macro_value_column=20))
        first_group, second_group = result.splitlines()[0:2], result.splitlines()[3:4]
        self.assertEqual(first_group[0].index("//"), first_group[1].index("//"))
        self.assertLess(first_group[0].index("//"), 80)
        self.assertLess(second_group[0].index("//"), 40)

    def test_object_macro_expression_is_not_a_parameter_list(self):
        result = format_c_code(
            '#define CHANNEL_MASK (1U << 12U) // mask\n',
            TemplateStyle(macro_value_column=30),
        )
        self.assertIn('#define CHANNEL_MASK          (1U << 12U)  // mask', result)

    def test_formatter_preserves_multiline_macro_continuation_lines(self):
        source = '#define VALUE ' + chr(92) + '\n    (1 + 2)\nint value=VALUE;\n'
        result = format_c_code(source, TemplateStyle())
        self.assertTrue(result.splitlines()[0].endswith('\\'))
        self.assertIn('    (1 + 2)', result)
        self.assertIn('int value = VALUE;', result)

    def test_overwrite_requires_an_explicit_file_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.c'
            source.write_text('int main(){return 0;}\n', encoding='utf-8')
            formatted = format_c_code(read_source(source), TemplateStyle())
            self.assertEqual(source.read_text(encoding='utf-8'), 'int main(){return 0;}\n')
            overwrite_file(source, formatted)
            self.assertEqual(source.read_text(encoding='utf-8'), formatted)

    def test_save_source_writes_a_new_file_without_touching_original(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.c'
            output = Path(directory) / 'saved.c'
            source.write_text('original\n', encoding='utf-8')
            save_source(output, 'formatted\n')
            self.assertEqual(source.read_text(encoding='utf-8'), 'original\n')
            self.assertEqual(output.read_text(encoding='utf-8'), 'formatted\n')

    def test_source_metadata_preserves_utf8_bom_and_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.c'
            source.write_bytes(b'\xef\xbb\xbfint main()\r\n{\r\n}\r\n')
            content, metadata = read_source_with_metadata(source)
            self.assertEqual(content, 'int main()\r\n{\r\n}\r\n')
            self.assertEqual(metadata.encoding, 'utf-8')
            self.assertEqual(metadata.bom, b'\xef\xbb\xbf')
            self.assertEqual(metadata.newline, '\r\n')
            write_source_preserving(source, 'int main()\n{\n}\n', metadata)
            self.assertEqual(source.read_bytes(), b'\xef\xbb\xbfint main()\r\n{\r\n}\r\n')

    def test_source_metadata_preserves_utf16_crlf(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.c'
            source.write_bytes(('int main()\r\n{\r\n}\r\n').encode('utf-16'))
            content, metadata = read_source_with_metadata(source)
            self.assertEqual(metadata.newline, '\r\n')
            write_source_preserving(source, content.replace('\r\n', '\n'), metadata)
            self.assertEqual(source.read_bytes().decode('utf-16'), content)

    def test_source_digest_changes_and_lossy_write_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.c'
            source.write_text('hello\n', encoding='cp1252')
            before = source_digest(source)
            source.write_text('changed\n', encoding='cp1252')
            self.assertNotEqual(before, source_digest(source))
            metadata = SourceMetadata('cp1252', b'', '\n', True)
            with self.assertRaises(UnicodeEncodeError):
                write_source_preserving(source, '汉字\n', metadata)

    def test_overwrite_replaces_source_without_creating_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'sample.c'
            source.write_bytes('int old;\r\n'.encode('gb18030'))
            backup = overwrite_file(source, 'int new;\n')
            self.assertIsNone(backup)
            self.assertFalse(Path(f'{source}.ccf-backup').exists())
            self.assertEqual(read_source(source), 'int new;\r\n')

    def test_custom_templates_round_trip_without_losing_spaces(self):
        with tempfile.TemporaryDirectory() as directory:
            template_dir = Path(directory)
            save_templates("int value=0;\nint main(){if(value==1){return 0;}}\n", "#ifndef SAMPLE_H\n#define SAMPLE_H\n  int field;\n#endif\n", template_dir)
            self.assertEqual(load_template(".c", template_dir), "int value=0;\nint main(){if(value==1){return 0;}}\n")
            self.assertEqual(load_template(".h", template_dir).splitlines()[1], "#define SAMPLE_H")
            style = TemplateStyle.from_template(template_dir, ".c")
            self.assertEqual(style.indent_width, 4)
            self.assertFalse(style.brace_on_new_line)
            self.assertFalse(style.space_before_brace)
            self.assertFalse(style.space_before_control_paren)
            self.assertFalse(style.space_around_operators)
            self.assertIn("int main(){", format_c_code("int main(){return 0;}\n", style))
            self.assertEqual(TemplateStyle.from_template(template_dir, ".h").indent_width, 2)

    def test_default_templates_are_embedded(self):
        with tempfile.TemporaryDirectory() as directory:
            missing_directory = Path(directory) / "missing"
            self.assertEqual(load_template(".c", missing_directory), DEFAULT_TEMPLATES[".c"])
            self.assertEqual(load_template(".h", missing_directory), DEFAULT_TEMPLATES[".h"])
            self.assertEqual(TemplateStyle.from_template(None).indent_width, 4)

    def test_default_templates_cover_conditionals_and_are_stable(self):
        source_c = DEFAULT_TEMPLATES[".c"]
        source_h = DEFAULT_TEMPLATES[".h"]
        self.assertIn("#if defined(", source_c)
        self.assertIn("#elif", source_c)
        self.assertIn("#ifdef", source_h)
        self.assertIn("#ifndef", source_h)
        self.assertIn("#endif", source_c)
        self.assertIn("#endif", source_h)
        for source in (source_c, source_h):
            formatted = format_c_code(source, TemplateStyle())
            self.assertEqual(formatted, format_c_code(formatted, TemplateStyle()))

    def test_diff_opcodes_reports_changed_lines(self):
        operations = diff_opcodes("int main() {\n    return 0;\n}\n", "int main()\n{\n    return 0;\n}\n")
        self.assertTrue(any(tag != "equal" for tag, *_ in operations))

    def test_diff_can_ignore_whitespace_only_changes(self):
        operations = diff_opcodes("int value = 0;\n", "int    value=0;\n", ignore_whitespace=True)
        self.assertEqual(operations, [("equal", 0, 1, 0, 1)])

    def test_unified_diff_is_empty_for_equal_content_and_reviewable_for_changes(self):
        self.assertEqual(unified_diff("same\n", "same\n", "sample.c"), "")
        patch = unified_diff("int value = 0;\n", "int value = 1;\n", "sample.c")
        self.assertIn("--- sample.c (before)\n", patch)
        self.assertIn("+++ sample.c (after)\n", patch)
        self.assertIn("-int value = 0;\n", patch)
        self.assertIn("+int value = 1;\n", patch)

    def test_batch_output_can_preserve_selected_folder_structure(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            source_root = project / "src"
            nested = source_root / "drivers"
            nested.mkdir(parents=True)
            first = source_root / "main.c"
            second = nested / "main.c"
            first.write_text("", encoding="utf-8")
            second.write_text("", encoding="utf-8")
            output_dir = Path(directory) / "formatted"
            outputs = BatchFormatThread.output_paths(
                [first, second],
                output_dir,
                preserve_structure=True,
                source_roots=[source_root],
            )
            self.assertEqual(outputs[0], output_dir / "src" / "main.formatted.c")
            self.assertEqual(outputs[1], output_dir / "src" / "drivers" / "main.formatted.c")

    def test_folder_scan_reports_code_files_without_following_links(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / "nested"
            nested.mkdir()
            (root / "main.c").write_text("", encoding="utf-8")
            (nested / "header.h").write_text("", encoding="utf-8")
            seen = []
            files = _scan_folder_code_files(root, on_file=seen.append)
            self.assertEqual(files, [root / "main.c", nested / "header.h"])
            self.assertEqual(seen, files)

    def test_folder_scan_can_skip_common_generated_directories(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "build").mkdir()
            (root / ".git").mkdir()
            (root / "src" / "main.c").write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (root / "build" / "generated.c").write_text("int generated(void) { return 0; }\n", encoding="utf-8")
            (root / ".git" / "ignored.c").write_text("int ignored(void) { return 0; }\n", encoding="utf-8")

            included = _scan_folder_code_files(root)
            skipped = _scan_folder_code_files(root, skip_generated_dirs=True)

            self.assertIn(root / "build" / "generated.c", included)
            self.assertIn(root / ".git" / "ignored.c", included)
            self.assertEqual(skipped, [root / "src" / "main.c"])

    def test_diagnostic_search_matches_file_name_path_and_multiple_terms(self):
        diagnostic = Diagnostic(
            Path("D:/project/drivers/LAN8671.c"),
            42,
            1,
            42,
            2,
            "warning",
            "control.braces.required",
            "条件语句需要大括号。",
            "请补充大括号。",
        )
        self.assertTrue(_diagnostic_matches_query(diagnostic, "LAN8671"))
        self.assertTrue(_diagnostic_matches_query(diagnostic, "drivers LAN8671"))
        self.assertTrue(_diagnostic_matches_query(diagnostic, "D:/project"))
        self.assertFalse(_diagnostic_matches_query(diagnostic, "other_file"))

    def test_project_config_selects_template_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "sample.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            config = save_project_config(source, "default")
            self.assertEqual(config, project / ".ccfconfig.json")
            self.assertEqual(load_project_profile(source), "default")

    def test_invalid_project_profile_falls_back_to_embedded_template(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            source = project / "sample.c"
            source.write_text("int main(void) { return 0; }\n", encoding="utf-8")
            (project / ".ccfconfig.json").write_text(
                '{"profile": "../../outside"}\n', encoding="utf-8"
            )
            self.assertEqual(load_project_profile(source), "../../outside")
            self.assertIsNone(template_dir_for_use(source))
            self.assertEqual(TemplateStyle.from_template(template_dir_for_use(source), ".c").indent_width, 4)

    def test_checker_reports_source_rule_violations(self):
        source = 'int value;\n#include "C:\\sdk\\board.h"\nint main(){if(value=value)return 0;}\n'
        diagnostics = check_source(Path("sample.c"), source)
        rule_ids = {diagnostic.rule_id for diagnostic in diagnostics}
        self.assertIn("include.header_first", rule_ids)
        self.assertIn("include.relative_path", rule_ids)
        self.assertIn("variable.initialized", rule_ids)
        self.assertIn("condition.no_assignment", rule_ids)

    def test_checker_marks_only_safe_repairs_as_fixable(self):
        diagnostics = check_source(Path("sample.c"), "int main(){if(value)return 0;}\n")
        self.assertTrue(any(item.rule_id == "control.braces.required" and item.fixable for item in diagnostics))
        self.assertFalse(any(item.rule_id == "function.comment.required" and item.fixable for item in diagnostics))

    def test_checker_requires_braces_for_do_while(self):
        diagnostics = check_source(Path("sample.c"), "void f(void) { do value++; while (ready); }\n")
        self.assertTrue(any(item.rule_id == "control.braces.required" for item in diagnostics))

    def test_checker_validates_header_guard(self):
        diagnostics = check_source(Path("sample.h"), "#ifndef SAMPLE_H_\n#define OTHER_H_\n\n#endif\n")
        rule_ids = {diagnostic.rule_id for diagnostic in diagnostics}
        self.assertIn("header.include_guard", rule_ids)
        self.assertIn("header.guard_comment", rule_ids)

    def test_checker_accepts_comment_before_function(self):
        source = "/* returns the value */\nint Foo(void)\n{\n    return 0;\n}\n"
        rule_ids = {diagnostic.rule_id for diagnostic in check_source(Path("sample.c"), source)}
        self.assertNotIn("function.comment.required", rule_ids)

    def test_lexer_keeps_comments_literals_and_longest_operators(self):
        tokens, issues = tokenize('/* note */ const char *s = "a // b"; value >>= 1;\n')
        self.assertFalse(issues)
        self.assertEqual(tokens[0].kind, "COMMENT")
        self.assertIn(("STRING", '"a // b"'), [(token.kind, token.value) for token in tokens])
        self.assertIn(("OPERATOR", ">>="), [(token.kind, token.value) for token in tokens])

    def test_lexer_keeps_continued_preprocessor_directive_together(self):
        source = '#define VALUE ' + chr(92) + '\n    (1 + 2)\nint value;\n'
        tokens, issues = tokenize(source)
        self.assertFalse(issues)
        self.assertEqual(tokens[0].kind, 'PREPROCESSOR')
        self.assertIn('(1 + 2)', tokens[0].value)
        self.assertEqual(tokens[1].value, 'int')

    def test_parser_reports_structure_and_context_errors(self):
        tokens, lexical_issues = tokenize("int main() { break; return 0;\n")
        self.assertFalse(lexical_issues)
        rule_ids = {issue.rule_id for issue in analyze_syntax(tokens)}
        self.assertIn("syntax.break_context", rule_ids)
        self.assertIn("syntax.unclosed_delimiter", rule_ids)

    def test_parser_recovers_context_after_mismatched_delimiter(self):
        tokens, lexical_issues = tokenize('int main(void) { if (value[0} return 0; }\n')
        self.assertFalse(lexical_issues)
        rule_ids = {issue.rule_id for issue in analyze_syntax(tokens)}
        self.assertIn('syntax.mismatched_delimiter', rule_ids)
        self.assertNotIn('syntax.return_context', rule_ids)

    def test_parser_understands_loop_and_switch_contexts(self):
        source = "int main() { for (index = 0; index < 2; index++) { continue; } switch (value) { case 1: break; default: break; } return 0; }"
        tokens, lexical_issues = tokenize(source)
        self.assertFalse(lexical_issues)
        rule_ids = {issue.rule_id for issue in analyze_syntax(tokens)}
        self.assertNotIn("syntax.continue_context", rule_ids)
        self.assertNotIn("syntax.break_context", rule_ids)
        self.assertNotIn("syntax.case_context", rule_ids)

    def test_parser_rejects_duplicate_preprocessor_else_branches(self):
        source = "#if defined(FEATURE)\nint value = 1;\n#else\nint value = 2;\n#else\nint value = 3;\n#endif\n"
        tokens, lexical_issues = tokenize(source)
        self.assertFalse(lexical_issues)
        issues = analyze_syntax(tokens)
        self.assertTrue(any(issue.rule_id == "syntax.preprocessor_branch" for issue in issues))

    def test_parser_rejects_elif_after_preprocessor_else(self):
        source = "#ifdef FEATURE\nint value = 1;\n#else\nint value = 2;\n#elif defined(OTHER)\nint value = 3;\n#endif\n"
        tokens, lexical_issues = tokenize(source)
        self.assertFalse(lexical_issues)
        issues = analyze_syntax(tokens)
        self.assertTrue(any(issue.rule_id == "syntax.preprocessor_branch" for issue in issues))

    def test_parser_does_not_flag_valid_reference_template_contexts(self):
        from ccf.files import read_source

        template = Path(r"D:\Code\C_and_C++_basic\Test\template.c")
        if not template.is_file():
            self.skipTest("reference template is not available on this machine")
        tokens, lexical_issues = tokenize(read_source(template))
        self.assertFalse(lexical_issues)
        rule_ids = {issue.rule_id for issue in analyze_syntax(tokens)}
        self.assertNotIn("syntax.return_context", rule_ids)
        self.assertNotIn("syntax.case_context", rule_ids)

    def test_checker_includes_lexical_and_syntax_diagnostics(self):
        diagnostics = check_source(Path("sample.c"), 'int main() {\n    const char *text = "not closed;\n')
        rule_ids = {diagnostic.rule_id for diagnostic in diagnostics}
        self.assertIn("lexical.unterminated_literal", rule_ids)
        self.assertIn("syntax.unclosed_delimiter", rule_ids)

    def test_checker_handles_do_while_and_float_fields(self):
        source = "typedef struct sample { float decimal; } sample_t;\nvoid f(void) { do { break; } while (0); }\n"
        rule_ids = {diagnostic.rule_id for diagnostic in check_source(Path("sample.c"), source)}
        self.assertNotIn("type.fixed_width", rule_ids)
        self.assertNotIn("control.braces.required", rule_ids)

    def test_checker_reports_duplicate_include_float_compare_and_multi_pointer(self):
        source = (
            '#include "sample.h"\n'
            '#include "sample.h"\n'
            'void check(float value, int **result)\n'
            '{\n'
            '    if (value == 0.0)\n'
            '    {\n'
            '    }\n'
            '}\n'
        )
        rule_ids = {diagnostic.rule_id for diagnostic in check_source(Path("sample.c"), source)}
        self.assertIn("include.duplicate", rule_ids)
        self.assertIn("condition.float_compare", rule_ids)
        self.assertIn("pointer.max_level", rule_ids)

    def test_checker_does_not_apply_function_rules_inside_macro_lines(self):
        source = '#define RETURN_IF(value) if (value) return 0\nint main(void) { return 0; }\n'
        rule_ids = {diagnostic.rule_id for diagnostic in check_source(Path("sample.c"), source)}
        self.assertNotIn("control.braces.required", rule_ids)
        self.assertNotIn("condition.explicit_logic", rule_ids)
        self.assertNotIn("syntax.return_context", rule_ids)

    def test_checker_ignores_continued_macro_body(self):
        source = '#define BODY ' + chr(92) + '\nif (value) return 0' + chr(92) + '\nint main(void) { return 0; }\n'
        rule_ids = {diagnostic.rule_id for diagnostic in check_source(Path("sample.c"), source)}
        self.assertNotIn("control.braces.required", rule_ids)
        self.assertNotIn("condition.explicit_logic", rule_ids)
        self.assertNotIn("include.header_first", rule_ids)

    def test_checker_requires_matching_guard_comment_name(self):
        source = '#ifndef SAMPLE_H\n#define SAMPLE_H\n#endif // OTHER_H\n'
        rule_ids = {diagnostic.rule_id for diagnostic in check_source(Path("sample.h"), source)}
        self.assertIn("header.guard_comment", rule_ids)


if __name__ == '__main__':
    unittest.main()
