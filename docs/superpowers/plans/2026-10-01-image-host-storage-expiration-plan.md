# 图床自动存储、期限与安全诊断实现计划

> **面向 AI 代理的工作者：** 必需子技能：使用 subagent-driven-development（推荐）或 executing-plans 逐任务实现此计划。步骤使用复选框（`- [ ]`）跟踪进度；每个实现任务均采用 test-driven-development，提交前采用 verification-before-completion。不得把写完本计划视为已获实现授权。

**目标：** 为 BeeIMG.cn、Boltp增加自动存储发现、24小时持久化元数据缓存、无效手填ID自动恢复、组合 expiration 转 expired_at，以及仅CLI可见的安全业务诊断。

**架构：** 使用少量职责单一模块，保留四参数图床适配器和顺序故障转移入口。每次通知或CLI操作持有一个上传上下文，复用元数据而不缓存图片；每张图片拥有独立的时间起点和每图床项一次纠错预算。通知只消费固定摘要和告警，CLI显式启用脱敏诊断。

**技术栈：** Python 3.12、标准库 dataclasses/contextvars/datetime/hashlib/hmac/json/pathlib/tempfile、现有 requests、ruamel.yaml、pytest。不增加依赖，不改截图后端。

---

## 0. 权威来源、基线与执行约束

- 规格：`docs/superpowers/specs/2026-09-30-image-host-storage-expiration-design.md`，以2026-10-01复审修订为准。
- 当前分支：`Screenshot-Push`；进入本轮文档复审前HEAD为 `c5d4965`。实施时记录实际HEAD，核对后续提交仅为已经批准的文档，不硬编码旧HEAD作为执行基线。
- 用户提供的未跟踪文件 `1.png`、`BeeImgcn.md`、`BeeImgcn_api.md`、`boltp.md` 必须保留原样，不暂存、删除或改写。
- 不读取用户真实配置和令牌，不发送真实上传、账号查询、通知或截图。测试只允许本地替身及明确隔离的回环HTTP测试。
- 继续使用当前工作区，不创建worktree，不push，不改.gitignore、Git配置、版本号、发布工作流或依赖声明。文档阶段只修改规格和计划。
- 规格已经跟踪；计划位于忽略目录。新增计划是否提交须遵循本次文档授权；精确处理单个文件，不能把整个docs目录强制加入。
- 业务实现每个任务独立提交，英文type/scope、中文描述；不使用审查编号或“审查反馈”等字样。不使用 `git add .`、`git add -A`、amend或rebase。
- 所有Python验证使用 `.\.venv\Scripts\python.exe -B`；pytest加 `-p no:cacheprovider`。以下PowerShell命令在 `E:\GitHub\2RPM` 执行。
- 本计划中的代码块是需要实现或加入测试的精确契约和关键分支，不得把它们直接写入用户配置；其余既有代码保持不变。

### 执行前门禁

```powershell
git status --short
git diff --cached --stat
git log --oneline -5
.\.venv\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.\.venv\Scripts\python.exe -B -m pytest tests\test.py -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

记录实际计数；历史1508项与legacy 62项只作背景，不能充当本次结果。出现非预期改动先停止，不覆盖、清理或纳入。

## 1. 复审结论与固定决策

1. 匿名只查group；有令牌时group和profile均须成功。不读取浏览器Cookie，不在认证失败后降为匿名。
2. 手填ID合法且可用时优先；非法或不在列表时取得新元数据，再选账号默认ID，否则列表第一项；只修改本次内存副本，不写回配置。
3. 每站每图最多一次纠错刷新、一次上传重传。上传前替代无效ID不是一次上传，但消耗纠错预算；不得先发送明知无效的ID。
4. 两站上传后重传白名单均为：HTTP恰为200、JSON映射、字符串status恰为 `error`、字符串message恰为 `不存在的储存驱动`。BeeIMG.cn来自历史样本；Boltp是用户批准的兼容策略，未实测。不得承诺服务端一定未保存或绝无重复图片。
5. 每图共用绝对时间起点；不同图床按自己的expiration和组策略计算不同deadline。同一图床重传只能保持或缩短deadline。
6. 正数file_expire_seconds作为客户端保守上限，非已确认的服务端优先级；0不缩短且不保证永久保存。时间最终转本机本地时区，无偏移格式不保证与服务端解释一致。
7. CLI诊断过滤整条上传链中的秘密，不只过滤当前站令牌。通知不收集message；不新增用于诊断的HTTP请求。
8. 现有四站不增加元数据查询，不更改请求契约。合法expiration在不支持的站点上忽略并固定告警；非法expiration仍失败。

## 2. 文件职责与依赖方向

### 新增生产文件

| 文件 | 唯一主要职责 |
| --- | --- |
| `modules/image_host/diagnostics.py` | 业务message的保守脱敏、限长和当前调用诊断作用域 |
| `modules/image_host/expiration.py` | 组合时长解析、绝对deadline计算及本地时间格式化；不联网、不读配置 |
| `modules/image_host/v2_http.py` | 两站JSON请求、资源释放、固定分类及精确存储失效判定；不重试 |
| `modules/image_host/storage.py` | group/profile解析、最小StorageMetadata与存储选择；不落盘 |
| `modules/image_host/storage_cache.py` | 本地HMAC身份、24小时缓存、原子写入及失效；不联网 |
| `modules/image_host/context.py` | 单次事件元数据复用、失败复用、纠错刷新结果及诊断开关；不执行上传 |

不建立通用插件框架，不另造第二套注册表。core不导入新增模块；diagnostics使用标准库，expiration依赖core；v2_http依赖core和diagnostics；storage依赖core和v2_http；storage_cache依赖storage的数据模型；context依赖storage/storage_cache/diagnostics；registry依赖上述模块和providers。底层模块不得反向导入registry、pipeline或CLI，避免包初始化时形成循环导入。

### 修改生产文件

- `modules/image_host/core.py`：追加安全错误码、默认值字段；保留原有构造方式和URL/凭证校验。
- `modules/image_host/providers.py`：仅改两站共用v2上传实现；四参数包装函数保留；其他四站不改。
- `modules/image_host/registry.py`：上传上下文、顶层expiration、存储发现、一次恢复和安全结果汇总。
- `modules/image_host/__init__.py`：按需公开UploadContext，不重命名原导出。
- `modules/screenshot/pipeline.py`：同一批截图共用上下文、读取上传告警和固定失败摘要。
- `modules/screenshot/cli.py`：显式启用诊断，成功或失败均展示允许输出的诊断及告警。
- `modules/config.py`：注释与示例更新；不改变配置迁移、格式化器和默认图床列表。

### 新增测试文件

`tests/test_image_host_diagnostics.py`、`tests/test_image_host_expiration.py`、`tests/test_image_host_storage.py`、`tests/test_image_host_storage_cache.py`、`tests/test_image_host_storage_recovery.py`。

现有两站、核心、pipeline、CLI、通知及配置测试作局部调整，不删除原安全、释放、控制信号、URL保真断言。没有现成 `tests/conftest.py`；优先文件内fixture，不为本功能创建全局自动fixture影响无关测试。

## 3. 跨任务接口合同

### 3.1 数据结构与函数签名

```python
@dataclass(frozen=True)
class StorageMetadata:
    storage_ids: tuple[int, ...]
    default_storage_id: int | None
    file_expire_seconds: int | None
    fetched_at: float

