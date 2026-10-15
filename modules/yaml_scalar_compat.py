#!/usr/bin/env python3
# -_- coding: utf-8 -_-

import re


class ScalarSyntaxError(ValueError):

    def __init__(self, line, column, code):
        self.line = line
        self.column = column
        self.code = code
        # 内联图床项序号，仅内联扫描失败时由 normalize_inline_upload 填充
        self.item_index = None
        super().__init__(f'line={line} column={column} code={code}')
        self.__cause__ = None
        self.__context__ = None


_VERSION_MARKERS = ('@{version}', '@{VERSION}', '@{Version}')
_QUOTED_MARKERS = ('{version}', '{VERSION}', '{Version}')
_ENV_REFERENCE = re.compile(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}')
_CONFIG_WHITELIST = frozenset({
    ('push', 'screenshot', 'targets', 'target'),
    ('push', 'screenshot', 'image_host', 'token'),
})
_TARGET_PATHS = frozenset({('push', 'screenshot', 'targets', 'target')})
_TOKEN_PATHS = frozenset({('push', 'screenshot', 'image_host', 'token')})
_BOUNDARY = ' \t\r\n,;}]'
_ALIAS = re.compile(r'\*[^\s,\[\]{}]+')
_ANCHOR = re.compile(r'&[^\s,\[\]{}]+')
_TAG = re.compile(r'![^\s,\[\]{};]*')


def _is_boundary(text, pos):
    if pos >= len(text):
        return True
    return text[pos] in _BOUNDARY


def _quoted_literal(value):
    return "'" + value.replace("'", "''") + "'"


class _Scanner:

    def __init__(self, text):
        self.text = text
        self.length = len(text)
        self.pos = 0
        self.line = 1
        self.column = 1
        self.output = []

    def _copy(self):
        char = self.text[self.pos]
        self.output.append(char)
        self.pos += 1
        if char == '\n':
            self.line += 1
            self.column = 1
        else:
            self.column += 1

    def _copy_many(self, count):
        for _ in range(count):
            self._copy()

    def _advance(self, count):
        self.pos += count
        self.column += count

    def _newline(self):
        if (self.text[self.pos] == '\r' and self.pos + 1 < self.length
                and self.text[self.pos + 1] == '\n'):
            self.output.append('\r\n')
            self.pos += 2
        else:
            self.output.append(self.text[self.pos])
            self.pos += 1
        self.line += 1
        self.column = 1

    def _comment(self):
        while self.pos < self.length and self.text[self.pos] not in '\r\n':
            self._copy()

    def _quoted(self):
        quote = self.text[self.pos]
        self._copy()
        while self.pos < self.length:
            char = self.text[self.pos]
            if quote == '"' and char == '\\':
                self._copy()
                if self.pos < self.length:
                    self._copy()
                continue
            if (quote == "'" and char == "'" and self.pos + 1 < self.length
                    and self.text[self.pos + 1] == "'"):
                self._copy()
                self._copy()
                continue
            if char == quote:
                self._copy()
                return
            if char in '\r\n':
                self._newline()
                continue
            self._copy()

    def _quoted_end(self, pos):
        quote = self.text[pos]
        index = pos + 1
        while index < self.length:
            if quote == '"' and self.text[index] == '\\':
                index += 2
                continue
            if (quote == "'" and self.text[index] == "'"
                    and index + 1 < self.length
                    and self.text[index + 1] == "'"):
                index += 2
                continue
            if self.text[index] == quote:
                return index + 1
            index += 1
        return None

    def _alias(self):
        match = _ALIAS.match(self.text, self.pos)
        if match is not None:
            self._copy_many(match.end() - self.pos)
        else:
            self._copy()

    def _property_end(self, pos):
        char = self.text[pos]
        if char == '&':
            match = _ANCHOR.match(self.text, pos)
            return match.end() if match is not None else None
        if char == '!':
            if self.text.startswith('!<', pos):
                end = self.text.find('>', pos)
                return end + 1 if end != -1 else None
            match = _TAG.match(self.text, pos)
            return match.end() if match is not None else None
        return None

    def _backtick(self):
        start = self.pos
        index = start + 1
        while index < self.length and self.text[index] != '`':
            if self.text[index] in '\r\n':
                raise ScalarSyntaxError(
                    self.line, self.column, 'backtick_multiline')
            index += 1
        if index >= self.length:
            raise ScalarSyntaxError(
                self.line, self.column, 'backtick_unterminated')
        if not _is_boundary(self.text, index + 1):
            raise ScalarSyntaxError(
                self.line, self.column, 'backtick_trailing')
        self.output.append(_quoted_literal(self.text[start + 1:index]))
        self._advance(index + 1 - self.pos)


