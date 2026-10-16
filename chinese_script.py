"""Normalize acoustic Chinese to the application's existing simplified script.

Only fresh model readings pass through here. User text, personal targets and
quotes already present in an input field are never converted by this module.
"""
from functools import lru_cache
import re


@lru_cache(maxsize=1)
def converter():
    from opencc import OpenCC
    # Plain character/phrase conversion; no regional vocabulary conversion.
    return OpenCC('t2s')


def acoustic_simplified(text):
    if not re.search('[\u3400-\u9fff]',text):return text
    try:return converter().convert(text)
    except (ImportError,OSError,RuntimeError):return text
