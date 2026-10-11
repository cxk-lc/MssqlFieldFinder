"""sqlbuild 单元测试：标识符转义、LIKE 转义、排序规则、语句构造。"""
from __future__ import annotations

import unittest

from src import sqlbuild as sb
from src.models import ColumnRef, MatchMode


class TestQuoteIdent(unittest.TestCase):
    def test_plain(self) -> None:
        self.assertEqual(sb.quote_ident("dbo"), "[dbo]")

    def test_close_bracket_doubled(self) -> None:
        self.assertEqual(sb.quote_ident("a]b"), "[a]]b]")

    def test_injection_attempt_is_neutralized(self) -> None:
        self.assertEqual(
            sb.quote_ident("x]; DROP TABLE t; --"), "[x]]; DROP TABLE t; --]"
        )

    def test_empty(self) -> None:
        self.assertEqual(sb.quote_ident(""), "[]")

    def test_unicode_and_spaces(self) -> None:
        self.assertEqual(sb.quote_ident("订单 表"), "[订单 表]")

    def test_qualified(self) -> None:
        self.assertEqual(sb.quote_qualified("dbo", "a]b"), "[dbo].[a]]b]")


class TestEscapeLike(unittest.TestCase):
    def test_percent(self) -> None:
        self.assertEqual(sb.escape_like("100%"), "100\\%")

    def test_underscore(self) -> None:
        self.assertEqual(sb.escape_like("a_b"), "a\\_b")

    def test_open_bracket(self) -> None:
        self.assertEqual(sb.escape_like("[x]"), "\\[x]")

    def test_backslash_first(self) -> None:
        # 必须先转义转义符自身，否则后续插入的转义符会被二次转义
        self.assertEqual(sb.escape_like("back\\slash"), "back\\\\slash")

    def test_escape_char_before_wildcard(self) -> None:
        self.assertEqual(sb.escape_like("\\%"), "\\\\\\%")

    def test_empty(self) -> None:
        self.assertEqual(sb.escape_like(""), "")

    def test_plain_unchanged(self) -> None:
        self.assertEqual(sb.escape_like("正常中文abc123"), "正常中文abc123")


class TestBuildLikePattern(unittest.TestCase):
    def test_exact_has_no_wildcard(self) -> None:
        self.assertEqual(sb.build_like_pattern("abc", MatchMode.EXACT), "abc")

    def test_fuzzy_wrapped(self) -> None:
        self.assertEqual(sb.build_like_pattern("abc", MatchMode.FUZZY), "%abc%")

    def test_fuzzy_escapes_wildcard(self) -> None:
        self.assertEqual(sb.build_like_pattern("50%", MatchMode.FUZZY), "%50\\%%")


