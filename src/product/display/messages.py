"""各公共消息里的用户原文位置（JSON Pointer 模式，数组下标写成 ``*``）。这些文字保持原文：不绑定、不翻译，也不算进显示覆盖率的分母。
页面语言不改变用户输入的语种、偏好、模型语言或请求。

职业候选的 ``matched_terms`` 是从用户原文里认出来的词，同样算原文。能力清单里兴趣与菜系的识别别名是用户要输入的词（匹配器的
词表），同样照原文：翻成英文会让人以为输入英文也能认出来；与类别名同字的别名也不借类别名的英文。只写模式，不写具体内容。
"""

from __future__ import annotations

#: ``UserInputDraft`` 里
DRAFT = (
    "/free_text/text",
    "/occupation/raw_text",
    "/leisure_interest/text",
    "/taste/text",
    "/attribute_preferences/*/raw_text",
    "/hard_requirements/*/raw_text",
)
#: ``ProductCapabilities`` 里（识别别名与输入目录的搜索词：都是用户要输入的词，照原文）
CAPABILITIES = (
    "/interests/leisure_categories/*/aliases",
    "/interests/cuisines/*/aliases",
    "/input_catalog/occupations/*/search_terms",
    "/input_catalog/leisure/entries/*/search_terms",
    "/input_catalog/taste/entries/*/search_terms",
    "/input_catalog/leisure/search_notes/*/terms",
    "/input_catalog/taste/search_notes/*/terms",
)
#: ``ProfileReview`` 里
REVIEW = (
    "/occupation/raw_text",
    "/occupation/candidates/*/matched_terms",
    "/leisure_interest/text",
    "/leisure_interest/ambiguous",
    "/leisure_interest/unrecognized",
    "/taste/text",
    "/taste/ambiguous",
    "/taste/unrecognized",
    "/statements/*/raw_text",
)
#: ``ConfirmedProfile`` 里
PROFILE = (
    "/free_text",
    "/occupation/raw_text",
    "/interests/leisure_text",
    "/interests/taste_text",
    "/interests/unrecognized_text",
    "/retained/*/raw_text",
    "/hard_requirements/*/raw_text",
)


def under(prefix: str, patterns: tuple[str, ...]) -> tuple[str, ...]:
    """把相对一个内嵌对象的模式换成相对外层消息（例如 ``ReviewReply`` 里的 ``/review``）。"""
    return tuple(prefix + p for p in patterns)
