"""User-visible catalog of the supplied C coding rules.

``enabled`` means the current checker can report this rule. Keeping planned
rules here gives the UI stable ids without pretending they are implemented.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RuleInfo:
    rule_id: str
    title: str
    description: str
    enabled: bool = True


RULE_CATALOG = (
    RuleInfo("include.header_first", "头文件先引用", "代码文件开头先引用头文件。"),
    RuleInfo("include.relative_path", "相对路径引用", "禁止在 include 中使用绝对路径。"),
    RuleInfo("include.duplicate", "重复头文件引用", "同一个头文件只引用一次。"),
    RuleInfo("header.include_guard", "头文件保护", "头文件使用匹配的 #ifndef/#define。"),
    RuleInfo("header.guard_comment", "保护宏注释", "文件末尾 #endif 标注对应宏名。"),
    RuleInfo("file.matching_header", "匹配头文件", "代码文件优先拥有同名头文件。"),
    RuleInfo("indentation.no_tabs", "禁止 Tab", "缩进使用空格，不使用 Tab。"),
    RuleInfo("indentation.width", "四空格缩进", "每一级缩进使用 4 个空格。"),
    RuleInfo("identifier.ascii_only", "ASCII 标识符", "变量、函数和定义名称使用 ASCII 字符。"),
    RuleInfo("name.forbidden", "名称避让", "禁止使用 I、l、O、o 和 C 语言关键字作为名称。"),
    RuleInfo("variable.initialized", "定义即初始化", "变量定义时设置初始值。"),
    RuleInfo("type.fixed_width", "定长类型", "优先使用 stdint.h 中的定长整数类型。"),
    RuleInfo("macro.global_only", "宏放在全局", "宏定义不能出现在函数体内。"),
    RuleInfo("macro.uppercase", "宏名大写", "宏名称使用大写字母和下划线。"),
    RuleInfo("control.braces.required", "控制语句加括号", "if/else/for/while/switch 必须使用大括号。"),
    RuleInfo("control.avoid_goto", "避免 goto", "使用结构化控制流替代不必要的 goto。"),
    RuleInfo("condition.no_assignment", "条件禁止赋值", "条件表达式中不进行赋值。"),
    RuleInfo("condition.explicit_logic", "条件表达式明确", "不要直接把变量当作条件。"),
    RuleInfo("function.no_pointer_return", "禁止指针返回", "函数返回类型不直接使用指针。"),
    RuleInfo("function.comment.required", "函数需要注释", "函数定义前说明参数、返回值和功能。"),
    RuleInfo("syntax.unclosed_delimiter", "分隔符闭合", "括号、大括号和方括号必须正确闭合。"),
    RuleInfo("syntax.mismatched_delimiter", "分隔符匹配", "括号、大括号和方括号必须成对匹配。"),
    RuleInfo("syntax.preprocessor_branch", "条件编译分支", "#else/#elif/#endif 必须对应条件编译开始指令。"),
    RuleInfo("syntax.unmatched_endif", "条件编译闭合", "每个 #endif 必须对应一个条件编译开始指令。"),
    RuleInfo("syntax.unclosed_preprocessor", "条件编译结束", "每个 #if/#ifdef/#ifndef 必须以 #endif 结束。"),
    RuleInfo("syntax.break_context", "break 使用位置", "break 只能出现在循环或 switch 代码块中。"),
    RuleInfo("syntax.continue_context", "continue 使用位置", "continue 只能出现在循环代码块中。"),
    RuleInfo("syntax.case_context", "case 使用位置", "case/default 只能出现在 switch 代码块中。"),
    RuleInfo("syntax.return_context", "return 使用位置", "return 只能出现在函数体中。"),
    RuleInfo("syntax.missing_semicolon", "语句终止", "需要分号结束的语句必须使用分号。"),
    RuleInfo("lexical.unterminated_literal", "字面量完整", "字符串、字符常量和块注释必须正确结束。"),
    RuleInfo("declaration.scope", "变量定义位置", "全局变量在文件开头，局部变量在函数开头定义。", False),
    RuleInfo("name.scope_case", "作用域命名大小写", "全局名称首字母大写，局部变量全部小写。", False),
    RuleInfo("type.literal_suffix", "数值类型后缀", "浮点和无符号数值使用明确的类型后缀。", False),
    RuleInfo("function.parameter_validation", "参数合法性检查", "函数应检查输入参数和边界条件。", False),
    RuleInfo("switch.case_break", "case 结束方式", "case 代码段应以 break 结束，连续 case 应相邻。", False),
    RuleInfo("condition.parentheses", "运算优先级明确", "比较和逻辑运算使用括号明确优先级。", False),
    RuleInfo("condition.float_compare", "浮点比较", "避免直接比较两个浮点数是否相等。"),
    RuleInfo("pointer.max_level", "指针层级", "尽量少用指针，禁止使用二级及以上指针。"),
    RuleInfo("pointer.assignment_safety", "指针赋值安全", "指针不参与不安全的逻辑比较、转换或浮点赋值。", False),
    RuleInfo("variable.const_volatile", "变量限定符", "只读数据使用 const，寄存器读取按需使用 volatile。", False),
    RuleInfo("variable.enum_values", "枚举值明确", "枚举项目应设置明确的值。", False),
    RuleInfo("safety.state_encoding", "状态值容错", "状态变量考虑码间距、校验和错误恢复。", False),
    RuleInfo("comment.function", "函数注释完整", "函数注释说明输入参数、返回值和主要功能。", False),
    RuleInfo("comment.consistency", "注释保持一致", "代码修改时同步修改相关注释。", False),
    RuleInfo("delivery.no_test_code", "清理测试代码", "交付软件中不保留测试用代码。", False),
)
