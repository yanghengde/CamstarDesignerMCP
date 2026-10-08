"""Short conversation titles derived from the first visible user message."""
import re

TITLE_MAX_LENGTH = 20


def clean_title(text: str) -> str:
    if not isinstance(text, str):
        return ''
    text = text.strip().splitlines()[0] if text.strip() else ''
    text = text.strip('"\'“”‘’`# ')
    text = re.sub(r'^(?:标题|名称)\s*[：:]\s*', '', text)
    text = re.sub(r'[\s"\'“”‘’`#，。！？、；：,.!?;:]+', '', text)
    return text[:TITLE_MAX_LENGTH]


def first_message_title(message: str) -> str:
    text = re.sub(r'^(?:请帮我|可以帮我|帮我|请|我想|我要|你好[，,！!\s]*)+', '', message.strip())
    return clean_title(text) or '设计对话'


def is_placeholder_title(title: str) -> bool:
    return bool(re.fullmatch(r'(?:新对话\s*\d*|默认会话|会话)', title or ''))