@dataclass(frozen=True)
class StorageSelection:
    storage_id: int
    replaced_manual: bool

@dataclass(frozen=True)
class ExpirationDecision:
    deadline: float | None
    expired_at: str | None
    shortened: bool
```

- `parse_expiration(value) -> int`：仅处理“已提供”的值，返回秒数；未提供由registry以键存在性区分。
- `compute_expiration(seconds, started_at, retention_seconds, previous_deadline, now, formatter=None) -> ExpirationDecision`：seconds为None代表未配置。formatter只用于纯函数测试注入，生产默认本地格式化。
- `fetch_storage_metadata(provider, token, *, now) -> StorageMetadata`：两站固定端点；空令牌只group，非空令牌先group再profile，任一失败不缓存半份结果。
- `select_storage(metadata, manual_present, manual_value) -> StorageSelection`：纯函数；metadata已校验，手填不可用时按默认/首项替代。是否需要刷新由context/registry决定，不在纯函数里联网。
- `request_json(provider, stage, token, *, data=None, files=None) -> dict`：在v2_http.py定义；stage只允许group/profile/upload，内部决定固定路径与GET/POST方法，不接受任意URL；失败抛固定ImageHostError。
- `StorageCache(root=None, *, clock=time.time)`：惰性IO；`identity(provider, token) -> str | None`，失败返回None；`load(identity) -> StorageMetadata | None`，缺失、失效或读取失败返回None；`save(identity, metadata) -> bool`和`invalidate(identity) -> bool`返回操作是否成功。`take_warnings() -> tuple[str, ...]`返回并清空保序去重的固定告警，不输出路径或身份摘要。None身份不进行磁盘操作，context退回事件内模式。
- `UploadContext(*, diagnostics=False, cache=None, clock=time.time)`：事件级对象，构造不进行磁盘或网络IO；`get_metadata(provider, token, *, correction=False)`、`invalidate(provider, token)`、`collect_secrets(hosts)`、`diagnostic_scope()`。普通查询与纠错刷新分开记账。
- `upload_with_fallback(png_bytes, filename, hosts, *, context=None) -> UploadResult`：原三个位置参数不变；未传context时创建只覆盖此次调用的上下文。
- 四参数适配器 `upload_beeimg_cn(image_bytes, filename, token, options) -> str` 与Boltp对应函数保持；它们只做一次POST，不自行查询缓存或重传。

精确实现属性可使用私有名，但外部签名按上表；新增类型不得再在别的模块另起同义名称。

### 3.2 结果兼容

在core中追加默认值，保持既有三参数UploadFailure、五参数UploadResult构造有效：

```python
@dataclass(frozen=True)
class UploadFailure:
    provider: str
    code: str
    message: str
    stage: str = ''
    http_status: int | None = None
    diagnostic: str | None = field(default=None, repr=False)

@dataclass(frozen=True)
class UploadResult:
    success: bool
    provider: str | None
    url: str | None = field(repr=False)
    attempts: tuple[str, ...]
    failures: tuple[UploadFailure, ...]
    warnings: tuple[str, ...] = ()
