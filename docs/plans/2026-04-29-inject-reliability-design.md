# 文字注入可靠性优化设计方案

> **项目**: voice-input-tool  
> **版本**: v3.0  
> **日期**: 2026-04-29  
> **作者**: Saber  
> **状态**: 三轮评审中

### 评审历程
- **v1.0**: 评审发现 7 项问题（3 严重 / 3 中等 / 1 轻微），v2.0 全部修复
- **v2.0**: 6/7 项 ✅已修复，1 项 ⚠️部分修复；新发现 2 个问题（中等 + 轻微偏中等）
- **v3.0**: 修复 v2.0 遗留的 ⚠️ + 2 个新发现问题，待三轮评审

---

## 一、问题描述

### 1.1 现象
- 用户在微信/企业微信中完成语音输入后，日志显示"注入成功"，但文字未出现在输入框
- 偶发性问题，非 100% 复现
- 记事本等原生 Win32 应用基本正常

### 1.2 根因分析
当前注入流程：`write_clipboard()` → `sleep(0.05)` → `keyboard.send('ctrl+v')`

**问题 1：keyboard.send 不可靠**
- `keyboard` 底层调用 `SendInput`，但经过额外抽象层（hook、键盘状态管理等），在某些场景下会静默失败（不抛异常）
- Electron 应用（微信/企业微信）的消息泵与 `keyboard` 库的 hook 注入存在时序竞争

**问题 2：延时不足**
- `sleep(0.05)` 仅 50ms，而剪贴板写入（PowerShell）需要时间。目标应用可能还没来得及从剪贴板读取就收到了 Ctrl+V
- PowerShell 写入成功返回后，操作系统层面剪贴板数据可能仍在传递中

**问题 3：降级路径不生效**
- `keyboard.send` 静默成功（返回 None、不抛异常）→ 永远不会走到 `_simulate_paste_sendinput()` 降级路径

### 1.3 能否解决当前问题？

**能。** 核心改动是：
1. 用 Win32 `SendInput` 直接调用替代 `keyboard.send`，消除 keyboard 库的 hook 层带来的时序竞争——这是导致 Electron 应用注入失败的主要原因
2. 延时从 50ms 增加到 100ms，给剪贴板充足就绪时间
3. SendInput 返回值检查 + 日志增强，失败时可见可排查，不再"静默成功"

**局限性**：SendInput 仍有极小概率在 Electron 中失败（UIPI、窗口焦点瞬间切换等极端场景），但有降级链路兜底。

### 1.4 影响范围
- **严重**: 微信/企业微信（Electron 应用）
- **轻微**: 其他标准 Win32 应用（记事本、Word）
- **无影响**: Mac（使用 DummyKeySimulator）

---

## 二、设计目标

| 目标 | 说明 |
|------|------|
| 注入可靠性 | 微信/企业微信场景下注入成功率 > 99% |
| 兼容性 | 不影响 Mac 平台、不影响现有配置 |
| 性能 | 注入延迟增加 < 200ms（可接受） |
| 可维护性 | 减少对 `keyboard` 库的依赖 |
| 可观测性 | 注入失败有明确日志，便于排查 |

---

## 三、技术方案

### 3.1 整体策略

**剪贴板写入** → **Win32 SendInput 模拟粘贴（主路径）** → **KEYEVENTF_UNICODE 直输（降级）** → **keyboard.send（最后兜底）**

> **v2.0 修正**：去掉了 keyboard.send 作为中间降级（它会静默成功导致后续降级走不到），改为最后兜底。将 KEYEVENTF_UNICODE 提升为第一降级。

### 3.2 模块设计

#### 3.2.1 模块结构

```
platform_adapter/
├── win32_input.py          # 新增：Win32 输入模拟（模块级单例）
├── clipboard_windows.py    # 修改：simulate_paste 改用 win32_input
├── clipboard_base.py       # 修改：注入策略提升到此处 + 延时常量
└── key_simulator.py        # 不变
```

#### 3.2.2 `win32_input.py` 接口设计

```python
# 模块级单例，避免重复创建
_win32_input_instance = None

def get_win32_input() -> 'Win32Input':
    """获取 Win32Input 单例"""
    global _win32_input_instance
    if _win32_input_instance is None:
        _win32_input_instance = Win32Input()
    return _win32_input_instance

class Win32Input:
    """Win32 输入模拟器（纯 ctypes，无第三方依赖）"""
    
    def simulate_ctrl_v(self) -> bool:
        """模拟 Ctrl+V 粘贴（SendInput 直接调用）"""
    
    def send_unicode_text(self, text: str, batch_size: int = 50) -> bool:
        """用 KEYEVENTF_UNICODE 批量发送文本（降级方案）"""
    
    def _send_input(self, inputs: list) -> int:
        """发送输入事件，返回实际成功数（-1 表示异常）"""
```

