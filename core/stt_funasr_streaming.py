"""FunASR 流式引擎

封装 FunASR paraformer-zh-streaming 模型的流式 API，
提供 chunk-by-chunk 增量转写能力。

线程模型：
- _lock 保护 _cache 状态
- transcribe_chunk 线程安全
"""

import os
import time
import threading
import logging
import re
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class FunASRStreamingEngine:
    """FunASR 流式引擎
    
    chunk_size = [0, 10, 5] 表示：
    - 0: 前瞻块数
    - 10: 当前块 = 10 * 60ms = 600ms
    - 5: 后瞻块 = 5 * 60ms = 300ms lookahead
    
    300ms lookahead 确保不会切断音节边界。
    """
    
    CHUNK_SIZE = [0, 10, 5]
    CHUNK_MS = 600  # 固定值，不可配置
    CHUNK_SAMPLES = 9600  # 16000 * 0.6
    
    def __init__(self, config):
        self.config = config
        self.model = None
        self._cache = {}  # 流式状态缓存
        self._lock = threading.Lock()  # 保护 cache 状态
        self._model_loaded = False
        self._load_error: Optional[str] = None
    
    def load_model(self) -> tuple[bool, str]:
        """加载 paraformer-zh-streaming 模型
        
        Returns:
            (success, error_msg) - 成功时 error_msg 为空
        """
        try:
            # 设置 ModelScope 镜像（可选）
            modelscope_endpoint = getattr(self.config, 'modelscope_endpoint', '')
            if modelscope_endpoint:
                os.environ['MODELSCOPE_ENDPOINT'] = modelscope_endpoint
                logger.info("使用 ModelScope 镜像: %s", modelscope_endpoint)
            
            from funasr import AutoModel
            
            # 使用流式模型
            model_name = 'paraformer-zh-streaming'
            logger.info("加载 FunASR 流式模型: %s", model_name)
            
            self.model = AutoModel(
                model=model_name,
                device="cpu",
                disable_update=True,
            )
            
            self._model_loaded = True
            self._load_error = None
            logger.info("FunASR 流式模型加载完成")
            return True, ''
            
        except ImportError as e:
            error_msg = "funasr 未安装，请运行: pip install funasr modelscope"
            logger.error(error_msg)
            self._load_error = error_msg
            return False, error_msg
            
        except Exception as e:
            error_msg = str(e)
            logger.error("FunASR 流式模型加载失败: %s", error_msg)
            self._load_error = error_msg
            return False, error_msg
    
    def get_chunk_samples(self) -> int:
        """返回引擎要求的 chunk 样本数"""
        return self.CHUNK_SAMPLES
    
    def transcribe_chunk(self, audio_chunk: np.ndarray, is_final: bool) -> str:
        """流式转写 - 每600ms调用一次
        
        Args:
            audio_chunk: 音频数据（长度由 get_chunk_samples 决定）
            is_final: 最后一个chunk时True，强制输出
            
        Returns:
            当前 chunk 的增量文本（非累积全量）
            - 中间 chunk：可能返回增量，也可能为空（等待更多上下文）
            - is_final=True：返回最后累积的文本
            - 异常时返回空字符串，不中断循环
        """
        if not self._model_loaded or self.model is None:
            logger.warning("模型未加载，无法转写")
            return ''
        
        try:
            with self._lock:
                result = self.model.generate(
                    input=audio_chunk,
                    cache=self._cache,
                    is_final=is_final,
                    chunk_size=self.CHUNK_SIZE,
                    encoder_chunk_look_back=4,
                    decoder_chunk_look_back=1
                )
                
                if result and len(result) > 0:
                    text = result[0].get('text', '') or ''
                    if text:
                        # FunASR 默认输出分词结果（词间有空格），去掉中文空格
                        while re.search(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', text):
                            text = re.sub(r'([\u4e00-\u9fff])\s+([\u4e00-\u9fff])', r'\1\2', text)
                        logger.debug("流式转写: '%s' (is_final=%s)", text[:30], is_final)
                    return text
                return ''
                
        except Exception as e:
            import traceback
            logger.error("流式转写异常: %s\n%s", e, traceback.format_exc())
            return ''  # 返回空字符串，不中断循环
    
    def reset(self):
        """重置流式状态，开始新会话（线程安全）"""
        with self._lock:
            self._cache = {}
            logger.debug("流式状态已重置")
    
    def is_ready(self) -> bool:
        """检查引擎是否就绪"""
        return self._model_loaded and self.model is not None
    
    def get_error(self) -> Optional[str]:
        """获取加载错误信息"""
        return self._load_error