```

ImageHostError保留 `(code, message)` 调用，第二参数继续忽略；新增仅关键字stage/http_status/diagnostic。只允许白名单stage、有效整数状态码、已经脱敏的诊断；异常args、str、repr仅固定中文摘要。不保存原始message、response或request。不可信适配器不能靠自定义message绕过registry重新映射。

新增码固定为 `config_error`、`storage_lookup_failed`、`storage_unavailable`、`transport_failed`、`http_failed`、`business_rejected`、`invalid_response`；保留所有旧码。stage白名单为 `group`、`profile`、`upload`、`storage`、`expiration`，否则空串。

## 4. 逐项实施任务

### 任务1：建立安全结果与诊断通道

**文件：** 修改core.py；创建diagnostics.py、test_image_host_diagnostics.py；局部扩展test_image_host_core.py。

- [ ] **步骤1：写失败测试。** 保留旧构造可用，验证消息不经异常直接传播，诊断从repr排除。新增以下纯函数测试：

```python
def test_redact_before_truncate_and_filter_other_host_secret():
    from modules.image_host.diagnostics import sanitize_message
    secret = 'FAKE_OTHER_HOST_TOKEN_7319'
    text = '上传失败 ' + '甲' * 190 + secret + ' https://example.test/a?sig=x'
    result = sanitize_message(text, (secret, '${OTHER_TOKEN}'))
    assert result is None or len(result) <= 200
    assert secret not in (result or '')
    assert 'FAKE_OTHER' not in (result or '')
    assert 'https://' not in (result or '')


def test_fixed_message_is_not_server_message():
    from modules.image_host.core import ImageHostError, UploadFailure
    error = ImageHostError('business_rejected', 'FAKE_RAW_SECRET',
                           stage='upload', diagnostic='请先绑定手机号')
    failure = UploadFailure('boltp', error.code, error.message,
                            error.stage, None, error.diagnostic)
    assert 'FAKE_RAW_SECRET' not in str(error)
    assert '请先绑定手机号' not in repr(error)
    assert '请先绑定手机号' not in repr(failure)
```

再参数化：非字符串、超过4096字符、完整URL、环境引用、ANSI/OSC转义、控制字符、HTML片段、`Authorization`/`Bearer`/token形态；可疑内容直接None。加入控制信号原对象传播测试，不捕BaseException。

- [ ] **步骤2：运行红灯。**

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_image_host_diagnostics.py tests\test_image_host_core.py -q -p no:cacheprovider
```

预期新接口缺失导致失败；若是测试拼写或导入路径错误，修测试再记录红灯。

- [ ] **步骤3：最小实现。** `sanitize_message(message, secrets)`：类型检查→4096字符上限→按长度降序替换非空秘密→移除URL/环境引用→检测剩余HTML或凭证形态并拒绝→清除终端控制序列及控制字符→截断200字符。为防控制字符把秘密拆开，清理后再替换一次秘密；不能声称覆盖任意编码泄露。字符筛选用unicodedata，不回显未经处理文本。

诊断作用域使用ContextVar，保证四参数适配器不靠全局可变对象传递上下文：

```python
from contextlib import contextmanager
from contextvars import ContextVar

_SCOPE = ContextVar('image_host_diagnostic_scope', default=None)

@contextmanager
def diagnostic_scope(secrets):
    marker = _SCOPE.set(tuple(secrets) if secrets is not None else None)
    try:
        yield
    finally:
        _SCOPE.reset(marker)


def safe_current_message(message):
    secrets = _SCOPE.get()
    if secrets is None:
        return None
    return sanitize_message(message, secrets)
```

作用域默认None，禁止默认收集。秘密容器不得出现在repr、日志、异常args中；不把原文延后到CLI才处理。

- [ ] **步骤4：运行绿灯并查看diff。** 上述测试通过；旧ImageHostError未知码、异常链、URL保真测试仍通过。
- [ ] **步骤5：精确暂存并提交。** `feat(upload): 增加安全错误分类与诊断通道`。

### 任务2：组合期限解析与纯函数换算

**文件：** 创建expiration.py、test_image_host_expiration.py。

- [ ] **步骤1：写失败测试。**

```python
import pytest

@pytest.mark.parametrize('value, expected', [
    ('7d', 604800), ('1d12h', 129600), ('2h30m', 9000),
    ('1w2d3h4m5s', 788645), (' 30m ', 1800),
])
def test_duration(value, expected):
    from modules.image_host.expiration import parse_expiration
    assert parse_expiration(value) == expected

@pytest.mark.parametrize('value', [None, True, 7, '', '0d', '-1d',
                                  '1.5h', '1h1d', '1d1d', '1d 2h', '7D'])
def test_bad_duration(value):
    from modules.image_host.core import ImageHostError
    from modules.image_host.expiration import parse_expiration
    with pytest.raises(ImageHostError) as exc:
        parse_expiration(value)
    assert exc.value.code == 'config_error'


def test_retry_never_extends_deadline():
    from modules.image_host.expiration import compute_expiration
    result = compute_expiration(604800, 1000, 7200, 4600, 1001,
                                formatter=lambda value: str(int(value)))
    assert result.deadline == 4600
    assert result.expired_at == '4600'
```

覆盖上限0/None、缩短、已过期、时间戳溢出、不同站不同期限、重传后更短上限、未配置期限。夏令时测试通过注入formatter观察绝对时间相差86400秒，不依赖Windows是否安装tzdata，不改变进程TZ。

- [ ] **步骤2：运行红灯。** `python.exe -B -m pytest tests\test_image_host_expiration.py -q -p no:cacheprovider`，使用项目虚拟环境完整路径。
- [ ] **步骤3：实现。** 解析器从头连续匹配 `[0-9]+[wdhms]`，ASCII数字，正整数、严格降序单位；仅strip首尾空白。累加秒数并限制在datetime可表示区间，转换失败映射config_error。