> **v2.0 修正**：
> - 模块级单例，避免每次创建新实例（评审问题 #6）
> - `send_unicode_text` 改为批量发送（评审问题 #5）
> - `simulate_ctrl_v` 去掉无用的 `delay_ms` 参数（评审问题 #4）

---

## 四、注入流程改造

### 4.1 当前流程
```
write_clipboard(text)
    ↓ (PowerShell → Win32 API 降级)
sleep(0.05)
    ↓
keyboard.send('ctrl+v')     ← 不可靠
    ↓ (仅异常时降级)
_simulate_paste_sendinput()  ← 永远走不到
```

### 4.2 新流程

```
_inject_via_keyboard(text)
    │
    ├─ write_clipboard(text)           ← 剪贴板写入（不变）
    │     ↓ (PowerShell → Win32 API 降级)
    │
    ├─ sleep(CLIPBOARD_SETTLE_MS)      ← 等待剪贴板就绪
    │
    ├─ simulate_paste()                ← 多态调用
    │     │
    │     ├─ [主] win32_input.simulate_ctrl_v()   ← SendInput 直接调用
    │     │     ↓ 失败
    │     ├─ [降级] send_unicode_text(text)       ← KEYEVENTF_UNICODE 直输
    │     │     ↓ 失败
    │     └─ [兜底] keyboard.send('ctrl+v')       ← 最后手段（接受静默成功）
    │
    ├─ sleep(POST_PASTE_WAIT_MS)       ← 等待目标应用处理
    │
    └─ restore_clipboard()             ← 恢复剪贴板（如有备份）
          sleep(CLIPBOARD_RESTORE_MS)
```

> **v2.0 关键修正**：
> 1. **注入策略提升到 `_inject_via_keyboard`**（评审问题 #2：职责分离）
> 2. **keyboard.send 降为最后兜底**（评审问题 #1、#2：解决静默成功导致降级断裂）
> 3. **流程图与代码完全一致**（评审问题 #1）

### 4.3 降级路径详细说明

| 优先级 | 方式 | 可靠性 | 失败时表现 |
|--------|------|--------|-----------|
| 1（主路径） | SendInput Ctrl+V | ⭐⭐⭐⭐⭐ | `simulate_ctrl_v()` 返回 False |
| 2（降级） | KEYEVENTF_UNICODE 直输 | ⭐⭐⭐ | `send_unicode_text()` 返回 False |
| 3（兜底） | keyboard.send Ctrl+V | ⭐⭐ | 静默成功，日志记录 WARN |

> **设计决策**：keyboard.send 作为最后兜底而非第一降级，因为：
> - 它会静默成功（不抛异常），放在前面会阻断后续降级
> - 但仍保留作为兜底，因为某些极端场景下 keyboard 库的 hook 机制反而能绕过某些限制
> - 此时日志会记录 WARN 级别提示，便于排查

---

## 五、详细代码设计

### 5.1 `platform_adapter/win32_input.py`

