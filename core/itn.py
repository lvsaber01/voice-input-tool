"""数字 ITN（Inverse Text Normalization）— Pipeline 插件。

将中文数字文本转换为阿拉伯数字，支持：
- 位权数值（一百二十三→123）
- 电话号码（精确 11 位纯数字）
- IP 地址（X点X点X点X）
- 百分比、分数、比值
- 时间、日期
- 范围（三五百→300~500）
- 成语/专有名词保护

零外部依赖（纯 Python 正则）。
"""

import re
from core.pipeline_step import PipelineStep, StepNames

# ─── 映射表 ───

NUM_MAP = {
    '零': '0', '一': '1', '幺': '1', '二': '2', '两': '2',
    '三': '3', '四': '4', '五': '5', '六': '6', '七': '7',
    '八': '8', '九': '9',
}

VALUE_MAP = {
    '零': 0, '一': 1, '二': 2, '两': 2, '三': 3, '四': 4, '五': 5,
    '六': 6, '七': 7, '八': 8, '九': 9, '十': 10, '百': 100,
    '千': 1000, '万': 10000, '亿': 100000000,
}

# ─── 成语/习语黑名单（含数字的固定表达，不应转换）───

IDIOM_BLACKLIST = [
    '正经八百', '五零二落', '五零四散', '五十步笑百步',
    '乱七八糟', '乌七八糟', '污七八糟', '四百四病',
    '十有八九', '十之八九', '三十而立', '三十六策', '三十六计', '三十六行',
    '三五成群', '三百六十行', '三六九等', '三心二意',
    '七老八十', '七零八落', '七零八碎', '七七八八', '乱七八遭',
    '略知一二', '零零星星', '零七八碎', '九九归一',
    '八九不离十', '百分之百', '入木三分', '一点一滴',
    '二三其德', '二三其意', '无银三百两',
    '九三学社', '五四运动', '路易十六', '十二五', '十三五', '十四五',
    '年三十', '一不做二不休', '一五一十', '一朝一夕',
    '三令五申', '四分五裂', '五颜六色', '七上八下',
    '千军万马', '千言万语', '万无一失',
]

# ─── 专有名词保护（ITN 不应转换的固定表达）───

PROPER_NOUN_PROTECTION = [
    '三八妇女节', '五一劳动节', '六一儿童节', '十一国庆节',
    '七一建党节', '八一建军节', '九一八', '双十二',
]

# ─── 数字字符集 ───

_DIGITS = '零幺一二两三四五六七八九'
_DIGIT_CHARS = set(_DIGITS)


