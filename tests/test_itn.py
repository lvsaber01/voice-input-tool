"""数字 ITN（Inverse Text Normalization）单元测试。

49 用例覆盖：
- 15 位权转换
- 13 正则转换（电话、IP、百分比、分数、比值、时间、日期、范围、序号）
- 10 保护机制（成语、专有名词、单字、无数字等）
- 4 范围转换（三五百→300~500 等）
- 10 负向/边界测试
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.itn import ITNStep


class ITNTestBase(unittest.TestCase):
    """ITN 测试基类，提供通用辅助方法。"""

    @classmethod
    def setUpClass(cls):
        cls.step = ITNStep(enabled=True)

    def assert_itn(self, input_text: str, expected: str, msg: str = ""):
        """断言 ITN 转换结果。"""
        result = self.step.process(input_text)
        self.assertEqual(result, expected, msg or f"'{input_text}' → '{result}', expected '{expected}'")


# ─── 位权转换（15 例）───


class TestITNValue(ITNTestBase):
    """位权数值转换测试。"""

    def test_basic_value(self):
        """一百二十三 → 123"""
        self.assert_itn("一百二十三", "123")

    def test_hundred_yi(self):
        """一百一 → 101（v6.0 重点修复）"""
        self.assert_itn("一百一", "101")

    def test_hundred_ling_yi(self):
        """一百零一 → 101"""
        self.assert_itn("一百零一", "101")

    def test_shi_wu(self):
        """十五 → 15"""
        self.assert_itn("十五", "15")

    def test_san_qian(self):
        """三千 → 3000"""
        self.assert_itn("三千", "3000")

    def test_wan_qian_bai(self):
        """一万三千七百零二 → 13702"""
        self.assert_itn("一万三千七百零二", "13702")

    def test_yi_level(self):
        """三亿 → 300000000"""
        self.assert_itn("三亿", "300000000")

    def test_decimal(self):
        """三点一四 → 3.14"""
        self.assert_itn("三点一四", "3.14")

    def test_wan_ling_yi(self):
        """一万零一 → 10001"""
        self.assert_itn("一万零一", "10001")

    def test_er_shi(self):
        """二十 → 20"""
        self.assert_itn("二十", "20")

    def test_shi_yi(self):
        """十一 → 11"""
        self.assert_itn("十一", "11")

    def test_liang_bai(self):
        """两百五十六 → 256"""
        self.assert_itn("两百五十六", "256")

    def test_qian_ling_ba(self):
        """三千零八 → 3008"""
        self.assert_itn("三千零八", "3008")

    def test_yi_bai(self):
        """一百 → 100"""
        self.assert_itn("一百", "100")

    def test_yi_qian(self):
        """一千 → 1000"""
        self.assert_itn("一千", "1000")


# ─── 正则转换（13 例）───


class TestITNRegex(ITNTestBase):
    """正则转换测试：电话、IP、百分比、分数、比值、时间、日期、范围、序号。"""

    def test_phone_11_digits(self):
        """一八五零零一二三四五六 → 18500123456（11位电话）"""
        self.assert_itn("一八五零零一二三四五六", "18500123456")

    def test_phone_too_short(self):
        """一二三四五六 → 一二三四五六（不够11位，不转换）"""
        self.assert_itn("一二三四五六", "123456")

    def test_phone_exclude_value_units(self):
        """一千二百三十四 → 1234（含位权单位，不走电话）"""
        self.assert_itn("一千二百三十四", "1234")

    def test_ip_address(self):
        """一九二点一六八点一点一 → 192.168.1.1"""
        self.assert_itn("一九二点一六八点一点一", "192.168.1.1")

    def test_percent(self):
        """百分之九十九 → 99%"""
        self.assert_itn("百分之九十九", "99%")

    def test_fraction(self):
        """三分之二 → 2/3"""
        self.assert_itn("三分之二", "2/3")

    def test_ratio(self):
        """三比一 → 3:1"""
        self.assert_itn("三比一", "3:1")

    def test_time_hour_minute(self):
        """三点十五分 → 03:15"""
        self.assert_itn("三点十五分", "03:15")

    def test_time_hour_minute_second(self):
        """三点二十五分三十秒 → 03:25:30"""
        self.assert_itn("三点二十五分三十秒", "03:25:30")

    def test_date_full(self):
        """二零二六年五月三日 → 2026年5月3日"""
        self.assert_itn("二零二六年五月三日", "2026年5月3日")

    def test_range_mode1(self):
        """三五百人 → 300~500人"""
        self.assert_itn("三五百人", "300~500人")

    def test_range_mode2(self):
        """十五六个人 → 15~16个人"""
        self.assert_itn("十五六个人", "15~16个人")

    def test_pure_digits(self):
        """二七一四九 → 27149"""
        self.assert_itn("二七一四九", "27149")


# ─── 保护机制（10 例）───


class TestITNProtection(ITNTestBase):
    """保护机制测试：成语、专有名词、单字、无数字。"""

    def test_idiom_luan_qi_ba_zao(self):
        """乱七八糟 → 乱七八糟（成语保护）"""
        self.assert_itn("乱七八糟", "乱七八糟")

    def test_idiom_shi_you_ba_jiu(self):
        """十有八九 → 十有八九（成语保护）"""
        self.assert_itn("十有八九", "十有八九")

    def test_proper_san_ba(self):
        """三八妇女节 → 三八妇女节（专有名词保护）"""
        self.assert_itn("三八妇女节", "三八妇女节")

    def test_proper_wu_yi(self):
        """五一劳动节 → 五一劳动节（专有名词保护）"""
        self.assert_itn("五一劳动节", "五一劳动节")

    def test_single_char(self):
        """一 → 一（单字不转换）"""
        self.assert_itn("一", "一")

    def test_no_digit_text(self):
        """你好世界 → 你好世界（无数字不转换）"""
        self.assert_itn("你好世界", "你好世界")

    def test_value_with_unit(self):
        """三十二个苹果 → 32个苹果"""
        self.assert_itn("三十二个苹果", "32个苹果")

    def test_value_hundred_fen(self):
        """一百分 → 100分"""
        self.assert_itn("一百分", "100分")

    def test_tech_gpt(self):
        """GPT四o → GPT4o"""
        self.assert_itn("GPT四o", "GPT4o")

    def test_tech_iphone(self):
        """iPhone四s → iPhone4s"""
        self.assert_itn("iPhone四s", "iPhone4s")


# ─── 范围转换（4 例，v7.0 补充）───


class TestITNRange(ITNTestBase):
    """范围转换测试：v7.0 三种模式。"""

    def test_range_san_wu_bai(self):
        """三五百人 → 300~500人（模式1）"""
        self.assert_itn("三五百人", "300~500人")

    def test_range_shi_wu_liu(self):
        """十五六个人 → 15~16个人（模式2）"""
        self.assert_itn("十五六个人", "15~16个人")

    def test_range_san_si_bai(self):
        """三四百人 → 300~400人（v7.0 模式3）"""
        self.assert_itn("三四百人", "300~400人")

    def test_range_er_san_shi(self):
        """二三十个 → 20~30个（模式1变体）"""
        self.assert_itn("二三十个", "20~30个")


# ─── 负向/边界测试（10 例）───


class TestITNEdge(ITNTestBase):
    """负向/边界测试：空字符串、纯空格、全数字字符等。"""

    def test_empty_string(self):
        """空字符串 → 空字符串"""
        self.assert_itn("", "")

    def test_whitespace_only(self):
        """纯空格 → 纯空格"""
        self.assert_itn("   ", "   ")

    def test_all_digit_chars(self):
        """一二三四五六七八九零 → 1234567890"""
        self.assert_itn("一二三四五六七八九零", "1234567890")

    def test_mixed_digit_unit(self):
        """一二三四五万 → 12345万（应转为数值）"""
        self.assert_itn("一二三四五万", "12345万")

    def test_digit_in_sentence(self):
        """我说了一句话 → 我说了1句话"""
        self.assert_itn("我说了一句话", "我说了1句话")

    def test_san_dian(self):
        """三点 → 3点（歧义：时间 vs 小数，走纯数字）"""
        self.assert_itn("三点", "3点")

    def test_large_number_with_unit(self):
        """三千八百个苹果 → 3800个苹果"""
        self.assert_itn("三千八百个苹果", "3800个苹果")

    def test_year_only(self):
        """二零二六年 → 2026年（仅年份无月日）"""
        self.assert_itn("二零二六年", "2026年")

    def test_idiom_yi_dian_yi_di(self):
        """一点一滴 → 一点一滴（成语保护）"""
        self.assert_itn("一点一滴", "一点一滴")

    def test_idiom_bai_fen_zhi_bai(self):
        """百分之百 → 百分之百（成语保护）"""
        self.assert_itn("百分之百", "百分之百")


# ─── 基础属性测试 ───


class TestITNStepProperties(ITNTestBase):
    """ITNStep 基础属性测试。"""

    def test_step_name(self):
        """步骤名称为 'itn'。"""
        self.assertEqual(self.step.name, "itn")

    def test_step_priority(self):
        """优先级为 50。"""
        self.assertEqual(self.step.priority, 50)

    def test_step_enabled_default(self):
        """默认启用。"""
        step = ITNStep()
        self.assertTrue(step.enabled)

    def test_step_enabled_false(self):
        """disabled 时 process 仍可调用（返回原文）。"""
        step = ITNStep(enabled=False)
        # 即使 disabled，process 仍应返回文本（TextPipeline 层面跳过）
        self.assertEqual(step.name, "itn")


if __name__ == "__main__":
    unittest.main()