```python
"""Win32 输入模拟器

纯 ctypes 实现，无第三方依赖。
用于替代 keyboard.send，提高 Electron 应用兼容性。
仅 Windows 平台可用，其他平台导入时不会初始化。
"""

import sys
import ctypes
import logging

logger = logging.getLogger(__name__)

# 非 Windows 平台不注册任何实现
if sys.platform != 'win32':
    Win32Input = None
    def get_win32_input():
        return None
else:
    import ctypes.wintypes

    # Win32 常量
    INPUT_KEYBOARD = 1
    KEYEVENTF_KEYUP = 0x0002
    KEYEVENTF_UNICODE = 0x0004
    VK_CONTROL = 0x11
    VK_V = 0x56

    # ULONG_PTR: 32位系统 4 字节，64位系统 8 字节（自动适配）
    ULONG_PTR = ctypes.wintypes.ULONG_PTR

    # SendInput 结构体定义
    class KEYBDINPUT(ctypes.Structure):
        """Win32 KEYBDINPUT 结构体
        
        dwExtraInfo 使用 wintypes.ULONG_PTR，自动适配 32/64 位系统。
        """
        _fields_ = [
            ("wVk", ctypes.c_ushort),        # 虚拟键码
            ("wScan", ctypes.c_ushort),       # 硬件扫描码（0=自动映射）
            ("dwFlags", ctypes.c_ulong),      # 标志位
            ("time", ctypes.c_ulong),         # 时间戳（0=系统自动）
            ("dwExtraInfo", ULONG_PTR),       # 应用定义附加值
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
        """
        _fields_ = [
            ("mi", MOUSEINPUT),
            ("ki", KEYBDINPUT),
        ]

    class INPUT(ctypes.Structure):
        _fields_ = [
            ("type", ctypes.c_ulong),
            ("union", INPUT_UNION),
        ]

    # 模块级单例
    _instance = None

    def get_win32_input():
        """获取 Win32Input 单例（懒加载）"""
        global _instance
        if _instance is None:
            _instance = Win32Input()
        return _instance

    class Win32Input:
        """Win32 输入模拟器"""

        def __init__(self):
            self.user32 = ctypes.windll.user32

        def _send_input(self, inputs: list) -> int:
            """发送输入事件
            
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
            """
            inputs = [
                self._make_key(VK_CONTROL),                    # Ctrl down
                self._make_key(VK_V),                           # V down
                self._make_key(VK_V, KEYEVENTF_KEYUP),          # V up
                self._make_key(VK_CONTROL, KEYEVENTF_KEYUP),    # Ctrl up
            ]
            return self._send_input(inputs) == len(inputs)

        def send_unicode_text(self, text: str, batch_size: int = 50) -> bool:
            """用 KEYEVENTF_UNICODE 批量发送文本

            绕过剪贴板，直接向系统输入队列发送 Unicode 字符。
            速度较慢，仅作为降级方案。

            Args:
                text: 要发送的文本
                batch_size: 每批发送的字符数（避免输入队列溢出）
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
            # dwExtraInfo 和 time 由 ctypes 零初始化（c_uint64=0）
            return inp
```

> **v2.0 修正**：
> - `dwExtraInfo` 改为 `ctypes.c_uint64`（ULONG_PTR），修复语义错误（评审问题 #3）
> - 去掉 `delay_ms` 参数，添加注释说明 SendInput 无需键间延时（评审问题 #4）
> - `send_unicode_text` 改为批量构造 + 分批发送（评审问题 #5）
> - 模块级单例 `get_win32_input()`（评审问题 #6）
> - 非 Windows 平台安全处理（`Win32Input = None`）
> - `_send_input` 返回值改为 `int`（-1=异常），更精确的错误区分

### 5.2 `clipboard_windows.py` 改造

```python
def simulate_paste(self) -> bool:
    """模拟 Ctrl+V（Win32 SendInput 直接调用优先）

    降级链：SendInput → KEYEVENTF_UNICODE → keyboard.send
    注意：keyboard.send 作为最后兜底，因为它会静默成功（不抛异常），
    放在前面会阻断后续降级路径。

    线程安全：pending_text 使用 threading.local 存储，
    每个线程有独立的副本，不会互相覆盖。
    """
    win32 = get_win32_input()
    if win32 is None:
        return False

    # 主路径：Win32 SendInput 模拟 Ctrl+V
    if win32.simulate_ctrl_v():
        logger.debug("simulate_paste: SendInput Ctrl+V 成功")
        return True
    logger.warning("simulate_paste: SendInput Ctrl+V 失败，尝试 KEYEVENTF_UNICODE")

    # 降级：KEYEVENTF_UNICODE 直输（绕过剪贴板）
    # pending_text 通过 threading.local 线程安全传递
    pending = getattr(_tls, 'pending_text', None)
    if pending:
        if win32.send_unicode_text(pending):
            logger.debug("simulate_paste: KEYEVENTF_UNICODE 成功")
            return True
        logger.warning("simulate_paste: KEYEVENTF_UNICODE 失败，尝试 keyboard.send")

    # 兜底：keyboard.send（可能静默成功）
    try:
        import keyboard
        keyboard.send('ctrl+v')
        logger.warning("simulate_paste: 使用 keyboard.send（可能静默成功）")
        return True
    except Exception as e:
        logger.error("simulate_paste: 所有方式均失败: %s", e)
    return False
```

> **v2.0 修正**：
> - 降级顺序改为 SendInput → KEYEVENTF_UNICODE → keyboard.send（评审问题 #1、#2）
> - KEYEVENTF_UNICODE 需要注入文本，通过 `threading.local` 传递（线程安全）
> - keyboard.send 降为最后兜底，日志标记为 WARN（评审问题 #2）