核心到期公式：

```python
from datetime import datetime


def local_deadline_text(deadline):
    return datetime.fromtimestamp(deadline).strftime('%Y-%m-%d %H:%M:%S')


def bounded_deadline(seconds, started_at, retention_seconds,
                     previous_deadline):
    duration = seconds
    if retention_seconds is not None and retention_seconds > 0:
        duration = min(duration, retention_seconds)
    deadline = started_at + duration
    if previous_deadline is not None:
        deadline = min(deadline, previous_deadline)
    return deadline
```

compute_expiration以该公式生成ExpirationDecision。发送格式精度为秒，因此先以math.floor将绝对deadline向下取整，再确认它严格大于now，最后格式化；不把无时区字符串重新解析回来比较，以免夏令时重叠造成歧义。每次POST前都重新检查期限，不能仅在元数据查询前检查。未配置seconds返回全空、不因组策略自行发送expired_at。捕获OverflowError/OSError/ValueError并在处理器外抛固定错误，避免敏感异常链。formatter注入只改变显示，不改变deadline校验。

- [ ] **步骤4：运行绿灯。** 精确比对字段、省略行为和绝对秒数，不只测正则。
- [ ] **步骤5：提交。** `feat(upload): 解析组合期限并计算本地到期时间`。

### 任务3：两站JSON传输与错误分类

**文件：** 创建v2_http.py；修改providers.py的两站共用辅助；修改两站测试文件，新建或扩展test_image_host_storage.py的HTTP部分。

- [ ] **步骤1：补请求和响应矩阵测试。** 将现有Response/Session替身扩展为按方法、URL返回独立响应；保留active/closed/read断言，增加GET而不放行真实requests。

```python
@pytest.mark.parametrize('provider', ['beeimg_cn', 'boltp'])
def test_exact_storage_error_classifier(provider):
    from modules.image_host.v2_http import is_storage_rejection
    payload = {'status': 'error', 'message': '不存在的储存驱动'}
    assert is_storage_rejection(provider, 200, payload)
    assert not is_storage_rejection(provider, 201, payload)
    assert not is_storage_rejection(provider, 200,
                                    dict(payload, message=' 不存在的储存驱动'))
    assert not is_storage_rejection('catbox', 200, payload)
```

覆盖两站成功、401/403/423/422/429/500/302、200业务error、未知status、状态非字符串、JSON类型错误、无URL、非法URL。超时/连接/解压读取错误为transport_failed；JSON语法错误为invalid_response。requests的JSONDecodeError需先于泛化RequestException分类。

- [ ] **步骤2：运行红灯。** 两站文件与core限定集；记录新增断言失败，不删除旧资源释放断言。
- [ ] **步骤3：实现request_json。** 固定站点基址和允许route：`/group`、`/user/profile`、`/upload`，方法分别GET/GET/POST。每次调用创建Session并嵌套Response上下文；timeout=(5,15)、verify=True、allow_redirects=False、stream=True；无HTTP适配器重试，不跟随重定向，不记录请求头和响应正文。

```python
def is_storage_rejection(provider, status_code, payload):
    return (
        provider in {'beeimg_cn', 'boltp'}
        and status_code == 200
        and isinstance(payload, dict)
        and type(payload.get('status')) is str
        and payload['status'] == 'error'
        and type(payload.get('message')) is str
        and payload['message'] == '不存在的储存驱动'
    )
```

仅upload阶段启用该判定。非2xx先http_failed；2xx合法error为business_rejected或精确storage_unavailable；合法success返回JSON映射；其他为invalid_response。业务message立即经safe_current_message处理，只有安全文本随错误返回。禁止保留响应对象及原始异常链。读取JSON设置解压后1MiB上限，超限invalid_response，使用iter_content累计，不先无限读取response.text；这是资源保护，不作为重试原因。

共用辅助继续校验permission/storage_id/album_id。在expiration.py定义跨模块使用的PreparedV2Options，保留四参数适配器签名，不引入用户可配置的内部标记键：

```python
class PreparedV2Options(dict):
    """携带编排层计算的期限，不修改用户参数映射。"""

    def __init__(self, options, *, expired_at=None):
        super().__init__(options)
        self.expired_at = expired_at
```

registry先将用户options深拷贝为普通dict，再构造PreparedV2Options；expired_at只从ExpirationDecision取得，不从用户同名属性读取。providers仅对该类型读取expired_at属性并加入表单，普通dict里的原生expired_at仍不发送。这个类型用于内部传值而不是安全授权边界，不宣称能防御任意调用方伪造Python对象。

另在providers.py提取 `validate_v2_options(options, *, require_storage=True)`：permission必须为非布尔0/1，拒绝原生is_public，album_id若存在必须为非布尔整数；require_storage=True时同时验证storage_id。该函数不联网、不记录告警。registry在查询前以require_storage=False调用，适配器在POST前以默认值调用，避免重复实现验证规则。未知选项告警仍在适配器现有位置产生。

- [ ] **步骤4：绿灯和兼容核对。** 直接调用四参数适配器仍为单次POST；registry集成元数据尚未在本任务接入。更新两站新增分类期望，其余四站旧码不变。
- [ ] **步骤5：提交。** `feat(upload): 细分两站传输与业务拒绝错误`。

