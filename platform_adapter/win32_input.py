"""Win32 输入模拟器

纯 ctypes 实现，无第三方依赖。
用于替代 keyboard.send，提高 Electron 应用兼容性。
仅 Windows 平台可用，其他平台导入时不会初始化。
"""

import sys
import ctypes
import types
import logging

logger = logging.getLogger(__name__)

# 非 Windows 平台不注册任何实现
if sys.platform != 'win32':
    Win32Input = None

    def get_win32_input():
        """非 Windows 平台返回 None"""
        return None

else:
    try:
        import ctypes.wintypes
        if not hasattr(ctypes.wintypes, 'ULONG_PTR'):
            raise ImportError('ctypes.wintypes missing ULONG_PTR')
    except ImportError:
        ctypes.wintypes = types.ModuleType('ctypes.wintypes')
        ctypes.wintypes.ULONG_PTR = ctypes.c_uint64

    # Win32 常量
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_CONTROL = 0x11
    VK_V = 0x56

    # ULONG_PTR: 32位系统 4 字节，64位系统 8 字节（自动适配）
    ULONG_PTR = ctypes.wintypes.ULONG_PTR

    # ------------------------------------------------------------------
    # SendInput 结构体定义
    # ------------------------------------------------------------------

    class KEYBDINPUT(ctypes.Structure):
        """Win32 KEYBDINPUT 结构体

        dwExtraInfo 使用 wintypes.ULONG_PTR，自动适配 32/64 位系统。
        """
        _fields_ = [
            ("wVk", ctypes.c_ushort),       # 虚拟键码
            ("wScan", ctypes.c_ushort),      # 硬件扫描码（0=自动映射）
            ("dwFlags", ctypes.c_ulong),     # 标志位
            ("time", ctypes.c_ulong),        # 时间戳（0=系统自动）
            ("dwExtraInfo", ULONG_PTR),      # 应用定义附加值
        ]

    class MOUSEINPUT(ctypes.Structure):
        """Win32 MOUSEINPUT 结构体

        仅用作 INPUT_UNION 占位，确保联合体大小与系统一致。
        MOUSEINPUT 是 INPUT 联合体中最大的成员。
        不声明会导致 sizeof(INPUT) 偏小，SendInput 静默失败。
        """
        _fields_ = [
            ("dx", ctypes.c_long),
            ("dy", ctypes.c_long),
            ("mouseData", ctypes.c_ulong),
            ("dwFlags", ctypes.c_ulong),
            ("time", ctypes.c_ulong),
            ("dwExtraInfo", ULONG_PTR),
        ]

    class INPUT_UNION(ctypes.Union):
        """INPUT 联合体

        必须包含 MOUSEINPUT（最大成员）确保联合体大小正确。
        64 位系统: sizeof = 32 字节，sizeof(INPUT) = 40 字节（匹配 Windows SDK）。
        """
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
        ]

    class INPUT(ctypes.Structure):
        """Win32 INPUT 结构体"""
        _fields_ = [
            ("type", ctypes.c_ulong),
            ("union", INPUT_UNION),
        ]

    # ------------------------------------------------------------------
    # 模块级单例
    # ------------------------------------------------------------------

    _instance = None

    def get_win32_input():
        """获取 Win32Input 单例（懒加载）"""
        global _instance
        if _instance is None:
            _instance = Win32Input()
        return _instance

    # ------------------------------------------------------------------
    # Win32Input 类
    # ------------------------------------------------------------------

    class Win32Input:
        """Win32 输入模拟器"""

        def __init__(self):
            self.user32 = ctypes.windll.user32

        def _send_input(self, inputs: list) -> int:
            """发送输入事件

            Args:
                inputs: INPUT 结构体列表

            Returns:
                实际成功发送的事件数，-1 表示异常
            """
            if not inputs:
                return 0
            try:
                arr = (INPUT * len(inputs))(*inputs)
                result = self.user32.SendInput(
                    len(inputs), arr, ctypes.sizeof(INPUT)
                )
                if result != len(inputs):
                    logger.warning(
                        "SendInput: 发送 %d 个事件，仅 %d 个成功",
                        len(inputs), result
                    )
                return result
            except OSError as e:
                logger.error("SendInput 异常: %s", e)
                return -1

        def simulate_ctrl_v(self) -> bool:
            """模拟 Ctrl+V 粘贴

            使用 SendInput 直接调用，4 个事件批量发送。
            SendInput 保证事件按序处理，无需键间延时。

            Returns:
                True 表示所有事件发送成功
            """
            inputs = [
                self._make_key(VK_CONTROL),                       # Ctrl down
                self._make_key(VK_V),                              # V down
                self._make_key(VK_V, KEYEVENTF_KEYUP),             # V up
                self._make_key(VK_CONTROL, KEYEVENTF_KEYUP),       # Ctrl up
            ]
            return self._send_input(inputs) == len(inputs)

        def send_unicode_text(self, text: str, batch_size: int = 50) -> bool:
            """用 KEYEVENTF_UNICODE 批量发送文本

            绕过剪贴板，直接向系统输入队列发送 Unicode 字符。
            速度较慢，仅作为降级方案。

            Args:
                text: 要发送的文本
                batch_size: 每批发送的字符数（避免输入队列溢出）

            Returns:
                True 表示所有字符发送成功
            """
            if not text:
                return True
            try:
                chars = [ord(ch) for ch in text]
                total = len(chars)
                success_count = 0

                for i in range(0, total, batch_size):
                    batch = chars[i:i + batch_size]
                    inputs = []
                    for code in batch:
                        # key down
                        inp_down = INPUT()
                        inp_down.type = INPUT_KEYBOARD
                        inp_down.union.ki.wScan = code
                        inp_down.union.ki.dwFlags = KEYEVENTF_UNICODE
                        # key up
                        inp_up = INPUT()
                        inp_up.type = INPUT_KEYBOARD
                        inp_up.union.ki.wScan = code
                        inp_up.union.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP
                        inputs.extend([inp_down, inp_up])

                    sent = self._send_input(inputs)
                    if sent == -1:
                        logger.error("KEYEVENTF_UNICODE 发送失败于第 %d 字符", i)
                        return False
                    success_count += sent

                if success_count < total * 2:
                    logger.warning(
                        "KEYEVENTF_UNICODE: 预期 %d 事件，实际 %d",
                        total * 2, success_count
                    )
                    return False
                return True
            except Exception as e:
                logger.error("send_unicode_text 异常: %s", e)
                return False

        def _make_key(self, vk: int, flags: int = 0) -> INPUT:
            """构造按键输入事件

            Args:
                vk: 虚拟键码（wScan 留 0，系统按 wVk 自动映射扫描码）
                flags: KEYEVENTF 标志位
            """
            inp = INPUT()
            inp.type = INPUT_KEYBOARD
            inp.union.ki.wVk = vk
            inp.union.ki.dwFlags = flags
            # dwExtraInfo 和 time 由 ctypes 零初始化（默认 0）
            return inp