class _InlineScanner(_Scanner):

    def __init__(self, text):
        super().__init__(text)
        self.stack = []
        self.quote = None
        self.comment = False
        self.scalar_start = True
        self.explicit_top = False
        self.seen_content = False
        self.item_index = 1

    def run(self):
        while self.pos < self.length:
            char = self.text[self.pos]
            if self.comment:
                self._copy()
                if char in '\r\n':
                    self.comment = False
                continue
            if self.quote is not None:
                self._quote_char()
                continue
            if char == '#' and (self.scalar_start or self.pos == 0
                                or self.text[self.pos - 1].isspace()):
                self.comment = True
                self._copy()
                continue
            if (char == '`' and self.scalar_start and self._top()
                    and self._token_value()):
                self._backtick()
                self.scalar_start = False
                self.seen_content = True
                continue
            if (char == '$' and self.scalar_start and self._top()
                    and self._token_value()):
                match = _ENV_REFERENCE.match(self.text, self.pos)
                if match is not None and _is_boundary(self.text, match.end()):
                    self.output.append(_quoted_literal(match.group()))
                    self._advance(match.end() - self.pos)
                    self.scalar_start = False
                    self.seen_content = True
                    continue
            if char in '"\'' and self.scalar_start:
                self.quote = char
                self.scalar_start = False
                self.seen_content = True
                self._copy()
                continue
            if char in '{[':
                if char == '{' and not self.stack and not self.seen_content:
                    self.explicit_top = True
                self.stack.append(char)
                self.scalar_start = True
                self.seen_content = True
                self._copy()
                continue
            if char in '}]':
                if self.stack:
                    self.stack.pop()
                self.scalar_start = False
                self.seen_content = True
                self._copy()
                continue
            if char == ';' and not self.stack:
                self._copy()
                self.item_index += 1
                self.explicit_top = False
                self.seen_content = False
                self.scalar_start = True
                continue
            if char == '?' and self.scalar_start and (
                    self.pos + 1 >= self.length
                    or self.text[self.pos + 1].isspace()):
                self.scalar_start = True
                self.seen_content = True
                self._copy()
                continue
            if char == '*' and self.scalar_start:
                match = _ALIAS.match(self.text, self.pos)
                if match is not None:
                    self._copy_many(match.end() - self.pos)
                    self.scalar_start = False
                    self.seen_content = True
                    continue
            if char in '&!' and self.scalar_start:
                end = self._property_end(self.pos)
                if end is not None:
                    self._copy_many(end - self.pos)
                    self.seen_content = True
                    continue
            if char == ',' or (char == ':' and (
                    self.pos + 1 >= self.length
                    or self.text[self.pos + 1].isspace()
                    or self.text[self.pos + 1] in '{[\"\'')):
                self.scalar_start = True
                self.seen_content = True
                self._copy()
                continue
            if not char.isspace():
                self.scalar_start = False
                self.seen_content = True
            self._copy()
        return ''.join(self.output)

    def _quote_char(self):
        char = self.text[self.pos]
        if self.quote == '"' and char == '\\':
            self._copy()
            if self.pos < self.length:
                self._copy()
            return
        if self.quote == "'" and self.text[self.pos:self.pos + 2] == "''":
            self._copy()
            self._copy()
            return
        if char == self.quote:
            self.quote = None
        self._copy()

    def _top(self):
        if not self.stack:
            return True
        return len(self.stack) == 1 and self.explicit_top

    def _token_value(self):
        index = self.pos - 1
        while index >= 0 and self.text[index] in ' \t':
            index -= 1
        if index < 0 or self.text[index] != ':':
            return False
        index -= 1
        while index >= 0 and self.text[index] in ' \t':
            index -= 1
        if index < 4 or self.text[index - 4:index + 1] != 'token':
            return False
        if index >= 5 and (self.text[index - 5].isalnum()
                           or self.text[index - 5] in '_-'):
            return False
        return True


