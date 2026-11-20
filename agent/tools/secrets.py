"""把模型 API Key 存进 Windows 凭据管理器（Generic Credential），不落到任何文件里。

写法和电脑助手分支 agent/assistant/storage.py 的 Credentials 一样，这里只留读、写、删三个函数。
"""
import ctypes
from ctypes import wintypes as w

TARGET = 'edge-agent/model'     # 凭据管理器里显示的名字
NOT_FOUND = 1168


class _Credential(ctypes.Structure):
    _fields_ = [('Flags', w.DWORD), ('Type', w.DWORD), ('TargetName', w.LPWSTR),
                ('Comment', w.LPWSTR), ('LastWritten', w.FILETIME),
                ('CredentialBlobSize', w.DWORD), ('CredentialBlob', ctypes.POINTER(ctypes.c_ubyte)),
                ('Persist', w.DWORD), ('AttributeCount', w.DWORD), ('Attributes', ctypes.c_void_p),
                ('TargetAlias', w.LPWSTR), ('UserName', w.LPWSTR)]


def _api():
    api = ctypes.WinDLL('advapi32', use_last_error=True)
    api.CredWriteW.argtypes = [ctypes.POINTER(_Credential), w.DWORD]
    api.CredReadW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, ctypes.POINTER(ctypes.POINTER(_Credential))]
    api.CredDeleteW.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD]
    api.CredFree.argtypes = [ctypes.c_void_p]
    return api


def put_key(secret: str, target: str = TARGET) -> None:
    """保存密钥。输入空字符串等于删除。"""
    if not secret:
        delete_key(target)
        return
    raw = secret.encode('utf-8')
    if len(raw) > 2500:      # 凭据管理器单条上限 2560 字节
        raise ValueError('密钥长度异常')
    blob = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    cred = _Credential(Type=1, TargetName=target, CredentialBlobSize=len(raw), CredentialBlob=blob,
                       Persist=2, UserName='edge-agent')
    api = _api()
    if not api.CredWriteW(ctypes.byref(cred), 0):
        raise ctypes.WinError(ctypes.get_last_error())


def get_key(target: str = TARGET) -> str:
    """读密钥；没存过交回空字符串，其他错误照常抛出。"""
    api = _api()
    ptr = ctypes.POINTER(_Credential)()
    if not api.CredReadW(target, 1, 0, ctypes.byref(ptr)):
        error = ctypes.get_last_error()
        if error == NOT_FOUND:
            return ''
        raise ctypes.WinError(error)
    try:
        return ctypes.string_at(ptr.contents.CredentialBlob, ptr.contents.CredentialBlobSize).decode('utf-8')
    finally:
        api.CredFree(ptr)


def delete_key(target: str = TARGET) -> None:
    """删除密钥；本来就没有也算成功。"""
    api = _api()
    if not api.CredDeleteW(target, 1, 0) and ctypes.get_last_error() != NOT_FOUND:
        raise ctypes.WinError(ctypes.get_last_error())
