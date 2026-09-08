"""The tokenizer is the foundation; if it lies, everything above it lies."""

import pytest

from codecartographer.languages import BY_NAME
from codecartographer.tokenizer import check_balance, find_matching, scan


def java():
    return BY_NAME["java"].syntax


def test_mask_preserves_offsets_and_lines():
    src = 'class A {\n  // note\n  String s = "text";\n}\n'
    result = scan(src, java())
    assert len(result.mask) == len(src)
    assert result.mask.count("\n") == src.count("\n")
    assert result.line_of(src.index("String")) == 3


def test_braces_inside_strings_are_not_code():
    """The bug that made the old checker report phantom syntax errors."""
    src = 'class A { String s = "}}}{{{"; }'
    result = scan(src, java())
    assert result.mask.count("{") == 1
    assert result.mask.count("}") == 1
    assert check_balance(result) == []


def test_keyword_inside_a_comment_is_invisible():
    src = "class A {\n  // if (x) { for (;;) {} }\n  void f() {}\n}"
    result = scan(src, java())
    assert "if" not in result.mask.split("\n")[1]
    assert check_balance(result) == []


def test_escaped_quote_does_not_end_the_string():
    src = r'class A { String s = "a \" b"; int n = 1; }'
    result = scan(src, java())
    assert check_balance(result) == []
    assert "int n" in result.mask


def test_unterminated_string_is_reported_once():
    src = 'class A {\n  String s = "never closed;\n}\n'
    problems = check_balance(scan(src, java()))
    codes = [c for c, _, _ in problems]
    assert "unterminated-string" in codes


def test_unclosed_brace_points_at_the_innermost_opener():
    src = "class A {\n  void f() {\n    if (x) {\n  }\n}\n"
    problems = check_balance(scan(src, java()))
    assert any(code == "unclosed-delimiter" for code, _, _ in problems)


def test_mismatched_delimiter_is_named():
    src = "class A { void f( ) { int a[2}; } }"
    problems = check_balance(scan(src, java()))
    assert any("mismatched" in code or "unexpected" in code
               for code, _, _ in problems)


def test_find_matching_skips_nested_pairs():
    mask = "{ a { b } c }"
    assert find_matching(mask, 0) == len(mask)


def test_comment_and_blank_line_counts():
    src = "// one\n\n/* two\n   three */\ncode();\n"
    result = scan(src, java())
    assert 1 in result.comment_lines
    assert 2 in result.blank_lines
    assert result.code_lines == 1


class TestLegacyQuirks:
    def test_cobol_fixed_format_ignores_sequence_area(self):
        """Columns 1-6 and 73-80 are card metadata, not code."""
        syntax = BY_NAME["cobol"].syntax

        def card(sequence, indicator, code, identification):
            return (sequence.ljust(6)[:6] + indicator +
                    code.ljust(65)[:65] + identification.ljust(8)[:8])

        src = "\n".join([
            card("000100", " ", "       IDENTIFICATION DIVISION.", "SEQ00010"),
            card("000200", "*", "       THIS WHOLE LINE IS A COMMENT", "SEQ00020"),
        ]) + "\n"
        result = scan(src, syntax)
        assert "000100" not in result.mask
        assert "SEQ00010" not in result.mask
        assert "IDENTIFICATION DIVISION" in result.mask
        assert 2 in result.comment_lines

    def test_vb_doubles_quotes_to_escape_them(self):
        syntax = BY_NAME["vb"].syntax
        src = 'Dim s As String\ns = "he said ""hi"" loudly"\nDim n As Integer\n'
        result = scan(src, syntax)
        assert "Dim n As Integer" in result.mask
        assert "loudly" not in result.mask

    def test_vb_rem_needs_a_word_boundary(self):
        syntax = BY_NAME["vb"].syntax
        result = scan("REMOVE = 1\nREM this is a comment\n", syntax)
        assert "REMOVE" in result.mask
        assert 2 in result.comment_lines

    def test_pascal_nests_nothing_but_closes_both_comment_styles(self):
        syntax = BY_NAME["pascal"].syntax
        result = scan("{ note } x := 1; (* other *) y := 2;", syntax)
        assert "note" not in result.mask
        assert "other" not in result.mask
        assert "x := 1" in result.mask

    def test_fortran_column_one_comment(self):
        syntax = BY_NAME["fortran"].syntax
        src = "C     this is a comment\n      X = 1\nCALL FOO\n"
        result = scan(src, syntax)
        assert 1 in result.comment_lines
        # `CALL` starts in column 1 but `C` must not swallow it.
        assert "CALL FOO" in result.mask

    def test_sql_doubles_quotes(self):
        syntax = BY_NAME["sql"].syntax
        result = scan("SELECT 'it''s fine' FROM dual; -- trailing\n", syntax)
        assert "FROM dual" in result.mask
        assert "trailing" not in result.mask

    def test_python_triple_quoted_string(self):
        syntax = BY_NAME["python"].syntax
        src = 'x = """\nif True:\n    nope()\n"""\ny = 1\n'
        result = scan(src, syntax)
        assert "nope" not in result.mask
        assert "y = 1" in result.mask
