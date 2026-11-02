"""Classify meaningful build log lines without treating ordinary output as success."""
import re


def log_level(message: str) -> str | None:
    text = message.strip()
    if (text.startswith('[ERROR]') or re.search(r'\b(?:fatal error|error):', text, re.I)
            or re.match(r'^\[run\] (?:failed|unexpected error)', text, re.I)
            or re.match(r'^\[run\] exit code (?!0\b)\d+', text, re.I)
            or re.match(r'^\[upload\].*\bfailed\b', text, re.I)
            or text.startswith('Traceback (most recent call last):')):
        return 'error'
    if (re.match(r'^\[(?:SPSA|BUILD)\] 成功:', text)
            or re.match(r'^\[run\] (?:all scripts completed|exit code 0\b)', text)
            or text == '[upload] all engine groups registered'):
        return 'success'
    if re.search(r'\bwarning:', text, re.I) or text.startswith('[WARNING]'):
        return 'warning'
    if re.match(r'^\[(?:SPSA|BUILD)\] 開始:', text) or text.startswith('[run] '):
        return 'stage'
    return None