### 任务4：存储元数据查询与选择

**文件：** 创建storage.py；完成test_image_host_storage.py。

- [ ] **步骤1：写纯解析及HTTP编排失败测试。**

```python
def test_manual_invalid_uses_default_without_mutating_value():
    from modules.image_host.storage import StorageMetadata, select_storage
    metadata = StorageMetadata((13, 14), 14, 0, 1000)
    manual = {'bad': 'value'}
    result = select_storage(metadata, True, manual)
    assert result.storage_id == 14
    assert result.replaced_manual is True
    assert manual == {'bad': 'value'}


def test_missing_manual_uses_first_when_default_absent():
    from modules.image_host.storage import StorageMetadata, select_storage
    result = select_storage(StorageMetadata((13, 14), None, None, 1000),
                            False, None)
    assert result.storage_id == 13
    assert result.replaced_manual is False
```

网络fixture记录匿名只GET group、有令牌group后profile；group成功profile失败不产出metadata；profile options非法失败，缺default/null正常，无效类型默认失败；保留列表顺序，空列表storage_unavailable，任一非法ID整份失败。保留期限字段缺失/null为None，0有效，bool/负数/非整数失败。

- [ ] **步骤2：运行红灯。** 新storage测试。
- [ ] **步骤3：实现。** 数据结构按第3节；`type(id) is int and id > 0` 排除bool。group须为有效对象，storages须为list，逐项映射；只留下ID，丢弃名称、intro、payments和账号细节。合法重复ID按原顺序去重。非空token才查询profile；不合并上一份profile，不把401视为未设置默认。

```python
def select_storage(metadata, manual_present, manual_value):
    if type(manual_value) is int and manual_value > 0:
        if manual_present and manual_value in metadata.storage_ids:
            return StorageSelection(manual_value, False)
    if not metadata.storage_ids:
        raise ImageHostError('storage_unavailable', '', stage='storage')
    selected = metadata.default_storage_id
    if selected not in metadata.storage_ids:
        selected = metadata.storage_ids[0]
    return StorageSelection(selected, manual_present)
```

传输错误保留固定code+group/profile阶段；成功HTTP但元数据形状错误采用storage_lookup_failed。不要以同一个通用码覆盖掉鉴权HTTP状态信息。

- [ ] **步骤4：绿灯。** 检查两个站点URL、Bearer隔离、group/profile每个响应独立关闭、控制信号原对象传播。
- [ ] **步骤5：提交。** `feat(upload): 查询账号默认存储与可用列表`。

### 任务5：24小时持久化缓存

**文件：** 创建storage_cache.py、test_image_host_storage_cache.py。

- [ ] **步骤1：写真实临时目录测试。**

```python
def test_disk_cache_has_no_token_and_expires(tmp_path):
    from modules.image_host.storage import StorageMetadata
    from modules.image_host.storage_cache import StorageCache
    clock = [1000.0]
    cache = StorageCache(tmp_path, clock=lambda: clock[0])
    token = 'FAKE_CACHE_SECRET_9381'
    identity = cache.identity('beeimg_cn', token)
    metadata = StorageMetadata((13,), 13, 3600, 1000.0)
    cache.save(identity, metadata)
    assert cache.load(identity) == metadata
    for path in tmp_path.rglob('*'):
        if path.is_file():
            assert token.encode() not in path.read_bytes()
    clock[0] += 86400
    assert cache.load(identity) is None
```

补身份隔离、跨实例复用、匿名分离、格式版本不符、未来时间、字段缺失、JSON损坏/超大文件、写入/替换失败、失效删除失败、损坏密钥、同时初始化密钥的确定性替身测试。不得访问实际LOCALAPPDATA。

- [ ] **步骤2：运行红灯。** 缓存测试仅tmp_path。
- [ ] **步骤3：实现。** Windows默认根为有效绝对 `%LOCALAPPDATA%/2RPM/image_host`；环境缺失或相对值时禁用磁盘缓存并固定告警，不回退仓库。构造无IO，首次需要v2元数据才初始化。32字节随机密钥位于该用户目录独立文件，排他创建、flush/fsync；既有密钥长度不符不得覆盖，回退事件内模式。不得把密钥或摘要打印到日志。

```python
def identity_digest(key, provider, token):
    import hashlib
    import hmac
    identity = provider.encode('ascii') + b'\0' + token.encode('utf-8')
    return hmac.new(key, identity, hashlib.sha256).hexdigest()
```

文件内容仅version/provider/fetched_at/storage_ids/default_storage_id/file_expire_seconds。JSON写同目录临时文件，flush/fsync后os.replace；失败只删除本次临时文件，不删除用户其他文件。缓存文件最大64KiB；读取校验时间、类型、provider和格式，年龄 `>=86400` 为过期。随机临时名并原子替换只保证完整性，不承诺多进程单次查询或强一致；测试不要求跨进程锁框架。

storage_cache模块维护进程内私有禁用集合，键为规范化缓存根目录与身份摘要，不含令牌；不同StorageCache实例也必须共享该失效保护。invalidate失败时登记禁用，后续load必miss；本轮不自动解除禁用。它只阻止读取不可靠缓存，不保存元数据或图片。失败告警由take_warnings进入context，不携带路径异常。缓存删除/保存失败绝不放出底层异常。刷新失败且旧缓存无法删时，该进程禁止再读；跨进程风险在验收记录中保留，不夸大为授权边界。补测试：销毁原context并在同进程新建context，仍不能读回已确认失效但删除失败的记录。

