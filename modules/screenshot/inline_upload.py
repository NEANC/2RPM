#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""严格解析截图 CLI 的内联图床映射并准备凭证。"""

from collections.abc import Mapping
from copy import deepcopy
import re

from ruamel.yaml import YAML
from ruamel.yaml.constructor import SafeConstructor

from modules.image_host.core import ImageHostError
from modules.image_host.core import resolve_token
from modules.image_host.expiration import parse_expiration
from modules.image_host.registry import UPLOADERS


_ENV = re.compile(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}')
_NODE_PROPERTY = re.compile(r'(?:&[^\s,\[\]{};]+|!<[^>]*>|![^\s,\[\]{};]*)(?=\s)')
_AUTH_OPTIONS = {'beeimg': frozenset({'albumid'}), 'catbox': frozenset()}


class InlineUploadError(ValueError):
    """仅保存内联图床项序号与固定错误类别。"""

    def __init__(self, index, code):
        """构造不包含用户原文的固定错误。"""
        self.index = index
        self.code = code
        super().__init__(f'第{index}项：{code}')
        self.__cause__ = None
        self.__context__ = None


def _split_items(text):
    """使用引号和括号状态机切分顶层图床项。"""
    if not isinstance(text, str) or not text.strip():
        raise InlineUploadError(1, 'empty_item')
    items = []
    stack = []
    quote = None
    start = 0
    scalar_start = True
    index = 0
    while index < len(text):
        char = text[index]
        if quote is not None:
            if quote == '"' and char == '\\':
                if index + 1 >= len(text):
                    raise InlineUploadError(len(items) + 1, 'mapping_syntax')
                index += 2
                continue
            if quote == "'" and text[index:index + 2] == "''":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if scalar_start and char in '&!':
            property_match = _NODE_PROPERTY.match(text, index)
            if property_match is not None:
                index = property_match.end()
                continue
        if char in "\"'" and scalar_start:
            quote = char
            scalar_start = False
        elif char in '{[':
            stack.append(char)
            scalar_start = True
        elif char in '}]':
            expected = {'}': '{', ']': '['}[char]
            if not stack or stack[-1] != expected:
                raise InlineUploadError(len(items) + 1, 'mapping_syntax')
            stack.pop()
            scalar_start = False
        elif char == ';' and not stack:
            item = text[start:index].strip()
            if not item:
                raise InlineUploadError(len(items) + 1, 'empty_item')
            items.append(item)
            start = index + 1
            scalar_start = True
        elif char == ',' or (char == ':' and (
                index + 1 == len(text) or text[index + 1].isspace()
                or text[index + 1] in '{[\"\'')):
            scalar_start = True
        elif not char.isspace():
            scalar_start = False
        index += 1
    if quote is not None or stack:
        raise InlineUploadError(len(items) + 1, 'mapping_syntax')
    item = text[start:].strip()
    if not item:
        raise InlineUploadError(len(items) + 1, 'empty_item')
    return items + [item]


class _UniqueKeyConstructor(SafeConstructor):
    """在合并展开前校验显式键，保留安全构造器的覆盖规则。"""

    def __init__(self, *args, **kwargs):
        """为当前解析器保存已检查节点，避免别名重复展开。"""
        super().__init__(*args, **kwargs)
        self._checked_nodes = set()

    def flatten_mapping(self, node):
        """逐个检查原始映射键，避免 merge 绕过重复键校验。"""
        if node in self._checked_nodes:
            return
        self._checked_nodes.add(node)
        keys = set()
        merge_key = object()
        for key_node, _ in node.value:
            if key_node.tag == 'tag:yaml.org,2002:merge':
                key = merge_key
            else:
                key = self.construct_object(key_node, deep=True)
                if isinstance(key, list):
                    key = tuple(key)
            if key in keys:
                raise ValueError('duplicate_key')
            keys.add(key)
        super().flatten_mapping(node)


def _load_mapping(item, index):
    """以安全 YAML 解析单项并抹除底层异常链。"""
    source = item if item.startswith('{') else '{' + item + '}'
    parse_failed = False
    try:
        yaml = YAML(typ='safe')
        yaml.Constructor = _UniqueKeyConstructor
        yaml.allow_duplicate_keys = False
        value = yaml.load(source)
    except Exception:
        parse_failed = True
    if parse_failed:
        raise InlineUploadError(index, 'mapping_syntax')
    if not isinstance(value, Mapping):
        raise InlineUploadError(index, 'mapping_required')
    return dict(value)


def _needs_auth(host):
    """判断匿名图床是否携带必须认证的选项。"""
    return bool(_AUTH_OPTIONS.get(host['provider'], frozenset()).intersection(
        host.get('options', {})))


def parse_inline_hosts(text):
    """静态校验全部内联图床项，失败时不返回部分结果。"""
    hosts = []
    for index, item in enumerate(_split_items(text), 1):
        host = _load_mapping(item, index)
        provider = host.get('provider')
        if not isinstance(provider, str):
            raise InlineUploadError(index, 'provider_invalid')
        provider = provider.strip().lower()
        if provider not in UPLOADERS:
            raise InlineUploadError(index, 'provider_invalid')
        host['provider'] = provider
        options = host.get('options', {})
        if not isinstance(options, Mapping):
            raise InlineUploadError(index, 'options_invalid')
        host['options'] = dict(options)
        if 'token' in host:
            token = host['token']
            if not isinstance(token, str) or not token:
                raise InlineUploadError(index, 'token_invalid')
            if '${' in token and _ENV.fullmatch(token) is None:
                raise InlineUploadError(index, 'environment_invalid')
        elif provider not in _AUTH_OPTIONS or _needs_auth(host):
            raise InlineUploadError(index, 'token_required')
        if provider == 'beeimg' and 'albumid' in host['options']:
            albumid = host['options']['albumid']
            if not isinstance(albumid, str) or len(albumid) not in (5, 9):
                raise InlineUploadError(index, 'options_invalid')
            if 'token' not in host:
                raise InlineUploadError(index, 'token_required')
        if 'expiration' in host:
            if 'expired_at' in host['options']:
                raise InlineUploadError(index, 'expiration_invalid')
            try:
                parse_expiration(host['expiration'])
            except ImageHostError:
                raise InlineUploadError(index, 'expiration_invalid') from None
        hosts.append(host)
    return hosts


def prepare_cli_host(host):
    """解析一次环境凭证，保留匿名认证选项并返回固定失败分类。"""
    prepared = deepcopy(host)
    try:
        token = resolve_token(host.get('token'))
    except ImageHostError as error:
        if error.code != 'missing_environment':
            return None, error.code
        if host['provider'] not in _AUTH_OPTIONS or _needs_auth(host):
            return None, 'missing_environment'
        token = ''
    prepared['token'] = token
    return prepared, None