class _ConfigScanner(_Scanner):

    def __init__(self, text):
        super().__init__(text)
        self.keys = []
        self.flow = []

    def run(self):
        while self.pos < self.length:
            char = self.text[self.pos]
            if char in '\r\n':
                self._newline()
                continue
            if char in ' \t':
                self._copy()
                continue
            if char == '#':
                self._comment()
                continue
            if char == '-' and (
                    self.pos + 1 >= self.length
                    or self.text[self.pos + 1] in ' \t\r\n'):
                self._copy()
                continue
            if char in '{[':
                while self.keys and self.keys[-1][0] >= self.column - 1:
                    self.keys.pop()
                self._flow_collection(tuple(key for _, key in self.keys))
                continue
            if self._key():
                continue
            self._plain_block()
        return ''.join(self.output)

    def _key(self):
        result = self._scan_key(self.pos)
        if result is None:
            return False
        colon, key = result
        indent = self.column - 1
        self._copy_many(colon - self.pos)
        self._copy()
        self._block_value(indent, key)
        return True

    def _scan_key(self, pos):
        index = pos
        while index < self.length and self.text[index] in '&!':
            end = self._property_end(index)
            if end is None:
                break
            index = end
            while index < self.length and self.text[index] in ' \t':
                index += 1
        if index >= self.length:
            return None
        char = self.text[index]
        if char in '"\'':
            end = self._quoted_end(index)
            if end is None:
                return None
            key = self.text[index + 1:end - 1]
            index = end
        elif char in '{}[]*':
            return None
        else:
            start = index
            while index < self.length and self.text[index] not in '\r\n':
                if self.text[index] == ':' and (
                        index + 1 >= self.length
                        or self.text[index + 1] in ' \t\r\n'):
                    break
                if (self.text[index] == '#' and index > start
                        and self.text[index - 1] in ' \t'):
                    return None
                index += 1
            if index >= self.length or self.text[index] != ':':
                return None
            key = self.text[start:index].strip()
        while index < self.length and self.text[index] in ' \t':
            index += 1
        if index >= self.length or self.text[index] != ':':
            return None
        if not key:
            return None
        return index, key

    def _block_value(self, indent, key):
        while self.keys and self.keys[-1][0] >= indent:
            self.keys.pop()
        path = tuple(item for _, item in self.keys) + (key,)
        while self.pos < self.length and self.text[self.pos] in ' \t':
            self._copy()
        while self.pos < self.length and self.text[self.pos] in '&!':
            end = self._property_end(self.pos)
            if end is None:
                break
            self._copy_many(end - self.pos)
            while self.pos < self.length and self.text[self.pos] in ' \t':
                self._copy()
        if self.pos >= self.length or self.text[self.pos] in '\r\n#':
            self.keys.append([indent, key])
            return
        char = self.text[self.pos]
        if char in '{[':
            self._flow_collection(path)
            return
        if char in '|>':
            self._block_scalar(indent)
            return
        if char in '"\'':
            self._quoted()
            return
        if char == '*':
            self._alias()
            return
        if char == '`' and path in _CONFIG_WHITELIST:
            self._backtick()
            return
        self._plain_block()

    def _block_scalar(self, parent_indent):
        self._copy()
        while self.pos < self.length and self.text[self.pos] in '+-0123456789':
            self._copy()
        while self.pos < self.length and self.text[self.pos] not in '\r\n':
            self._copy()
        if self.pos < self.length:
            self._newline()
        while self.pos < self.length:
            index = self.pos
            spaces = 0
            while index < self.length and self.text[index] == ' ':
                index += 1
                spaces += 1
            if index >= self.length:
                break
            if self.text[index] in '\r\n':
                while self.pos < self.length and self.text[self.pos] not in '\r\n':
                    self._copy()
                self._newline()
                continue
            if spaces <= parent_indent:
                break
            while self.pos < self.length and self.text[self.pos] not in '\r\n':
                self._copy()
            if self.pos < self.length:
                self._newline()

    def _plain_block(self):
        while self.pos < self.length and self.text[self.pos] not in '\r\n':
            if (self.text[self.pos] == '#' and self.pos > 0
                    and self.text[self.pos - 1] in ' \t'):
                break
            self._copy()

    def _flow_collection(self, path):
        char = self.text[self.pos]
        kind = 'map' if char == '{' else 'sequence'
        self._copy()
        self.flow.append({'kind': kind, 'path': path, 'key': None,
                          'expect_key': kind == 'map'})
        while self.flow and self.pos < self.length:
            char = self.text[self.pos]
            if char in '\r\n':
                self._newline()
                continue
            if char in ' \t':
                self._copy()
                continue
            if char == '#':
                self._comment()
                continue
            frame = self.flow[-1]
            if char in '{[':
                inner = 'map' if char == '{' else 'sequence'
                child = frame['path']
                if frame['kind'] == 'map' and frame['key'] is not None:
                    child = frame['path'] + (frame['key'],)
                self._copy()
                self.flow.append({'kind': inner, 'path': child, 'key': None,
                                  'expect_key': inner == 'map'})
                continue
            if char in '}]':
                self._copy()
                self.flow.pop()
                if self.flow and self.flow[-1]['kind'] == 'map':
                    self.flow[-1]['key'] = None
                    self.flow[-1]['expect_key'] = True
                continue
            if char == ',':
                self._copy()
                frame['key'] = None
                frame['expect_key'] = frame['kind'] == 'map'
                continue
            if char == ':':
                self._copy()
                continue
            self._flow_scalar(frame)

    def _flow_scalar(self, frame):
        if frame['kind'] == 'sequence':
            self._flow_token(frame, frame['path'], False)
            return
        if frame['expect_key']:
            self._flow_token(frame, frame['path'], True)
            return
        key = frame['key']
        path = frame['path'] + (key,) if key is not None else frame['path']
        self._flow_token(frame, path, False)

    def _flow_token(self, frame, path, is_key):
        char = self.text[self.pos]
        if char in '"\'':
            # 引号内文本仅用于键名判定，输出仍原样复制引号形式
            key = None
            if is_key:
                end = self._quoted_end(self.pos)
                if end is not None:
                    key = self.text[self.pos + 1:end - 1]
            self._quoted()
            self._record(frame, is_key, key)
            return
        if char == '*':
            self._alias()
            self._record(frame, is_key, None)
            return
        if char == '`' and not is_key and path in _CONFIG_WHITELIST:
            self._backtick()
            self._record(frame, is_key, None)
            return
        if char in '&!':
            end = self._property_end(self.pos)
            if end is not None:
                self._copy_many(end - self.pos)
                return
        token = self._flow_plain(path, is_key)
        self._record(frame, is_key, token)

    def _record(self, frame, is_key, token):
        if frame['kind'] != 'map':
            return
        if is_key:
            frame['key'] = token.strip() if token else None
            frame['expect_key'] = False
        else:
            frame['key'] = None
            frame['expect_key'] = True

    def _flow_plain(self, path, is_key):
        chunks = []
        while self.pos < self.length:
            char = self.text[self.pos]
            if char in ',\r\n]}':
                break
            if char == ':' and (
                    self.pos + 1 >= self.length
                    or self.text[self.pos + 1] in ' \t\r\n,]}'):
                break
            if char == ' ':
                following = (self.text[self.pos + 1]
                             if self.pos + 1 < self.length else '')
                if following == '#':
                    break
                chunks.append(char)
                self._advance(1)
                continue
            if char == '{':
                consumed = False
                if (not is_key and path in _TARGET_PATHS and chunks
                        and chunks[-1] == '@'):
                    for marker in _QUOTED_MARKERS:
                        if self.text.startswith(marker, self.pos):
                            chunks.append(marker)
                            self._advance(len(marker))
                            consumed = True
                            break
                if consumed:
                    continue
                break
            if char == '$' and not is_key and path in _TOKEN_PATHS and not chunks:
                match = _ENV_REFERENCE.match(self.text, self.pos)
                if (match is not None
                        and _is_boundary(self.text, match.end())):
                    self.output.append(_quoted_literal(match.group()))
                    self._advance(match.end() - self.pos)
                    return match.group()
                chunks.append(char)
                self._advance(1)
                continue
            chunks.append(char)
            self._advance(1)
        text = ''.join(chunks)
        if (not is_key and path in _TARGET_PATHS
                and any(marker in text for marker in _VERSION_MARKERS)):
            self.output.append(_quoted_literal(text))
        else:
            self.output.append(text)
        return text


def normalize_config_scalars(text):
    if '`' not in text and '{' not in text:
        return text
    return _ConfigScanner(text).run()


def normalize_inline_upload(text):
    if not isinstance(text, str):
        return text
    if '`' not in text and '${' not in text:
        return text
    scanner = _InlineScanner(text)
    try:
        return scanner.run()
    except ScalarSyntaxError as error:
        error.item_index = scanner.item_index
        raise