- [ ] **步骤4：绿灯。** 比较文件字节、目录残留、用户文件未删；不存在令牌或原响应。
- [ ] **步骤5：提交。** `feat(upload): 持久化隔离的存储元数据缓存`。

### 任务6：事件上下文与一次纠错预算

**文件：** 创建context.py、test_image_host_storage_recovery.py；按需导出UploadContext。

- [ ] **步骤1：编写事件复用和失败复用测试。** fake fetcher返回StorageMetadata或安全ImageHostError，fake cache仅在tmp_path。两张图片同站同token只取一次普通元数据，不同token不共享；下一事件允许重新读取有效磁盘缓存；查询失败在同事件复用、下一事件重新查询。

```python
def test_context_does_not_do_io_during_construction(monkeypatch):
    from modules.image_host.context import UploadContext
    from modules.image_host import context as module
    calls = []
    monkeypatch.setattr(module, 'fetch_storage_metadata',
                        lambda *a, **k: calls.append('network'))
    value = UploadContext(diagnostics=False)
    assert value is not None
    assert calls == []
```

- [ ] **步骤2：运行红灯。** recovery文件中的context用例。
- [ ] **步骤3：实现事件状态。** 内存身份按站点与事件随机HMAC摘要分离，原token不作为可打印字典key；磁盘身份另由StorageCache生成。metadata、查询失败、数据来源（disk/fresh）、是否已因同一无效手填配置纠错分开保存。错误缓存保存固定结果，不保留traceback或异常实例。每次调用upload_with_fallback创建独立image_state，保存started_at、每项预算和deadline；不把图片字节放进context。

状态表必须落实为明确分支：

| 初始状态 | 处理 | 本图纠错预算 |
| --- | --- | --- |
| 有效缓存+合法可用手填值 | 使用手填值 | 未用 |
| 有效旧缓存+非法/缺失于列表的手填值 | 强制查询一次，选新ID | 已用 |
| 常规查询刚得到新列表，手填值不可用 | 直接使用这份新结果替代，不再查第二次 | 已用 |
| 同事件已纠正相同手填值 | 复用纠正结果 | 已用 |
| 自动模式正常查询/缓存命中 | 选择默认或首项 | 未用 |
| 上传后精确拒绝且预算未用 | 先失效磁盘和事件记录，再查询 | 已用 |
| 任一刷新失败 | 保存安全失败供本事件复用 | 已用且失败 |

“相同手填值”不序列化任意对象、不记录原文；使用host列表位置和provider/凭证身份在事件中标记配置项。同凭证其他图床项共享元数据，但每图每项上传预算独立；共享新结果不能导致逐图重复纠错查询。

诊断开启时collect_secrets扫描host列表：仅处理映射内token，记录字面量/环境引用及可成功解析值；忽略解析错误直到对应站点按原顺序执行。事件内固定解析快照用于上传，防止过滤值与实际发送值不一致；通知也可按首次实际使用惰性解析，不提前触发环境错误。退出事件后释放秘密；repr不包含秘密集合。

- [ ] **步骤4：绿灯。** 验证普通读取、纠错刷新、失效失败的次数及隔离；无隐藏进程级图片缓存。
- [ ] **步骤5：提交。** `feat(upload): 增加事件级存储复用与恢复状态`。

### 任务7：注册表接入存储、期限与受限重传

**文件：** 修改registry.py；扩展recovery/expiration/core/两站测试。不得另建平行上传入口绕过UPLOADERS。

- [ ] **步骤1：补真实registry链路测试。** 使用真实UPLOADERS中的两站适配器，仅HTTP边界为替身，测试请求顺序 `group → profile → upload`；匿名 `group → upload`。缓存有效时只有upload。两站各测试精确拒绝后 `upload → group → profile → upload`，第二次失败转本地备用站，最多两次POST。

测试场景与精确期望：

| 输入/响应 | GET行为 | POST行为 |
| --- | --- | --- |
| 非法expiration | 0 | 0，转备用站 |
| 旧缓存+手填字符串ID | 一次完整新查询 | 新ID一次POST |
| 手填13，新列表只有14 | 一次新查询 | 14一次POST，配置仍13 |
| 首次自动上传精确存储错误 | 一次纠错查询 | 总共最多2次POST |
| 上传前已纠正，首次POST仍存储错误 | 不再刷新 | 本站仅1次POST |
| 超时/绑定/审核/限流/近似文案 | 不做纠错GET | 本站仅1次POST |
| 刷新失败 | 不读旧缓存 | 不发第二次POST |
| 前站失败、后站成功 | 顺序执行 | 保留前站安全failure |

- [ ] **步骤2：运行红灯。** recovery和两站测试，必要调整HTTP假对象支持GET；不能靠mock整个新流程让红灯消失。
- [ ] **步骤3：实现注册表编排。** 签名增加仅关键字context；保留原输入校验、provider标准化、深拷贝及尝试顺序。每图started_at只记一次。先解析顶层expiration/冲突和v2普通选项，再查询元数据；storage_id异常是恢复分支，不提前invalid_options。原生options.expired_at没有顶层expiration时仍忽略并固定告警。

