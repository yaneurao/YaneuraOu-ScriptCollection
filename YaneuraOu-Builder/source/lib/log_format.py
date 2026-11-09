"""Classify meaningful build log lines without treating ordinary output as success."""
import re


def build_error_hint(message: str) -> str | None:
    if ('libclang_rt.profile' in message
            and re.search(r'could not open|cannot open|cannot find|no such file|file not found', message, re.I)):
        return (
            'ClangのPGO用ライブラリが見つかりません。\n\n'
            'MSYS2 MINGW64のターミナルで次を実行し、ビルドを再実行してください。\n\n'
            'pacman -S mingw-w64-x86_64-compiler-rt\n\n'
            '導入済みの場合は、Clangとcompiler-rtのバージョン・配置の整合を確認してください。'
        )
    return None


def log_level(message: str) -> str | None:
    text = message.strip()
    if (text.startswith('[ERROR]') or re.search(r'\b(?:fatal error|error):', text, re.I)
            or re.match(r'^\[run\] (?:failed|unexpected error)', text, re.I)
            or re.match(r'^\[run\] exit code (?!0\b)\d+', text, re.I)
            or re.match(r'^\[upload\].*\bfailed\b', text, re.I)
            or text.startswith('Traceback (most recent call last):')):
        return 'error'
    if (re.match(r'^\[(?:SPSA|BUILD)\] 成功:', text)
            or re.match(r'^\[PGO\] (?:DONE|完了):', text)
            or re.match(r'^\[run\] (?:all scripts completed|exit code 0\b)', text)
            or text == '[upload] all engine groups registered'):
        return 'success'
    if re.search(r'\bwarning:', text, re.I) or text.startswith('[WARNING]'):
        return 'warning'
    if (re.match(r'^\[(?:SPSA|BUILD|PGO)\] 開始:', text)
            or text.startswith('[PGO] START:') or text.startswith('[run] ')):
        return 'stage'
    return None
