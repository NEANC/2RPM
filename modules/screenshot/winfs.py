#!/usr/bin/env python3
# -_- coding: utf-8 -_-
"""Windows 句柄锚定的安全文件 I/O。"""

import ctypes
from ctypes import wintypes
from contextlib import ExitStack
from contextlib import contextmanager
import ntpath
import sys


class UNICODE_STRING(ctypes.Structure):
    """NT UTF-16 名称结构。"""

    _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT),
                ('Buffer', wintypes.LPWSTR)]


class OBJECT_ATTRIBUTES(ctypes.Structure):
    """带父目录句柄的相对对象属性。"""

    _fields_ = [('Length', wintypes.ULONG), ('RootDirectory', wintypes.HANDLE),
                ('ObjectName', ctypes.POINTER(UNICODE_STRING)),
                ('Attributes', wintypes.ULONG), ('SecurityDescriptor', ctypes.c_void_p),
                ('SecurityQualityOfService', ctypes.c_void_p)]


class IO_STATUS_BLOCK(ctypes.Structure):
    """NT I/O 状态块。"""

    _fields_ = [('StatusOrPointer', ctypes.c_void_p), ('Information', ctypes.c_size_t)]


class FILE_DISPOSITION_INFO(ctypes.Structure):
    """文件句柄删除标记。"""

    _fields_ = [('DeleteFile', wintypes.BOOLEAN)]


class BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
    """Windows 文件身份与属性信息。"""

    _fields_ = [('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
                ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
                ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                ('id_high', wintypes.DWORD), ('id_low', wintypes.DWORD)]


class FILE_ID_BOTH_DIR_INFO(ctypes.Structure):
    """FILE_ID_BOTH_DIR_INFO 变长目录记录头。"""

    _fields_ = [('NextEntryOffset', wintypes.DWORD), ('FileIndex', wintypes.DWORD),
                ('CreationTime', ctypes.c_longlong), ('LastAccessTime', ctypes.c_longlong),
                ('LastWriteTime', ctypes.c_longlong), ('ChangeTime', ctypes.c_longlong),
                ('EndOfFile', ctypes.c_longlong), ('AllocationSize', ctypes.c_longlong),
                ('FileAttributes', wintypes.DWORD), ('FileNameLength', wintypes.DWORD),
                ('EaSize', wintypes.DWORD), ('ShortNameLength', ctypes.c_byte),
                ('ShortName', wintypes.WCHAR * 12), ('FileId', ctypes.c_longlong),
                ('FileName', wintypes.WCHAR * 1)]


class UnsafeObjectError(OSError):
    """无法证明对象在固定的无重解析边界内。"""


_K32 = ctypes.WinDLL('kernel32', use_last_error=True)
_NT = ctypes.WinDLL('ntdll')
_HANDLE = wintypes.HANDLE
_DWORD = wintypes.DWORD
_PTR = ctypes.c_void_p
_INVALID = ctypes.c_void_p(-1).value
_REPARSE = 0x400
_DIRECTORY = 0x10
_READ_ATTRIBUTES = 0x80
_SYNCHRONIZE = 0x100000
_DELETE = 0x10000


def _bind(dll, name, result, args):
    """为系统调用设置明确的 ABI。"""
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = args
    return function


_CreateFile = _bind(_K32, 'CreateFileW', _HANDLE,
                    [wintypes.LPCWSTR, _DWORD, _DWORD, _PTR, _DWORD, _DWORD, _HANDLE])
_Close = _bind(_K32, 'CloseHandle', wintypes.BOOL, [_HANDLE])
_Info = _bind(_K32, 'GetFileInformationByHandle', wintypes.BOOL,
              [_HANDLE, ctypes.POINTER(BY_HANDLE_FILE_INFORMATION)])
_Enum = _bind(_K32, 'GetFileInformationByHandleEx', wintypes.BOOL,
              [_HANDLE, ctypes.c_int, _PTR, _DWORD])
_Set = _bind(_K32, 'SetFileInformationByHandle', wintypes.BOOL,
             [_HANDLE, ctypes.c_int, _PTR, _DWORD])
_Write = _bind(_K32, 'WriteFile', wintypes.BOOL,
               [_HANDLE, _PTR, _DWORD, ctypes.POINTER(_DWORD), _PTR])
_NtCreate = _bind(_NT, 'NtCreateFile', ctypes.c_long,
                  [ctypes.POINTER(_HANDLE), _DWORD, ctypes.POINTER(OBJECT_ATTRIBUTES),
                   ctypes.POINTER(IO_STATUS_BLOCK), ctypes.POINTER(ctypes.c_longlong),
                   _DWORD, _DWORD, _DWORD, _DWORD, _PTR, _DWORD])
_DosError = _bind(_NT, 'RtlNtStatusToDosError', wintypes.ULONG, [ctypes.c_long])


def _raise_os(code):
    """将 Win32 错误转换为稳定的 Python 文件异常。"""
    if code in (80, 183):
        raise FileExistsError(17, '目标已存在')
    if code in (2, 3):
        raise FileNotFoundError(2, '对象已消失')
    raise ctypes.WinError(code)


class _Handle:
    """独占并幂等释放一个内核句柄。"""

    def __init__(self, value):
        """保存成功打开的句柄值。"""
        self.value = value

    def close(self):
        """关闭句柄一次，关闭失败时报告错误。"""
        if self.value is not None:
            value, self.value = self.value, None
            if not _Close(value):
                _raise_os(ctypes.get_last_error())


def _close_owned(handle):
    """清理句柄时不覆盖正在传播的主异常。"""
    primary = sys.exception()
    try:
        handle.close()
    except Exception:
        if primary is None:
            raise
        primary.add_note('截图句柄释放失败')


def _identity(handle):
    """获取对象身份并拒绝重解析点。"""
    info = BY_HANDLE_FILE_INFORMATION()
    if not _Info(handle.value, ctypes.byref(info)):
        _raise_os(ctypes.get_last_error())
    if info.attributes & _REPARSE:
        raise UnsafeObjectError('拒绝重解析对象')
    return (info.volume, (info.id_high << 32) | info.id_low,
            bool(info.attributes & _DIRECTORY))


def _component(name):
    """检查相对路径必须是单个普通 Windows 名称。"""
    if (not name or name in ('.', '..') or name[-1:] in (' ', '.')
            or any(char in name for char in '\\/:\x00')
            or len(name.encode('utf-16-le')) > 510):
        raise UnsafeObjectError('无效路径组件')
    return name


def _open_child(parent, name, directory, create=False, delete=False):
    """通过固定父目录句柄相对打开或创建一个子项。"""
    name = _component(name)
    buffer = ctypes.create_unicode_buffer(name)
    length = len(name.encode('utf-16-le'))
    text = UNICODE_STRING(length, length + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attrs = OBJECT_ATTRIBUTES(ctypes.sizeof(OBJECT_ATTRIBUTES), parent.value,
                              ctypes.pointer(text), 0x40, None, None)
    status_block = IO_STATUS_BLOCK()
    handle = _HANDLE()
    access = _READ_ATTRIBUTES | _SYNCHRONIZE
    access |= 1 if directory else (2 if create else 0)
    if delete:
        access |= _DELETE
    disposition = (3 if directory else 2) if create else 1
    options = (1 if directory else 0x40) | 0x00200000 | 0x20
    status = _NtCreate(ctypes.byref(handle), access, ctypes.byref(attrs),
                       ctypes.byref(status_block), None, 0, 1, disposition,
                       options, None, 0)
    if status < 0:
        _raise_os(_DosError(status))
    owned = _Handle(handle.value)
    try:
        if _identity(owned)[2] != directory:
            raise UnsafeObjectError('对象类型改变')
    except BaseException as error:
        if create and not directory:
            try:
                _mark_delete(owned)
            except Exception:
                error.add_note('截图半成品清理失败')
        _close_owned(owned)
        raise
    return owned


def _parts(path):
    """拆解普通绝对路径，拒绝设备命名空间与点组件。"""
    if not ntpath.isabs(path) or path.startswith(('\\\\?\\', '\\\\.\\')):
        raise UnsafeObjectError('需要普通绝对路径')
    drive, tail = ntpath.splitdrive(path.replace('/', '\\'))
    if not drive or not tail.startswith('\\'):
        raise UnsafeObjectError('路径根无效')
    names = [_component(name) for name in tail.split('\\') if name]
    return drive + '\\', names


@contextmanager
def _open_path(path, create_dirs=False, delete_leaf=False):
    """逐级固定祖先目录句柄并保持至操作完成。"""
    root, names = _parts(path)
    if delete_leaf and not names:
        raise UnsafeObjectError('禁止删除根')
    with ExitStack() as stack:
        value = _CreateFile(root, 1 | _READ_ATTRIBUTES | _SYNCHRONIZE,
                            1, None, 3, 0x02000000 | 0x00200000, None)
        if value == _INVALID:
            _raise_os(ctypes.get_last_error())
        current = _Handle(value)
        stack.callback(_close_owned, current)
        if not _identity(current)[2]:
            raise UnsafeObjectError('根不是目录')
        for index, name in enumerate(names):
            current = _open_child(current, name, True, create_dirs,
                                  delete_leaf and index == len(names) - 1)
            stack.callback(_close_owned, current)
        yield current


def _entries(handle):
    """从固定目錄句柄解码目录项名称和对象标识。"""
    first = True
    capacity = 65536
    name_offset = FILE_ID_BOTH_DIR_INFO.FileName.offset
    while True:
        buffer = ctypes.create_string_buffer(capacity)
        if not _Enum(handle.value, 11 if first else 10, buffer, capacity):
            code = ctypes.get_last_error()
            if code == 18:
                return
            _raise_os(code)
        first = False
        offset = 0
        while True:
            if offset + ctypes.sizeof(FILE_ID_BOTH_DIR_INFO) > capacity:
                raise UnsafeObjectError('目录记录越界')
            record = FILE_ID_BOTH_DIR_INFO.from_buffer(buffer, offset)
            size = record.FileNameLength
            step = record.NextEntryOffset
            limit = offset + step if step else capacity
            if (size % 2 or limit > capacity or offset + name_offset + size > limit
                    or (step and (step % 8 or step < name_offset))):
                raise UnsafeObjectError('目录记录无效')
            name = ctypes.string_at(ctypes.addressof(buffer) + offset + name_offset,
                                    size).decode('utf-16-le')
            if name not in ('.', '..'):
                yield (_component(name), record.FileAttributes,
                       record.FileId & 0xffffffffffffffff)
            if not step:
                break
            offset += step


def _mark_delete(handle):
    """通过文件句柄标记其打开的目录项删除。"""
    info = FILE_DISPOSITION_INFO(True)
    if not _Set(handle.value, 4, ctypes.byref(info), ctypes.sizeof(info)):
        _raise_os(ctypes.get_last_error())


def _write(handle, data):
    """循环处理 WriteFile 部分写入并拒绝零进展。"""
    offset = 0
    while offset < len(data):
        block = data[offset:offset + 1024 * 1024]
        buffer = ctypes.create_string_buffer(block)
        written = _DWORD()
        if not _Write(handle.value, buffer, len(block), ctypes.byref(written), None):
            _raise_os(ctypes.get_last_error())
        if not 0 < written.value <= len(block):
            raise OSError('写入无进展')
        offset += written.value


def write_exclusive(path, data):
    """相对父句柄排他创建并写入，失败时按原句柄清理半成品。"""
    parent, name = ntpath.split(path)
    with _open_path(parent, create_dirs=True) as directory:
        with ExitStack() as stack:
            handle = _open_child(directory, name, False, True, True)
            stack.callback(_close_owned, handle)
            identity = None
            try:
                identity = _identity(handle)
                _write(handle, data)
            except BaseException as error:
                try:
                    if identity is None or _identity(handle) != identity:
                        raise UnsafeObjectError('半成品身份无法确认')
                    _mark_delete(handle)
                except Exception:
                    error.add_note('截图半成品清理失败')
                raise


def list_names(path):
    """列出固定目录句柄中的直接子项名称。"""
    with _open_path(path) as directory:
        return tuple(name for name, attributes, file_id in _entries(directory))


@contextmanager
def directory_entries(path):
    """保持枚举根及祖先句柄存活，并携带候选对象身份。"""
    with _open_path(path) as directory:
        volume = _identity(directory)[0]
        entries = tuple((name, attributes, (volume, file_id,
                                            bool(attributes & _DIRECTORY)))
                        for name, attributes, file_id in _entries(directory))
        yield directory, entries


@contextmanager
def _open_tree(path, parent, expected):
    """相对原枚举根打开候选并在扫描前核对身份。"""
    if parent is None:
        with _open_path(path, delete_leaf=True) as root:
            yield root
        return
    with ExitStack() as stack:
        root = _open_child(parent, path, True, delete=True)
        stack.callback(_close_owned, root)
        if _identity(root) != expected:
            raise UnsafeObjectError('日期目录身份改变')
        yield root


def remove_tree(path, *, parent=None, expected=None):
    """固定整树对象后按句柄身份校验并后序删除。"""
    with _open_tree(path, parent, expected) as root:
        with ExitStack() as stack:
            pending = [(root, _identity(root))]
            preorder = []
            while pending:
                handle, identity = pending.pop()
                preorder.append((handle, identity))
                if not identity[2]:
                    continue
                for name, attributes, file_id in _entries(handle):
                    if attributes & _REPARSE:
                        raise UnsafeObjectError('整树含重解析对象')
                    child_directory = bool(attributes & _DIRECTORY)
                    try:
                        child = _open_child(handle, name, child_directory, delete=True)
                    except FileNotFoundError:
                        continue
                    stack.callback(_close_owned, child)
                    child_id = _identity(child)
                    expected = (identity[0], file_id, child_directory)
                    if child_id != expected or _identity(handle) != identity:
                        raise UnsafeObjectError('扫描对象身份改变')
                    pending.append((child, child_id))
            for handle, identity in reversed(preorder):
                if _identity(handle) != identity:
                    raise UnsafeObjectError('删除对象身份改变')
                _mark_delete(handle)
                handle.close()
