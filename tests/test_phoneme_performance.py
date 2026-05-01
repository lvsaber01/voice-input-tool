"""性能基准测试 — 音素匹配系统。

验证索引构建、纠错耗时、内存占用、并发安全等性能指标。
阈值取设计预期值的宽松版本（Phase 4 校准阶段）。
"""

import os
import sys
import tempfile
import threading
import time
import tracemalloc
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.phoneme.phoneme_types import text_to_phonemes
from core.phoneme.phoneme_corrector import PhonemeCorrector


# ─── 辅助函数 ───

def _generate_hotwords(count: int) -> str:
    """生成指定数量的热词内容（中英混合）。

    使用固定模板 + 变体生成，确保多样性。
    """
    zh_words = [
        "撒贝宁", "东方财富", "科大讯飞", "乐清", "张三丰",
        "腾讯会议", "阿里巴巴", "字节跳动", "拼多多", "小红书",
        "微信支付", "京东商城", "美团外卖", "滴滴出行", "百度搜索",
        "网易云音乐", "哔哩哔哩", "知乎社区", "今日头条", "快手短视频",
    ]
    en_words = [
        "Claude", "PyTorch", "Docker", "GitHub", "HuggingFace",
        "OpenAI", "TensorFlow", "Kubernetes", "JavaScript", "TypeScript",
        "ReactNative", "VueFramework", "AngularDev", "NodePackage", "PythonAsync",
        "JavaRuntime", "GoCompiler", "RustCargo", "SwiftUIKit", "KotlinAndroid",
    ]
    lines = []
    for i in range(count):
        if i % 2 == 0:
            base = zh_words[i % len(zh_words)]
            # 添加变体后缀以增加唯一性
            if i >= len(zh_words) * 2:
                base = f"{base}{i}"
        else:
            base = en_words[i % len(en_words)]
            if i >= len(en_words) * 2:
                base = f"{base}{i}"
        lines.append(base)
    return "\n".join(lines)


def _generate_chinese_text(char_count: int) -> str:
    """生成指定字数的中文测试文本。"""
    base = "科大迅飞语音识别技术在全球范围内得到广泛应用，东方菜富发布了最新财报，撒贝你主持今天的节目。"
    # 重复直到达到目标长度
    result = base
    while len(result) < char_count:
        result += base
    return result[:char_count]


# ─── 测试类 ───

class TestIndexBuildPerformance(unittest.TestCase):
    """索引构建性能基准。"""

    def test_build_100_hotwords(self):
        """索引构建 100 条热词（< 100ms）。"""
        content = _generate_hotwords(100)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            path = f.name
        try:
            corrector = PhonemeCorrector(threshold=0.7, enabled=True)
            start = time.perf_counter()
            count = corrector.update_from_file(path)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.assertGreater(count, 0)
            self.assertLess(elapsed_ms, 100, f"100 条热词索引构建耗时 {elapsed_ms:.1f}ms，超过 100ms")
        finally:
            os.unlink(path)

    def test_build_500_hotwords(self):
        """索引构建 500 条热词（< 500ms）。"""
        content = _generate_hotwords(500)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            path = f.name
        try:
            corrector = PhonemeCorrector(threshold=0.7, enabled=True)
            start = time.perf_counter()
            count = corrector.update_from_file(path)
            elapsed_ms = (time.perf_counter() - start) * 1000
            self.assertGreater(count, 0)
            self.assertLess(elapsed_ms, 500, f"500 条热词索引构建耗时 {elapsed_ms:.1f}ms，超过 500ms")
        finally:
            os.unlink(path)


