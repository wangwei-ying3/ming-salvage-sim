"""自定义异常。L0 叶子模块。"""

from __future__ import annotations


class ExitGame(Exception):
    pass


class LLMUnavailable(Exception):
    pass


class LLMContractError(Exception):
    pass
