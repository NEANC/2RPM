#!/usr/bin/env python3
# -_- coding: utf-8 -_-

"""通知字段元数据与无副作用的模拟预览。"""

from dataclasses import dataclass

from onepush import get_notifier
from onepush.exceptions import NoSuchNotifierError

from modules.notification import (_CHANNEL_AUTH_FIELDS,
                                  _collect_template_fields,
                                  _configured_screenshot_names,
                                  _is_placeholder_root,
                                  validate_template_fields)
from modules.screenshot.targets import allocate_targets
from modules.utils import KNOWN_PROVIDERS, parse_push_channels


_BASE_VALUES = {'host_name': '模拟主机', 'current_time': '2000/01/01 12:00:00',
                'short_current_time': '12:00:00'}
_EVENT_VALUES = {
    'on_end': {'process_name': '模拟程序.exe', 'process_pid': 1234,
               'process_run_time': '1m'},
    'on_timeout': {'process_name': '模拟程序.exe', 'process_pid': 1234,
                   'process_run_time': '1m'},
    'on_wait_timeout': {'process_name': '模拟程序.exe', 'process_wait_time': '30s'},
    'on_external': {'process_name': '模拟程序.exe', 'process_pid': 1234,
                    'external_program_name': '模拟程序.exe',
                    'external_program_path': 'C:\\模拟\\程序.exe'},
}
_LABELS = {'password': '密码', 'host': '服务器', 'user': '用户',
           'token': '令牌', 'sckey': '推送密钥'}


@dataclass(frozen=True)
class ProviderField:
    """已安装通道参数的最小表单描述。"""

    name: str
    required: bool
    secret: bool
    label: str


def provider_fields(provider: str) -> tuple[ProviderField, ...]:
    """从已安装 OnePush 定义获取字段，未知通道不推测参数。"""
    try:
        params = get_notifier(provider).params
    except NoSuchNotifierError:
        return ()
    return tuple(ProviderField(name, required,
                               name.lower().replace('_', '').replace('-', '')
                               in _CHANNEL_AUTH_FIELDS,
                               _LABELS.get(name, name))
                 for required, names in ((True, params['required']),
                                         (False, params['optional']))
                 for name in names if name not in ('title', 'content', 'msg', 'subject'))


def event_variables(template_key: str) -> dict[str, object]:
    """为选定事件提供固定的模拟值，不读取本机业务信息。"""
    if template_key not in _EVENT_VALUES:
        raise ValueError('未知通知事件')
    return {**_BASE_VALUES, **_EVENT_VALUES[template_key]}


def _template_context(config: dict, template_key: str):
    """按发送流程的分配规则得到模板与截图输出名。"""
    push = config.get('push', {})
    template = push.get('templates', {}).get(template_key, {})
    values = event_variables(template_key)
    names = _configured_screenshot_names(push.get('screenshot', {}), set(values))
    return push, template, values, names


def preview_template(config: dict, template_key: str) -> tuple[str, str]:
    """验证并渲染带模拟标记的标题及正文，不执行任何发送。"""
    _, template, values, names = _template_context(config, template_key)
    title, content = template.get('title', ''), template.get('content', '')
    if validate_template_fields(title, content, values, names):
        raise ValueError('通知模板变量或格式无效')
    fields = _collect_template_fields(values, names, title, content)
    values.update({name: '[模拟截图]' for name in fields
                   if name not in values and _is_placeholder_root(name, names)})
    try:
        return '[模拟] ' + title.format(**values), '[模拟] ' + content.format(**values)
    except (ValueError, TypeError):
        raise ValueError('通知模板变量或格式无效') from None


def screenshot_conditions(config: dict, template_key: str) -> dict[str, bool]:
    """分别说明截图事件开关、通道、有效引用和目标状态。"""
    push, template, values, names = _template_context(config, template_key)
    title, content = template.get('title', ''), template.get('content', '')
    fields = _collect_template_fields(values, names, title, content)
    section = push.get('screenshot', {})
    targets, _ = allocate_targets(section.get('targets'), set(values))
    return {
        'enabled': template.get('enable', True) is True and
                   template.get('capture_screenshot', True) is True,
        'channels': any(
            channel.get('provider') in KNOWN_PROVIDERS
            for channel in parse_push_channels(
                push.get('push_channel_settings', {}).get('channels'))),
        'references': fields is not None and not validate_template_fields(
            title, content, values, names) and any(
                name == 'screenshot' or name in names
                for name in fields if name not in values),
        'targets': any(target['error'] is None for target in targets),
    }