```python
# options已经深拷贝；metadata与correction_used由任务6的状态表取得。
manual_present = 'storage_id' in options
manual_value = options.get('storage_id')
selection = select_storage(metadata, manual_present, manual_value)
prepared_options = PreparedV2Options(options)
prepared_options['storage_id'] = selection.storage_id
previous_deadline = None
for upload_number in range(2):
    decision = compute_expiration(
        seconds, started_at, metadata.file_expire_seconds,
        previous_deadline, context.clock())
    previous_deadline = decision.deadline
    prepared_options.expired_at = decision.expired_at
    try:
        candidate = UPLOADERS[provider](
            png_bytes, filename, token, prepared_options)
        url = validate_image_url(candidate)
        break
    except ImageHostError as error:
        can_refresh = (
            provider in {'beeimg_cn', 'boltp'}
            and error.code == 'storage_unavailable'
            and error.stage == 'upload'
            and not correction_used
            and upload_number == 0
        )
        if not can_refresh:
            raise
        correction_used = True
        context.invalidate(provider, token)
        metadata = context.get_metadata(provider, token, correction=True)
        selection = select_storage(metadata, manual_present, manual_value)
        prepared_options['storage_id'] = selection.storage_id
        # 下一轮POST前重新核对时间，并以previous_deadline限制不得延长。
        # 元数据查询失败直接传播安全错误，不进入下一轮POST。
```

以上分支之外要收集固定替代/期限/缓存告警并保序去重。内部请求使用用户options的副本；不得直接改host。其他四站仍四参数调用，不查询元数据；合法expiration仅告警。每个配置项只在attempts记一次，内部重传不伪造成新的配置项。

单站内部第一次可恢复拒绝在CLI诊断开启时也保留安全记录；恢复成功时success=True且failures可以含该次失败，CLI据此说明前次失败已恢复，不将其当最终失败。通知仅根据最终结果和固定告警显示，不能把诊断漏入正文。

- [ ] **步骤4：绿灯。** 运行全部image_host测试；更新两站“每项只POST一次”的旧断言为按新流程准确GET/POST次数，保留非法其他参数零网络、资源释放、秘密不泄露等断言。模拟测试明确Boltp白名单来自用户批准策略。
- [ ] **步骤5：提交。** `feat(upload): 接入自动存储期限与受限恢复`。

### 任务8：通知与CLI复用上下文并展示安全结果

**文件：** 修改pipeline.py、cli.py；扩展test_screenshot_pipeline.py、test_notification_screenshot.py、test_screenshot_cli_upload.py。

- [ ] **步骤1：写集成失败测试。** 两张图、三通知通道及重试：capture次数等于2；同身份metadata只查一份；上传只按各图及允许重传计数。通知路径diagnostic始终None。CLI前站业务拒绝后备用站成功仍输出经处理提示，未带--upload时不构造磁盘缓存、不读配置、不查询存储。

用现有fixture调用真实send_notification或run_screenshot_cli，断言：

```python
assert capture.call_count == 2
assert group_get.call_count == 1
assert profile_get.call_count == 1
assert 'FAKE_RAW_SECRET' not in caplog.text
assert '请先绑定手机号' not in notification_body
assert '指定存储无效，已自动选择可用存储' in notification_body
```

上述变量在各测试中由既有capture/notifier/HTTP替身返回值赋值，不能新增会发送真实网络的fixture。对应CLI用capsys检查安全message可见、秘密/URL/完整响应不可见；上传成功业务URL按既有功能仍显示，区分成功结果与错误诊断脱敏。

- [ ] **步骤2：运行红灯。** pipeline、notification_screenshot、notification、external_action_monitor_integration、CLI上传五文件限定集。
- [ ] **步骤3：接入。** prepare_screenshots禁用返回之后、循环之前创建一个UploadContext(diagnostics=False)，传给_prepare_target；后者给upload_with_fallback传context，warnings.extend(uploaded.warnings)。将固定failure.code映射到安全中文上传失败类别，不能直接插入diagnostic或不可信message。保留ScreenshotBatch三个字段与append_screenshot_notices签名。

CLI仅在_upload_debug_image加载并筛选hosts成功后创建UploadContext(diagnostics=True)，调用同一个upload_with_fallback。先输出warnings及允许展示的failure固定摘要/diagnostic，再处理最终success。失败返回1，成功输出URL/Markdown返回0；不额外发送探测请求，不删除本地图片。

```python
context = UploadContext(diagnostics=True)
uploaded = upload_with_fallback(png_bytes, filename, hosts, context=context)
for warning in uploaded.warnings:
    print(f'提示：{warning}')
for failure in uploaded.failures:
    print(f'图床 {failure.provider}：{failure.message}')
    if failure.diagnostic:
        print(f'服务端提示：{failure.diagnostic}')
```

显示阶段和状态码时只使用core校验后的值。现有测试fake uploader需接受新context关键字，不能以删掉次数断言来兼容。

- [ ] **步骤4：绿灯。** 核查控制信号、冲突改名补附、告警只集中记录一次、URL日志保护、多通道顺序及原通知返回格式。
- [ ] **步骤5：提交。** `feat(notification): 复用上传上下文并接入安全告警`；CLI如独立实现，可另提交 `feat(cli): 显示脱敏的图床业务诊断`，每次均限定回归。

### 任务9：配置注释与往返兼容

**文件：** 修改config.py；扩展test_screenshot_config.py、test_config_layout_migration.py。

- [ ] **步骤1：写失败断言。** 新注释说明自动选择、手填失效自动替代但不写回、expiration组合时长、本地时区、服务端不保证实际删除；真实YAML临时文件往返后expiration仍为原字符串，token引用保持字面量、storage_id非法值未被配置清理器删掉。