class TestCorrectionPerformance(unittest.TestCase):
    """纠错耗时性能基准。"""

    @classmethod
    def setUpClass(cls):
        """预构建纠错器（只构建一次）。"""
        # 100 热词
        content_100 = _generate_hotwords(100)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content_100)
            cls.path_100 = f.name
        cls.corrector_100 = PhonemeCorrector(threshold=0.7, enabled=True)
        cls.corrector_100.update_from_file(cls.path_100)

        # 500 热词
        content_500 = _generate_hotwords(500)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content_500)
            cls.path_500 = f.name
        cls.corrector_500 = PhonemeCorrector(threshold=0.7, enabled=True)
        cls.corrector_500.update_from_file(cls.path_500)

    @classmethod
    def tearDownClass(cls):
        for path in (cls.path_100, cls.path_500):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_correct_100_hotwords_50_chars(self):
        """单次纠错 100 热词 + 50 字文本（< 10ms）。"""
        text = _generate_chinese_text(50)
        # 运行多次取中位数
        times = []
        for _ in range(20):
            start = time.perf_counter()
            self.corrector_100.correct(text)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
        times.sort()
        median_ms = times[len(times) // 2]
        self.assertLess(median_ms, 10, f"100 热词 + 50 字中位数耗时 {median_ms:.1f}ms，超过 10ms")

    def test_correct_500_hotwords_50_chars(self):
        """单次纠错 500 热词 + 50 字文本（< 20ms）。"""
        text = _generate_chinese_text(50)
        times = []
        for _ in range(20):
            start = time.perf_counter()
            self.corrector_500.correct(text)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
        times.sort()
        median_ms = times[len(times) // 2]
        self.assertLess(median_ms, 20, f"500 热词 + 50 字中位数耗时 {median_ms:.1f}ms，超过 20ms")

    def test_correct_100_hotwords_200_chars(self):
        """单次纠错 100 热词 + 200 字文本（< 50ms）。"""
        text = _generate_chinese_text(200)
        times = []
        for _ in range(20):
            start = time.perf_counter()
            self.corrector_100.correct(text)
            elapsed = (time.perf_counter() - start) * 1000
            times.append(elapsed)
        times.sort()
        median_ms = times[len(times) // 2]
        self.assertLess(median_ms, 50, f"100 热词 + 200 字中位数耗时 {median_ms:.1f}ms，超过 50ms")


class TestTruncationProtection(unittest.TestCase):
    """长文本截断保护。"""

    def test_long_text_truncation_effective(self):
        """超过 600 音素的文本被截断，性能可控。"""
        content = _generate_hotwords(100)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            path = f.name
        try:
            corrector = PhonemeCorrector(threshold=0.7, enabled=True)
            corrector.update_from_file(path)

            # 生成超长文本（远超 600 音素 ≈ 200 汉字）
            long_text = _generate_chinese_text(500)
            phonemes = text_to_phonemes(long_text)

            # 验证音素数确实超过 MAX_PHONEME_LENGTH
            if len(phonemes) <= PhonemeCorrector.MAX_PHONEME_LENGTH:
                self.skipTest(f"生成的音素数 {len(phonemes)} 未超过 MAX {PhonemeCorrector.MAX_PHONEME_LENGTH}")

            # 纠错应该仍然快速（因为截断）
            times = []
            for _ in range(10):
                start = time.perf_counter()
                corrector.correct(long_text)
                elapsed = (time.perf_counter() - start) * 1000
                times.append(elapsed)

            median_ms = sorted(times)[len(times) // 2]
            # 截断后性能应与 200 字相当
            self.assertLess(median_ms, 100, f"超长文本截断后耗时 {median_ms:.1f}ms，超过 100ms")
        finally:
            os.unlink(path)


class TestMemoryFootprint(unittest.TestCase):
    """内存占用估算。"""

    def test_memory_100_hotwords(self):
        """100 条热词内存占用 < 10MB。"""
        content = _generate_hotwords(100)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            path = f.name
        try:
            tracemalloc.start()
            corrector = PhonemeCorrector(threshold=0.7, enabled=True)
            corrector.update_from_file(path)
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()

            peak_mb = peak / (1024 * 1024)
            self.assertLess(peak_mb, 10, f"100 条热词内存峰值 {peak_mb:.1f}MB，超过 10MB")
        finally:
            os.unlink(path)


class TestConcurrentCorrection(unittest.TestCase):
    """并发纠错性能。"""

    def test_10_threads_no_deadlock(self):
        """并发纠错 10 线程（无死锁，< 1s 完成）。"""
        content = _generate_hotwords(100)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            path = f.name
        try:
            corrector = PhonemeCorrector(threshold=0.7, enabled=True)
            corrector.update_from_file(path)
            text = _generate_chinese_text(50)

            errors = []
            results = []
            barrier = threading.Barrier(10)

            def worker():
                try:
                    barrier.wait(timeout=5)
                    r = corrector.correct(text)
                    results.append(r.text)
                except Exception as e:
                    errors.append(e)

            threads = [threading.Thread(target=worker) for _ in range(10)]
            start = time.perf_counter()
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=1)
            elapsed_s = time.perf_counter() - start

            self.assertEqual(len(errors), 0, f"并发错误: {errors}")
            self.assertLess(elapsed_s, 1.0, f"10 线程并发耗时 {elapsed_s:.2f}s，超过 1s")
            # 所有结果应一致
            self.assertEqual(len(set(results)), 1, "并发结果不一致")
        finally:
            os.unlink(path)


class TestColdStartPerformance(unittest.TestCase):
    """冷启动性能。"""

    def test_first_load_with_pypinyin(self):
        """冷启动首次加载延迟（含 pypinyin import，< 1s）。"""
        content = _generate_hotwords(50)
        with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
            f.write(content)
            path = f.name
        try:
            start = time.perf_counter()
            corrector = PhonemeCorrector(threshold=0.7, enabled=True)
            corrector.update_from_file(path)
            elapsed_s = time.perf_counter() - start
            self.assertLess(elapsed_s, 1.0, f"冷启动耗时 {elapsed_s:.2f}s，超过 1s")
        finally:
            os.unlink(path)


class TestScalingBehavior(unittest.TestCase):
    """热词数增长对耗时的影响（线性/亚线性）。"""

    def test_scaling_linear_or_better(self):
        """热词数从 100→500，耗时增长不超过 10 倍（应线性或亚线性）。"""
        results = {}
        for count in (100, 200, 500):
            content = _generate_hotwords(count)
            with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8') as f:
                f.write(content)
                path = f.name
            try:
                corrector = PhonemeCorrector(threshold=0.7, enabled=True)
                corrector.update_from_file(path)
                text = _generate_chinese_text(50)

                times = []
                for _ in range(20):
                    start = time.perf_counter()
                    corrector.correct(text)
                    elapsed = (time.perf_counter() - start) * 1000
                    times.append(elapsed)
                times.sort()
                results[count] = times[len(times) // 2]
            finally:
                os.unlink(path)

        # 500 热词的耗时不应超过 100 热词的 10 倍
        ratio = results[500] / max(results[100], 0.001)
        self.assertLess(ratio, 10, f"500/100 耗时比 {ratio:.1f}，超过 10 倍（非线性增长）")


if __name__ == "__main__":
    unittest.main()
