"""下载、解析失败的类别。单独一个模块是为了让 provider 也能抛这些异常（pipeline 反过来 import provider）。"""


class ProcessError(Exception):
    """一部法规下载或解析失败，消息说明原因。"""
    kind = "失败"


class DeadLinkError(ProcessError):
    """源链接已失效（404、无效地址），重试也没用。"""
    kind = "链接失效"


class EmptyContentError(ProcessError):
    """下载成功但拿不到正文：跳转桩、JS 验证页、空壳页面。"""
    kind = "内容为空"