### 5.3 `clipboard_base.py` 改造

```python
class ClipboardInjectorBase(ABC):
    """剪贴板注入器基类"""

    # 延时常量（可配置，便于调优）
    CLIPBOARD_SETTLE_MS = 0.1     # 剪贴板写入后等待时间（秒）
    POST_PASTE_WAIT_MS = 0.05     # 粘贴后等待时间（秒）
    CLIPBOARD_RESTORE_MS = 0.15   # 恢复剪贴板前等待时间（秒）

    def _inject_via_keyboard(self, text: str) -> bool:
        """通过剪贴板+模拟粘贴注入文字

        注入策略：
        1. 写入剪贴板
        2. 等待剪贴板就绪
        3. 模拟粘贴（SendInput 优先，多级降级）
        4. 等待目标应用处理
        5. 恢复剪贴板

        线程安全：使用 threading.local 传递 pending_text，
        每个线程有独立的副本。调用方仍应串行调用此方法。
        """
        import time

        # 将待注入文本传递给 simulate_paste（KEYEVENTF_UNICODE 降级需要）
        # 使用 threading.local 确保线程安全
        _tls.pending_text = text

        try:
            if not self.write_clipboard(text):
                logger.error("写入剪贴板失败")
                return False

            time.sleep(self.CLIPBOARD_SETTLE_MS)

            if not self.simulate_paste():
                logger.error("模拟粘贴失败")
                return False

            time.sleep(self.POST_PASTE_WAIT_MS)

            logger.info("注入成功: %d 字符", len(text))
            return True
        except Exception as e:
            logger.error("注入失败: %s", e)
            return False
        finally:
            _tls.pending_text = None
```

> **v2.0 修正**：
> - 延时参数提取为类常量（评审问题 #7）
> - 注入策略（粘贴失败后尝试 KEYEVENTF_UNICODE）逻辑在此层协调
> - `_pending_text` 通过 `threading.local()` 传递，确保线程安全

### 5.4 `clipboard_base.py` 中的 `_inject_via_clipboard` 修改

```python
    def _inject_via_clipboard(self, text: str) -> bool:
        """通过剪贴板注入文字（含备份恢复）"""
        import time

        # 备份剪贴板
        try:
            self._backup = self.read_clipboard()
            self._backup_to_file(self._backup)
        except Exception:
            self._backup = None

        # 将待注入文本通过 threading.local 传递给 simulate_paste
        _tls.pending_text = text

        try:
            # 写入剪贴板
            if not self.write_clipboard(text):
                logger.error("写入剪贴板失败")
                return False

            time.sleep(self.CLIPBOARD_SETTLE_MS)

            # 模拟粘贴
            if not self.simulate_paste():
                logger.error("模拟粘贴失败")
                return False

        except Exception as e:
            logger.error("注入异常: %s", e)
            return False
        finally:
            _tls.pending_text = None

        # 恢复剪贴板
        if self.config.restore_clipboard and self._backup is not None:
            try:
                time.sleep(self.CLIPBOARD_RESTORE_MS)
                self.write_clipboard(self._backup)
            except Exception:
                logger.debug("恢复剪贴板失败（可忽略）")

        return True
```

---

## 六、Mac 平台兼容

- `win32_input.py` 模块在非 Windows 平台上将 `Win32Input` 设为 `None`，`get_win32_input()` 返回 `None`
- `simulate_paste()` 检测到 `win32 is None` 直接返回 False
- `key_simulator.py` 不变，Mac 上仍使用 `DummyKeySimulator`
- **零影响**：本次修改仅涉及 Windows 平台适配层

---

## 七、测试计划

### 7.1 单元测试

| 测试项 | 验证内容 | 优先级 |
|--------|---------|--------|
| `Win32Input.simulate_ctrl_v` | SendInput 返回值检查 | P0 |
| `Win32Input.send_unicode_text` | 批量发送、空文本、单字符、长文本 | P0 |
| `Win32Input.send_unicode_text` | 中英文混合、特殊字符（emoji） | P1 |
| `simulate_paste` 降级链 | SendInput→Unicode→keyboard 顺序正确 | P0 |
| `_inject_via_keyboard` | `_pending_text` 正确传递和清理 | P0 |
| 延时常量 | 类常量值正确 | P1 |
| 非 Windows 平台 | `get_win32_input()` 返回 None | P1 |

### 7.2 手动集成测试