class ITNStep(PipelineStep):
    """数字逆文本正则化步骤。"""

    def __init__(self, enabled: bool = True):
        super().__init__(name=StepNames.ITN, priority=50, enabled=enabled)
        self._idiom_re = re.compile('|'.join(re.escape(i) for i in IDIOM_BLACKLIST))
        self._proper_re = re.compile('|'.join(re.escape(n) for n in PROPER_NOUN_PROTECTION))
        self._rules = self._build_rules()

    def process(self, text: str) -> str:
        """执行 ITN 转换。

        流程：成语保护 → 专有名词保护 → 逐条正则（占位符互斥）→ 恢复
        """
        if not text:
            return text

        placeholders = {}
        protected = text

        # 1. 成语保护
        for m in self._idiom_re.finditer(text):
            key = f"\x00I{len(placeholders)}\x00"
            placeholders[key] = m.group(0)
            protected = protected.replace(m.group(0), key, 1)

        # 2. 专有名词保护
        for m in self._proper_re.finditer(text):
            key = f"\x00I{len(placeholders)}\x00"
            placeholders[key] = m.group(0)
            protected = protected.replace(m.group(0), key, 1)

        # 3. 逐条应用正则规则（已匹配区间用占位符保护）
        for pattern, converter in self._rules:
            protected = self._apply_protected(pattern, converter, protected, placeholders)

        # 4. 恢复占位符
        for key, orig in placeholders.items():
            protected = protected.replace(key, orig)

        return protected

    def _apply_protected(self, pattern, converter, text, placeholders):
        """应用正则规则，将匹配结果替换为占位符。"""
        def _replace(match):
            result = converter(match)
            key = f"\x00I{len(placeholders)}\x00"
            placeholders[key] = result
            return key
        return pattern.sub(_replace, text)

    def _build_rules(self):
        """构建正则规则列表（有序）。

        执行顺序（从最具体到最宽泛）：
        1. 电话（精确11位纯数字）— 最具体，避免被其他规则拆分
        2. IP（含 点 的特殊格式）
        3. 百分比/分数/比值（含关键字锚点）
        4. 时间（要求 分/秒 关键字，避免匹配小数）
        5. 日期（年月日格式）
        6. 范围（三五百 等）
        7. 位权数值（含十百千万亿）— 转换函数内判断 _cp vs _cv
        8. 纯数字（2+ 连续数字字符）
        9. 单数字（嵌入非数字文本中）
        """
        _D = f'[{_DIGITS}]'
        _P = '十百千万亿'  # power-unit chars
        # 位权数值：至少含一个位权单位，前后不跟数字/位权字符
        # 使用 (?=.*[十百千万亿]) 前瞻确保含位权单位
        _VALUE_PAT = (
            r'(?<![{_DIGITS}{_P}])'
            r'(?=[零幺一二两三四五六七八九十百千万亿]*[十百千万亿])'
            r'[零幺一两二三四五六七八九十][零幺一两二三四五六七八九十百千万亿]+'
            r'(?![零幺一两二三四五六七八九十百千万亿])'
        )
        # 小数：X点X（但不含 分/秒 关键字，避免匹配时间）
        _DECIMAL_PAT = rf'({_D}+)点({_D}+)(?!分)(?!秒)'
        return [
            # 1. 电话号码（精确 11 位纯数字，前后无数字/位权字符）
            (re.compile(rf'(?<![{_DIGITS}{_P}])({_D}{{11}})(?![{_DIGITS}{_P}])'),
             self._r_phone),
            # 2. IP（X点X点X点X）
            (re.compile(rf'(?:{_D}+点){{3}}{_D}+'), self._r_ip),
            # 3. 百分比
            (re.compile(r'百分之([零一二三四五六七八九十百千万]+)'), self._r_percent),
            # 4. 分数
            (re.compile(r'([零一二三四五六七八九十百千万]+)分之([零一二三四五六七八九十百千万]+)'), self._r_fraction),
            # 5. 比值
            (re.compile(r'([零一二三四五六七八九十百千万]+)比([零一二三四五六七八九十百千万]+)'), self._r_ratio),
            # 6. 时间（点+分+秒，要求 分 或 秒 关键字）
            # 注意：数字部分包含 十，因为 "十五分"=15分 是合法时间
            (re.compile(r'[零幺一二两三四五六七八九十]+点[零幺一二两三四五六七八九十]+分(?:[零幺一二两三四五六七八九十]+秒)?|[零幺一二两三四五六七八九十]+点[零幺一二两三四五六七八九十]+秒'), self._r_time),
            # 7. 日期（二零二六年五月三日）
            (re.compile(r'(?:(?:二[零一二三四五六七八九]){2})年(?:([一二三四五六七八九十]+)月)?(?:([一二三四五六七八九十]+)[日号])?'), self._r_date),
            # 8. 范围（v7.0: 三种模式，加负向后顾防止在长数字串中误匹配）
            (re.compile(
                r'(?<![' + _DIGITS + r'])([一二三四五六七八九])([一二三四五六七八九])([十百千万亿])'
                r'|(十[一二三四五六七八九])([一二三四五六七八九])([万千亿]?)'
                r'|(?<![' + _DIGITS + r'])([一二三四五六七八九])([一二三四五六七八九])([百千万])([十百千万]?)'
            ), self._r_range),
            # 9. 小数（三点一四 → 3.14）— 排除 分/秒 后缀
            (re.compile(_DECIMAL_PAT), self._r_decimal),
            # 10. 位权数值（至少含一个位权单位字符）
            (re.compile(_VALUE_PAT), self._r_value),
            # 11. 纯数字（2+ 连续数字字符）
            (re.compile(rf'{_D}{{2,}}'), self._r_pure),
            # 12. 单数字（前非数字，后跟非数字、非位权字符，且后面有字符）
            (re.compile(rf'(?<![{_DIGITS}])({_D})(?=[^{_DIGITS}{_P}])'), self._r_single),
        ]

    # ─── 转换函数 ───

    @staticmethod
    def _r_phone(m):
        """电话号码转换（精确 11 位纯数字）。"""
        return ''.join(NUM_MAP.get(c, c) for c in m.group(1))

    @staticmethod
    def _r_ip(m):
        """IP 地址转换。"""
        return '.'.join(''.join(NUM_MAP.get(c, c) for c in p) for p in m.group(0).split('点'))

    @staticmethod
    def _r_percent(m):
        """百分比转换。"""
        return ITNStep._cv(m.group(1)) + '%'

    @staticmethod
    def _r_fraction(m):
        """分数转换。"""
        return f"{ITNStep._cv(m.group(2))}/{ITNStep._cv(m.group(1))}"

    @staticmethod
    def _r_ratio(m):
        """比值转换。"""
        return f"{ITNStep._cv(m.group(1))}:{ITNStep._cv(m.group(2))}"

    @staticmethod
    def _r_time(m):
        """时间转换：split+逆序判断。"""
        parts = [p for p in re.split(r'[点分秒]', m.group(0)) if p]
        if not parts:
            return m.group(0)
        h = ITNStep._cv(parts[0]).zfill(2)
        if len(parts) >= 3:
            return f"{h}:{ITNStep._cv(parts[1]).zfill(2)}:{ITNStep._cv(parts[2]).zfill(2)}"
        elif len(parts) >= 2:
            return f"{h}:{ITNStep._cv(parts[1]).zfill(2)}"
        return h

    @staticmethod
    def _r_date(m):
        """日期转换。"""
        r = []
        full = m.group(0)
        if '年' in full:
            r.append(ITNStep._cp(full[:full.index('年')]) + '年')
        if m.group(1):
            r.append(ITNStep._cv(m.group(1)) + '月')
        if m.group(2):
            r.append(ITNStep._cv(m.group(2)) + '日')
        return ''.join(r) if r else m.group(0)

    @staticmethod
    def _r_range(m):
        """范围转换（三种模式）。"""
        if m.group(1):  # 模式1: 三五百 → 300~500
            d1 = VALUE_MAP.get(m.group(1), 0)
            d2 = VALUE_MAP.get(m.group(2), 0)
            u = VALUE_MAP.get(m.group(3), 1)
            return f"{d1 * u}~{d2 * u}"
        elif m.group(4):  # 模式2: 十五六 → 15~16
            base_str = m.group(4)
            tail_digit = m.group(5)
            base_val = int(ITNStep._cv(base_str))
            tail_val = VALUE_MAP.get(tail_digit, 0)
            last_digit_val = VALUE_MAP.get(base_str[-1], 0) if base_str else 0
            return f"{base_val}~{base_val + tail_val - last_digit_val}"
        elif m.group(7):  # 模式3: 三四百 → 300~400
            d1 = VALUE_MAP.get(m.group(7), 0)
            d2 = VALUE_MAP.get(m.group(8), 0)
            u = VALUE_MAP.get(m.group(9), 1)
            suffix = m.group(10) or ''
            return f"{d1 * u}~{d2 * u}{suffix}"
        return m.group(0)

    @staticmethod
    def _r_value(m):
        """位权数值转换 — 智能判断纯数字映射 vs 位权计算。

        - 无位权单位（十百千万亿）→ 逐字符映射（_cp）
        - 短序列（≤3 字符）→ 位权计算（_cv）
        - 位权单位穿插在数字中 → 位权计算（_cv）
        - 位权单位仅在末尾（如 一二三四五万）→ 逐字符映射数字，保留单位
        """
        text = m.group(0)
        _POWER = '十百千万亿'
        has_power = any(c in _POWER for c in text)

        if not has_power:
            # 纯数字序列，无位权单位 → 逐字符映射
            return ITNStep._cp(text)

        if len(text) <= 3:
            # 短序列（如 一千、二十、十五）→ 位权计算
            return ITNStep._cv(text)

        # 长序列：检查位权单位是否穿插在数字中
        has_interspersed = False
        for i, c in enumerate(text):
            if c in _POWER and i < len(text) - 1:
                has_interspersed = True
                break

        if has_interspersed:
            # 位权单位穿插（如 一万三千七百零二）→ 位权计算
            return ITNStep._cv(text)
        else:
            # 位权单位仅在末尾（如 一二三四五万）→ 逐字符映射数字，保留单位
            result = ''
            for c in text:
                if c in _POWER:
                    result += c
                else:
                    result += NUM_MAP.get(c, c)
            return result

    @staticmethod
    def _r_pure(m):
        """纯数字字符映射。"""
        return ITNStep._cp(m.group(0))

    @staticmethod
    def _r_decimal(m):
        """小数转换：三点一四 → 3.14"""
        int_part = ITNStep._cp(m.group(1))
        dec_part = ITNStep._cp(m.group(2))
        return f"{int_part}.{dec_part}"

    @staticmethod
    def _r_single(m):
        """单数字转换：GPT四o → GPT4o, 三点 → 3点。

        使用正向前瞻确保后面有非数字字符（不在字符串末尾）。
        """
        return NUM_MAP.get(m.group(1), m.group(1))

    # ─── 核心转换函数 ───

    @staticmethod
    def _cp(text: str) -> str:
        """纯字符映射（逐字符替换）。"""
        return ''.join(NUM_MAP.get(c, c) for c in text)

    @staticmethod
    def _cv(text: str) -> str:
        """位权数值转换。

        算法：value, temp = 0, 0
        - 遇到位权单位（十百千万亿）：value += temp * unit, temp = 0
        - 最后：value += temp
        """
        if not text:
            return text

        if '点' in text:
            int_p, dec_p = text.split('点', 1)
        else:
            int_p, dec_p = text, ''

        if not int_p:
            return text

        value, temp = 0, 0
        for c in int_p:
            if c == '十':
                temp = 10 if temp == 0 else temp * 10
            elif c == '零':
                pass
            elif c in '一二两三四五六七八九':
                temp += VALUE_MAP.get(c, 0)
            elif c == '万':
                value = (value + temp) * VALUE_MAP['万']
                temp = 0
            elif c in '百千':
                value += temp * VALUE_MAP[c]
                temp = 0
            elif c == '亿':
                value = (value + temp) * VALUE_MAP['亿']
                temp = 0

        value += temp
        result = str(value)

        if dec_p:
            result += '.' + ITNStep._cp(dec_p)

        return result
