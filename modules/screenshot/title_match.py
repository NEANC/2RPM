from dataclasses import dataclass
import re


_MARKERS = ('@{version}', '@{VERSION}', '@{Version}')
_MARKER_CANDIDATE = re.compile(
    r'@\{\s*(?ai:version)\s*\}|@\{(?ai:version)(?=[^{}]*(?:\{|$))')
_CONTROL = re.compile(r'[\x00-\x1f\x7f-\x9f\u2028\u2029]')
_VERSION = re.compile(
    r'(?:(?:[vV] *|(?ai:version) +))?'
    r'[0-9]+(?:\.[0-9]+){1,3}(?:[ +\-].*)?')
_HASH = re.compile(r'[gG]?[0-9a-fA-F]{7,64}')


@dataclass(frozen=True)
class WindowTarget:
    text: str
    base: str
    versioned: bool


def has_version_marker_candidate(text: str) -> bool:
    return _MARKER_CANDIDATE.search(text) is not None


def parse_window_target(text: str) -> WindowTarget:
    if not isinstance(text, str) or not text.strip():
        raise ValueError('invalid_target')
    candidates = list(_MARKER_CANDIDATE.finditer(text))
    if not candidates:
        return WindowTarget(text, text, False)
    marker = candidates[0]
    if (len(candidates) != 1 or marker.group() not in _MARKERS
            or marker.end() != len(text) or not text[:marker.start()].strip()):
        raise ValueError('invalid_target')
    return WindowTarget(text, text[:marker.start()], True)


def matches_window_title(target: WindowTarget, title: str) -> bool:
    if not target.versioned:
        return target.text == title
    if _CONTROL.search(title):
        return False
    base = target.base.casefold()
    if title.casefold() == base:
        return True
    for index, character in enumerate(title):
        if character != ' ' or title[:index].casefold() != base:
            continue
        suffix = title[index:].lstrip(' ')
        if _VERSION.fullmatch(suffix) or _HASH.fullmatch(suffix):
            return True
    return False


def validate_capture_target(source: str, target: str) -> None:
    if source == 'window':
        parse_window_target(target)
        return
    if (source != 'adb' or not isinstance(target, str) or not target.strip()
            or has_version_marker_candidate(target)):
        raise ValueError('invalid_target')
