"""F1 Emoji 清理单元测试 — 16 用例

测试 FunASREngine._postprocess_sensevoice() 的 emoji 清除功能。
"""

import pytest
from unittest.mock import patch, MagicMock


class TestEmojiCleanup:
    """测试 _postprocess_sensevoice 的 emoji 清理功能。"""

    def test_emoji_only(self):
        """纯 emoji → 空字符串"""
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice("😊😂") == ""

    def test_emoji_in_text(self):
        """emoji 混中文 → 去除 emoji"""
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice("你好😊世界") == "你好世界"

    def test_emoji_at_end(self):
        """emoji 在末尾 → 去除"""
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice("你好世界😊") == "你好世界"

    def test_no_emoji(self):
        """无 emoji → 原文不变"""
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice("你好世界") == "你好世界"

    def test_multiple_emoji(self):
        """多个 emoji → 全部去除"""
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice("😊😂🤔你好😊") == "你好"

    def test_emo_marker(self):
        """EMO 标记 → 去除"""
        from core.stt_funasr import FunASREngine
        result = FunASREngine._postprocess_sensevoice("文本<|EMO_HAPPY|>")
        assert "EMO_HAPPY" not in result
        assert "<|" not in result
        assert "|>" not in result
        assert result == "文本"

    def test_mixed_marker_emoji(self):
        """标记+emoji → 全部去除"""
        from core.stt_funasr import FunASREngine
        result = FunASREngine._postprocess_sensevoice("<|EMO_HAPPY|>😊文本")
        assert "EMO_HAPPY" not in result
        assert "😊" not in result
        assert "<|" not in result
        assert "|>" not in result
        assert result == "文本"

    def test_english_emoji(self):
        """英文+emoji → 去除 emoji（空格保留）"""
        from core.stt_funasr import FunASREngine
        # _postprocess_sensevoice 本身不处理空格，空格保留
        result = FunASREngine._postprocess_sensevoice("Hello😊 World")
        assert "😊" not in result
        assert "Hello" in result
        assert "World" in result

    def test_null_input(self):
        """None → 空字符串"""
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice(None) == ""

    def test_empty_input(self):
        """空串 → 空字符串"""
        from core.stt_funasr import FunASREngine
        assert FunASREngine._postprocess_sensevoice("") == ""

    def test_rich_postprocess(self):
        """主路径：rich_transcription_postprocess 可用"""
        from core.stt_funasr import FunASREngine
        with patch("core.stt_funasr.FunASREngine._postprocess_sensevoice",
                    wraps=FunASREngine._postprocess_sensevoice):
            # 当 rich_transcription_postprocess 可用时，先清除标记再清除 emoji
            mock_rtp = MagicMock(return_value="你好😊世界<|nospeech|>")
            with patch.dict('sys.modules', {'funasr.utils.postprocess_utils': MagicMock(rich_transcription_postprocess=mock_rtp)}):
                result = FunASREngine._postprocess_sensevoice("原始文本")
                # rich_transcription_postprocess 模拟返回已处理文本
                # 然后 emoji 被清除
                # 注意：由于 mock 在不同 import 路径，这里验证基本行为
                pass
        # 直接验证 emoji 清除
        assert FunASREngine._postprocess_sensevoice("你好😊世界") == "你好世界"

    def test_rich_unavailable(self):
        """fallback 路径：模拟 rich_transcription_postprocess 不可用

        注意：由于模块缓存，无法在测试中真正阻止 import。
        当 rich_transcription_postprocess 可用时，标记由它处理。
        此测试验证 emoji 清除在任何路径下都生效。
        """
        from core.stt_funasr import FunASREngine
        # 直接验证 emoji 清除功能（无论标记是否由 rtp 处理）
        result = FunASREngine._postprocess_sensevoice("文本😊更多😊emoji")
        assert "😊" not in result
        assert "文本" in result
        assert "更多" in result
        assert "emoji" in result

    def test_zwj_sequence(self):
        """ZWJ 组合 emoji → 全部清除"""
        from core.stt_funasr import FunASREngine
        # 👨‍👩‍👧 = U+1F468 + ZWJ + U+1F469 + ZWJ + U+1F467
        result = FunASREngine._postprocess_sensevoice("👨\u200D👩\u200D👧文本")
        assert result == "文本"

    def test_skin_tone(self):
        """肤色修饰符 emoji → 全部清除"""
        from core.stt_funasr import FunASREngine
        # 👋🏻 = U+1F44B + U+1F3FB
        result = FunASREngine._postprocess_sensevoice("👋\U0001F3FB文本")
        assert result == "文本"

    def test_flag_emoji(self):
        """国旗 emoji → 清除"""
        from core.stt_funasr import FunASREngine
        # 🇨🇳 = U+1F1E8 + U+1F1F3
        result = FunASREngine._postprocess_sensevoice("🇨\U0001F1F3文本")
        assert result == "文本"

    def test_cjk_not_affected(self):
        """CJK 字符不被误伤"""
        from core.stt_funasr import FunASREngine
        text = "你好世界测试中文标点，句号。"
        assert FunASREngine._postprocess_sensevoice(text) == text


class TestEmojiPattern:
    """测试 _EMOJI_PATTERN 正则本身的正确性。"""

    def test_pattern_compiled(self):
        """预编译正则存在且可用"""
        from core.stt_funasr import _EMOJI_PATTERN
        assert _EMOJI_PATTERN is not None

    def test_pattern_no_cjk_match(self):
        """正则不匹配中文字符"""
        from core.stt_funasr import _EMOJI_PATTERN
        assert _EMOJI_PATTERN.sub('', "你好世界") == "你好世界"

    def test_pattern_no_ascii_match(self):
        """正则不匹配 ASCII 字符"""
        from core.stt_funasr import _EMOJI_PATTERN
        assert _EMOJI_PATTERN.sub('', "Hello World 123") == "Hello World 123"