class TestCollation(unittest.TestCase):
    def test_ignore_case(self) -> None:
        self.assertEqual(sb.collation_for(True), "Latin1_General_CI_AS")

    def test_case_sensitive(self) -> None:
        self.assertEqual(sb.collation_for(False), "Latin1_General_CS_AS")

    def test_normalize_strips_ci_suffix(self) -> None:
        self.assertEqual(
            sb.normalize_collation_base("Latin1_General_CI_AS"), "Latin1_General"
        )

    def test_normalize_strips_cs_suffix(self) -> None:
        self.assertEqual(
            sb.normalize_collation_base("Chinese_PRC_CS_AS"), "Chinese_PRC"
        )

    def test_normalize_strips_bin2(self) -> None:
        self.assertEqual(
            sb.normalize_collation_base("Latin1_General_BIN2"), "Latin1_General"
        )

    def test_normalize_trims(self) -> None:
        self.assertEqual(
            sb.normalize_collation_base("  Latin1_General  "), "Latin1_General"
        )

    def test_collation_for_with_full_name_input(self) -> None:
        self.assertEqual(
            sb.collation_for(True, "Chinese_PRC_CI_AS"), "Chinese_PRC_CI_AS"
        )

    def test_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            sb.normalize_collation_base("")

    def test_illegal_chars_raise(self) -> None:
        for bad in ("Latin1 General", "a;b", "a'--", "Latin1=General"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                sb.normalize_collation_base(bad)


class TestContentPredicate(unittest.TestCase):
    def test_exact(self) -> None:
        sql, params = sb.build_content_predicate("Name", "abc", MatchMode.EXACT, True)
        self.assertEqual(sql, "[Name] COLLATE Latin1_General_CI_AS = ?")
        self.assertEqual(params, ("abc",))

    def test_fuzzy(self) -> None:
        sql, params = sb.build_content_predicate("Name", "abc", MatchMode.FUZZY, True)
        self.assertEqual(
            sql, "[Name] COLLATE Latin1_General_CI_AS LIKE ? ESCAPE '\\'"
        )
        self.assertEqual(params, ("%abc%",))

    def test_case_sensitive_flag(self) -> None:
        sql, _ = sb.build_content_predicate("Name", "abc", MatchMode.EXACT, False)
        self.assertIn("COLLATE Latin1_General_CS_AS", sql)

    def test_custom_collation_base(self) -> None:
        sql, _ = sb.build_content_predicate(
            "Name", "abc", MatchMode.EXACT, True, "Chinese_PRC"
        )
        self.assertIn("COLLATE Chinese_PRC_CI_AS", sql)

    def test_column_name_is_escaped(self) -> None:
        sql, _ = sb.build_content_predicate("a]b", "v", MatchMode.EXACT, True)
        self.assertTrue(sql.startswith("[a]]b] COLLATE"))

    def test_value_is_never_interpolated(self) -> None:
        sql, params = sb.build_content_predicate(
            "Name", "' OR 1=1--", MatchMode.EXACT, True
        )
        self.assertNotIn("1=1", sql)
        self.assertEqual(params, ("' OR 1=1--",))


class TestProbeSql(unittest.TestCase):
    ref = ColumnRef("dbo", "订单表", "Remark", "BASE TABLE", "nvarchar")

    def test_default_is_top_with_nolock(self) -> None:
        sql, params = sb.build_probe_sql(self.ref, "x", MatchMode.FUZZY, True)
        self.assertTrue(
            sql.startswith("SELECT TOP (3) [Remark] FROM [dbo].[订单表] WITH (NOLOCK) WHERE "),
            sql,
        )
        self.assertEqual(params, ("%x%",))

    def test_nolock_can_be_disabled(self) -> None:
        sql, _ = sb.build_probe_sql(self.ref, "x", MatchMode.FUZZY, True, with_nolock=False)
        self.assertNotIn("NOLOCK", sql)

    def test_count_rows_mode(self) -> None:
        sql, _ = sb.build_probe_sql(self.ref, "x", MatchMode.EXACT, True, count_rows=True)
        self.assertTrue(sql.startswith("SELECT COUNT_BIG(*) FROM [dbo].[订单表]"), sql)
        self.assertIn("= ?", sql)

    def test_sample_size_clamped_high(self) -> None:
        sql, _ = sb.build_probe_sql(self.ref, "x", MatchMode.FUZZY, True, sample_size=999)
        self.assertIn("TOP (50)", sql)

    def test_sample_size_clamped_low(self) -> None:
        sql, _ = sb.build_probe_sql(self.ref, "x", MatchMode.FUZZY, True, sample_size=0)
        self.assertIn("TOP (1)", sql)


class TestDetailSql(unittest.TestCase):
    """双击查看命中明细所用的语句。"""

    ref = ColumnRef("sales", "订单表", "Remark", "BASE TABLE", "nvarchar")

    def test_selects_all_columns_from_table(self) -> None:
        sql, params = sb.build_detail_sql(self.ref, "zhang", MatchMode.FUZZY, True)
        self.assertTrue(
            sql.startswith("SELECT TOP (200) * FROM [sales].[订单表] WITH (NOLOCK) WHERE "),
            sql,
        )
        self.assertEqual(params, ("%zhang%",))

    def test_exact_mode_uses_equality(self) -> None:
        sql, params = sb.build_detail_sql(self.ref, "zhang", MatchMode.EXACT, True)
        self.assertIn("= ?", sql)
        self.assertEqual(params, ("zhang",))

    def test_collation_reflects_case_sensitivity(self) -> None:
        sql, _ = sb.build_detail_sql(self.ref, "zhang", MatchMode.FUZZY, False)
        self.assertIn("Latin1_General_CS_AS", sql)

    def test_nolock_can_be_disabled(self) -> None:
        sql, _ = sb.build_detail_sql(self.ref, "x", MatchMode.FUZZY, True, with_nolock=False)
        self.assertNotIn("NOLOCK", sql)

    def test_limit_is_bounded(self) -> None:
        sql, _ = sb.build_detail_sql(self.ref, "x", MatchMode.FUZZY, True, limit=10)
        self.assertIn("TOP (10)", sql)
        sql, _ = sb.build_detail_sql(self.ref, "x", MatchMode.FUZZY, True, limit=10**6)
        self.assertIn(f"TOP ({sb.DETAIL_ROW_LIMIT_MAX})", sql)

    def test_limit_has_lower_bound(self) -> None:
        sql, _ = sb.build_detail_sql(self.ref, "x", MatchMode.FUZZY, True, limit=0)
        self.assertIn("TOP (1)", sql)

    def test_value_is_parameterized(self) -> None:
        sql, params = sb.build_detail_sql(self.ref, "' OR 1=1--", MatchMode.FUZZY, True)
        self.assertNotIn("1=1", sql)
        self.assertEqual(params, ("%' OR 1=1--%",))

    def test_no_write_statement(self) -> None:
        sql, _ = sb.build_detail_sql(self.ref, "x", MatchMode.FUZZY, True)
        upper = sql.upper()
        for keyword in ("INSERT", "UPDATE ", "DELETE", "DROP", "ALTER"):
            self.assertNotIn(keyword, upper)


class TestMetadataSql(unittest.TestCase):
    def test_targets_tables_and_views(self) -> None:
        self.assertIn("INFORMATION_SCHEMA.COLUMNS", sb.METADATA_SQL)
        self.assertIn("INFORMATION_SCHEMA.TABLES", sb.METADATA_SQL)
        self.assertIn("'BASE TABLE'", sb.METADATA_SQL)
        self.assertIn("'VIEW'", sb.METADATA_SQL)

    def test_no_write_statement(self) -> None:
        upper = sb.METADATA_SQL.upper()
        for keyword in ("INSERT", "UPDATE ", "DELETE", "DROP", "ALTER"):
            self.assertNotIn(keyword, upper)


if __name__ == "__main__":
    unittest.main(verbosity=2)