| 测试场景 | 目标应用 | 预期结果 | 优先级 |
|---------|---------|---------|--------|
| 微信聊天输入框 | 微信 PC | 文字正确出现 | P0 |
| 企业微信聊天输入框 | 企业微信 | 文字正确出现 | P0 |
| 记事本 | 记事本 | 文字正确出现 | P1 |
| Word 文档 | Microsoft Word | 文字正确出现 | P1 |
| VS Code 编辑器 | Visual Studio Code | 文字正确出现 | P1 |
| 连续快速注入（5次） | 任意 | 无丢字、无重复 | P1 |
| 中文长文本（>100字） | 微信 | 完整输入 | P1 |
| 中英文混合文本 | 微信 | 正确出现 | P2 |
| emoji 表情 | 微信 | 正确出现 | P2 |

### 7.3 回归测试

- `config.method = 'clipboard'` 模式正常
- `config.method = 'keyboard'` 模式正常
- 剪贴板备份/恢复功能不受影响

---

## 八、风险评估

| 风险 | 等级 | 缓解措施 |
|------|------|---------|
| SendInput 在某些 Electron 应用仍失败 | 低 | KEYEVENTF_UNICODE 降级 + keyboard.send 兜底 |
| 延时增加影响用户体验 | 低 | 总增加 < 200ms，用户基本无感 |
| ctypes 调用在异常环境下崩溃 | 低 | 全 try-except 保护 + 日志记录 |
| KEYEVENTF_UNICODE 在 Electron 中兼容性差 | 中 | 仅作为降级，不是主路径；日志可观测 |
| UIPI 阻止 SendInput | 低 | 微信/企微不以提升权限运行 |

---

## 九、实现计划

| 步骤 | 内容 | 预计耗时 |
|------|------|---------|
| 1 | 新建 `win32_input.py` | 15min |
| 2 | 修改 `clipboard_windows.py` 的 `simulate_paste` | 10min |
| 3 | 修改 `clipboard_base.py`（延时常量 + `_pending_text` 传递） | 10min |
| 4 | 编写单元测试 | 20min |
| 5 | 提交 + 推送 | 5min |
| 6 | Master 手动验证 | 10min |

**总计**: ~70min

---

## 十、远期优化（本次不实现）

1. **注入结果验证**：通过 UI Automation 读取输入框内容，比对预期文本
2. **自适应延时**：根据注入成功率动态调整延时参数（使用配置文件而非常量）
3. **Mac 平台 SendInput**：使用 `CGEventPost` 实现 Mac 上的可靠注入
4. **流式输入**：实时转写结果逐步注入，而非等语音结束后一次性注入
5. **策略模式重构**：如需更多注入方式，可将降级链重构为策略模式

---

## 附录：评审问题修复清单

### v1.0 问题（v2.0/v3.0 修复）

| # | 严重度 | 问题 | 修复 | 核查 |
|---|--------|------|------|------|
| 1 | 🔴严重 | 流程图与代码不一致 | v2.0 流程图与代码一致 | ✅ |
| 2 | 🔴严重 | keyboard.send 静默成功阻断降级 | keyboard.send 降为最后兜底 | ✅ |
| 3 | 🔴严重 | ctypes dwExtraInfo 类型错误 | v3.0 改用 `ctypes.wintypes.ULONG_PTR` | ✅→v3 |
| 4 | 🟡中等 | delay_ms 未使用 | 移除参数 | ✅ |
| 5 | 🟡中等 | send_unicode_text 效率低 | 批量构造 + 分批发送 | ✅ |
| 6 | 🟡中等 | Win32Input 重复创建 | 模块级单例 | ✅ |
| 7 | 🟢轻微 | 延时硬编码 | 提取为类常量 | ✅ |

### v2.0 新发现问题（v3.0 修复）

| # | 严重度 | 问题 | 修复 |
|---|--------|------|------|
| 3-续 | 🟡中等 | dwExtraInfo 32位系统不兼容（c_uint64 固定 8 字节） | 改用 `ctypes.wintypes.ULONG_PTR`（自动适配 32/64 位） |
| A | 🟡中等 | `_pending_text` 实例属性非线程安全 | 改用 `threading.local()` 模块级 `_tls` |
| B | 🟡中等偏轻 | `INPUT_UNION` 缺少 MOUSEINPUT，sizeof(INPUT) 偏小 | 添加 MOUSEINPUT 占位结构体，确保联合体大小正确 |

---

*文档版本: v3.0*  
*等待三轮评审*