```python
def test_expiration_survives_screenshot_formatting():
    from copy import deepcopy
    from modules.config import correct_screenshot_config
    value = {'push': {'screenshot': {'targets': [], 'image_host': [
        {'provider': 'beeimg_cn', 'token': '${FAKE_TOKEN}',
         'expiration': '1d12h', 'options': {'storage_id': 'wrong'}}
    ]}}}
    original = deepcopy(value)
    correct_screenshot_config(value)
    assert value == original
```

补序列/流式风格断言，不能只比较普通dict忽略既有格式要求。

- [ ] **步骤2：运行红灯。** 配置与布局迁移两文件。
- [ ] **步骤3：只改注释与示例。** 移除“必须手填storage_id”，保留显式覆盖说明，不写死账号的13/2/4；示例使用 `${BEEIMG_CN_TOKEN}` 与 `expiration: 7d`。默认catbox列表、capture_screenshot默认值、迁移和清理器不变。若往返测试暴露顶层expiration被过滤，定位最窄的字段保留路径后再修改，不能全局关闭清理。
- [ ] **步骤4：绿灯。** 确认真实用户配置未读取或改写。
- [ ] **步骤5：提交。** `docs(config): 说明自动存储与组合过期时间`。

### 任务10：全量验收与执行交接

**文件：** 只更新计划复选框与必要的脱敏验证记录，不生成含真实响应/账号信息的夹具。

- [ ] **步骤1：范围及秘密检查。** 对比实施基线到HEAD，确认没有新图床、SM.MS、依赖、截图后端或打包变更；未跟踪的四个用户文件仍原样。搜索新模块中的日志/print/异常插值，逐条确认无token、cache identity、HTTP原文或图片字节。
- [ ] **步骤2：运行限定集。**

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests\test_image_host_diagnostics.py tests\test_image_host_expiration.py tests\test_image_host_storage.py tests\test_image_host_storage_cache.py tests\test_image_host_storage_recovery.py tests\test_image_host_core.py tests\test_image_host_beeimg_cn.py tests\test_image_host_boltp.py tests\test_screenshot_pipeline.py tests\test_screenshot_cli_upload.py tests\test_notification_screenshot.py tests\test_screenshot_config.py -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
```

- [ ] **步骤3：全量与依赖验证。**

```powershell
.\.venv\Scripts\python.exe -B -m pytest tests -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.\.venv\Scripts\python.exe -B -m pytest tests\test.py -q -p no:cacheprovider
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
.\.venv\Scripts\python.exe -B -m pip check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
git diff --check
git status --short
```

- [ ] **步骤4：独立规格与质量核验。** 子代理驱动时，每任务先规格后质量，较小纯文案可合并；最终核验请求次数、刷新预算、持久化失效、跨账号隔离、CLI对备用站秘密过滤和通知默认不收集原文。只读审查不能借机真实上传。
- [ ] **步骤5：提交和交付。** 报实际命令/退出码/计数及提交SHA。区分“实现通过合成测试”“历史人工上传事实”“未验证服务端到期与Boltp上传后重传”。未授权不得自动发起线上验证或改用户账号。

## 5. 每任务提交模板

每项先看git diff，精确暂存该任务文件，再检查暂存区只包含预期文件。PowerShell使用here-string，不使用Bash heredoc；任何失败立即停止后续commit：

```powershell
git diff --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
git diff --cached --check
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
git diff --cached --stat
```

提交消息采用各任务给出的标题；正文说明行为与边界，不写令牌、私有URL或原始服务端响应。业务任务不强制暂存整个文档目录。

## 6. 规格覆盖矩阵

| 规格要求 | 任务 |
| --- | --- |
| 四参数适配器、原调用兼容 | 1、3、7、8 |
| 账号默认/匿名首项/手动覆盖及自动替代 | 4、6、7 |
| 无效手填先查新列表、不改配置 | 6、7、9 |
| 身份隔离、24小时、原子写入、失效后不用旧缓存 | 5、6、7 |
| 事件复用、失败复用、每图预算、精确两站白名单 | 3、6、7、8 |
| 组合时长、统一起点、各站独立上限、重传不延长 | 2、7 |
| 不支持期限的站点告警、原生字段冲突 | 7、9 |
| 固定类别、阶段、HTTP码 | 1、3、4、7 |
| CLI独立诊断、整链秘密过滤、repr与日志隔离 | 1、6、7、8 |
| 多目标/多通道不重复截图、通知告警补附 | 8 |
| 配置往返和未跟踪用户文件不变 | 9、10 |
| 不联网、不用真实令牌、控制信号/资源释放 | 每任务及10 |

## 7. 计划自检与批准门槛

- 对照规格逐条确认任务覆盖，无未定义外部模块或平行上传流程。
- 核对签名：所有任务均使用StorageMetadata、StorageSelection、ExpirationDecision、UploadContext和仅关键字context；不得出现另一套同义类型。
- 检查本计划代码块中的常量、数字和期望；首项选择保持接口顺序，不隐式将字符串默认ID转整数。
- 有关未知服务端时区、期限优先级、Bearer-only账号profile成功、Boltp精确重传样本的限制必须保留，不能用测试替身声称线上事实。
- 本计划获用户确认并选择执行方式后，才开始任务1。当前阶段不实现、不跑线上请求、不自动安装依赖。
