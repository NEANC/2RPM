import pytest

from modules.screenshot.inline_upload import InlineUploadError
from modules.screenshot.inline_upload import parse_inline_hosts
from modules.yaml_scalar_compat import ScalarSyntaxError
from modules.yaml_scalar_compat import normalize_config_scalars
from modules.yaml_scalar_compat import normalize_inline_upload


BREAKS = ['\r', '\n', '\r\n', '\x85', '\u2028', '\u2029']


@pytest.mark.parametrize('newline', BREAKS)
def test_inline_comment_end_and_original_newlines(newline):
    """注释在所有 YAML 换行终止，转换后保留换行原文。"""
    text = 'provider: catbox, # ignored ; `broken' + newline + 'token: `a;b`'
    expected = text.replace('token: `a;b`', "token: 'a;b'")
    assert normalize_inline_upload(text) == expected
    assert parse_inline_hosts(text)[0]['token'] == 'a;b'


@pytest.mark.parametrize('newline', BREAKS)
@pytest.mark.parametrize('flow', [False, True])
def test_config_comments_and_boundaries_preserve_newlines(newline, flow):
    """块和流映射的注释、标量边界共用换行规则。"""
    lines = ['push:', '  screenshot:', '    image_host:']
    if flow:
        lines += ['      - {provider: catbox, # ignored `',
                  '         token: `safe`', '        }']
    else:
        lines += ['      - provider: catbox # ignored `',
                  '        token: `safe`', '        options: {}']
    text = newline.join(lines) + newline
    assert normalize_config_scalars(text) == text.replace('`safe`', "'safe'")


@pytest.mark.parametrize('newline', BREAKS)
@pytest.mark.parametrize('prefix', ['# comment', 'provider: "a', 'provider: "a\\'])
def test_inline_error_coordinates_after_newline(newline, prefix):
    """注释、引号及转义中的换行均准确更新行列。"""
    suffix = '' if prefix.startswith('#') else '",'
    text = prefix + newline + suffix + ' token: `bad' + newline + 'value`'
    with pytest.raises(ScalarSyntaxError) as caught:
        normalize_inline_upload(text)
    assert (caught.value.line, caught.value.column, caught.value.code) == (
        2, len(suffix) + 9, 'backtick_multiline')


@pytest.mark.parametrize('newline', BREAKS)
def test_config_multiline_backticks_rejected_at_original_position(newline):
    """配置跨行反引号报告原始起点且不泄露文本。"""
    text = newline.join(['push:', '  screenshot:', '    image_host:',
                         '      - token: `secret', 'tail`'])
    with pytest.raises(ScalarSyntaxError) as caught:
        normalize_config_scalars(text)
    assert (caught.value.line, caught.value.column, caught.value.code) == (
        4, 16, 'backtick_multiline')
    assert 'secret' not in str(caught.value)


@pytest.mark.parametrize('newline', BREAKS)
def test_inline_item_index_after_comment_newline(newline):
    """两层扫描器一致终止注释，跨行反引号归属第二项。"""
    text = ('provider: catbox # ignored ;' + newline
            + '; provider: catbox, token: `secret' + newline + 'tail`')
    with pytest.raises(InlineUploadError) as caught:
        parse_inline_hosts(text)
    assert (caught.value.index, caught.value.code) == (2, 'mapping_syntax')


def test_mixed_newlines_preserved_and_crlf_counted_once():
    """混合换行逐字保留，CRLF 只占一行。"""
    text = '# a\r\n# b\r# c\x85# d\u2028# e\u2029token: `safe`\n'
    assert normalize_inline_upload(text) == text.replace('`safe`', "'safe'")
    with pytest.raises(ScalarSyntaxError) as caught:
        normalize_inline_upload(text.replace('`safe`', '`bad\nvalue`'))
    assert (caught.value.line, caught.value.column) == (6, 8)
