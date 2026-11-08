"""Windows actor observations and conservative interactive-owner admission.

Principal validation alone is not provenance. Live endpoints must obtain these
facts from process/client tokens, never an agent-supplied JSON report.
"""
from dataclasses import dataclass
import os
import re


class BoundaryError(ValueError):
    pass


@dataclass(frozen=True)
class Principal:
    sid: str
    session_id: int


def validate(value):
    if (type(value) is not Principal or type(value.sid) is not str
            or re.fullmatch(r"S-1-[0-9]+(?:-[0-9]+)+", value.sid) is None
            or type(value.session_id) is not int or not 0 <= value.session_id <= 0xFFFFFFFF):
        raise BoundaryError("WINDOWS_PRINCIPAL_INVALID")


def admit(owner, agent, expected_agent_sid):
    validate(owner)
    validate(agent)
    if owner.sid == agent.sid:
        raise BoundaryError("OWNER_AGENT_SID_SAME")
    if agent.sid != expected_agent_sid:
        raise BoundaryError("WRONG_AGENT_SID")
    if owner.session_id == 0 or agent.session_id == 0:
        raise BoundaryError("INTERACTIVE_WINDOWS_SESSIONS_REQUIRED")
    if owner.session_id == agent.session_id:
        raise BoundaryError("SEPARATE_WINDOWS_SESSIONS_REQUIRED")


def from_token(token):
    if os.name != "nt":
        raise BoundaryError("WINDOWS_REQUIRED")
    import ctypes as C
    from ctypes import wintypes as W
    api = C.WinDLL("advapi32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    info = api.GetTokenInformation
    info.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    info.restype = W.BOOL
    convert = api.ConvertSidToStringSidW
    convert.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
    convert.restype = W.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [C.c_void_p], C.c_void_p
    size = W.DWORD()
    info(token, 1, None, 0, C.byref(size))
    if not 0 < size.value <= 65536:
        raise BoundaryError("WINDOWS_TOKEN_SIZE_INVALID")
    buf = C.create_string_buffer(size.value)
    if not info(token, 1, buf, size, C.byref(size)):
        raise BoundaryError("WINDOWS_TOKEN_QUERY_FAILED")
    sid = W.LPWSTR()
    if not convert(C.cast(buf, C.POINTER(C.c_void_p))[0], C.byref(sid)):
        raise BoundaryError("WINDOWS_SID_QUERY_FAILED")
    try:
        sid_value = sid.value
    finally:
        kernel.LocalFree(C.cast(sid, C.c_void_p))
    session = W.DWORD()
    if not info(token, 12, C.byref(session), C.sizeof(session), C.byref(size)):  # TokenSessionId
        raise BoundaryError("WINDOWS_SESSION_QUERY_FAILED")
    result = Principal(sid_value, session.value)
    validate(result)
    return result


def current():
    if os.name != "nt":
        raise BoundaryError("WINDOWS_REQUIRED")
    import ctypes as C
    from ctypes import wintypes as W
    kernel = C.WinDLL("kernel32", use_last_error=True)
    api = C.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], W.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [W.HANDLE], W.BOOL
    api.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, C.POINTER(W.HANDLE)]
    api.OpenProcessToken.restype = W.BOOL
    token = W.HANDLE()
    if not api.OpenProcessToken(kernel.GetCurrentProcess(), 8, C.byref(token)):
        raise BoundaryError("WINDOWS_TOKEN_QUERY_FAILED")
    try:
        return from_token(token)
    finally:
        kernel.CloseHandle(token)


def require_standard_agent_token(token):
    """Live-only conservative privilege gate; reports/SID alone cannot satisfy it.

    Even deny-only Administrators membership can have an elevated linked token.
    No account/group/privilege is changed by this check.
    """
    if os.name != "nt":
        raise BoundaryError("WINDOWS_REQUIRED")
    import ctypes as C
    from ctypes import wintypes as W
    api = C.WinDLL("advapi32", use_last_error=True)
    kernel = C.WinDLL("kernel32", use_last_error=True)
    api.GetTokenInformation.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD, C.POINTER(W.DWORD)]
    api.GetTokenInformation.restype = W.BOOL
    api.ConvertSidToStringSidW.argtypes = [C.c_void_p, C.POINTER(W.LPWSTR)]
    api.ConvertSidToStringSidW.restype = W.BOOL
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [C.c_void_p], C.c_void_p

    def query(kind):
        size = W.DWORD()
        api.GetTokenInformation(token, kind, None, 0, C.byref(size))
        if not 0 < size.value <= 65536:
            raise BoundaryError(f"WINDOWS_AGENT_TOKEN_SIZE_{kind}_UNAVAILABLE")
        buffer = C.create_string_buffer(size.value)
        if not api.GetTokenInformation(token, kind, buffer, size, C.byref(size)):
            raise BoundaryError(f"WINDOWS_AGENT_TOKEN_QUERY_{kind}_UNAVAILABLE")
        return buffer

    def sid_text(pointer):
        output = W.LPWSTR()
        if not api.ConvertSidToStringSidW(pointer, C.byref(output)):
            raise BoundaryError("WINDOWS_AGENT_TOKEN_UNAVAILABLE")
        try:
            return output.value
        finally:
            kernel.LocalFree(C.cast(output, C.c_void_p))

    if C.cast(query(18), C.POINTER(W.DWORD))[0] != 1 or C.cast(query(20), C.POINTER(W.DWORD))[0] != 0:
        raise BoundaryError("STANDARD_AGENT_TOKEN_REQUIRED")
    integrity_buffer = query(25)  # retain storage while the SID pointer is in use
    integrity = sid_text(C.cast(integrity_buffer, C.POINTER(C.c_void_p))[0])
    if not re.fullmatch(r"S-1-16-[0-9]+", integrity) or not 4096 <= int(integrity.rsplit("-", 1)[1]) <= 8192:
        raise BoundaryError("STANDARD_AGENT_TOKEN_REQUIRED")

    class SidAndAttributes(C.Structure):
        _fields_ = [("sid", C.c_void_p), ("attributes", W.DWORD)]
    class Groups(C.Structure):
        _fields_ = [("count", W.DWORD), ("entries", SidAndAttributes * 1)]
    groups = query(2)
    count = C.cast(groups, C.POINTER(W.DWORD))[0]
    offset = Groups.entries.offset
    if count > 1024 or offset + count * C.sizeof(SidAndAttributes) > C.sizeof(groups):
        raise BoundaryError("WINDOWS_AGENT_TOKEN_UNAVAILABLE")
    entries = C.cast(C.addressof(groups) + offset, C.POINTER(SidAndAttributes))
    privileged_groups = {f"S-1-5-32-{rid}" for rid in (544, 547, 548, 549, 550, 551, 552, 556, 578)}
    for index in range(count):
        if sid_text(entries[index].sid) in privileged_groups:
            raise BoundaryError("STANDARD_AGENT_TOKEN_REQUIRED")

    class Luid(C.Structure):
        _fields_ = [("low", W.DWORD), ("high", W.LONG)]
    class Privilege(C.Structure):
        _fields_ = [("luid", Luid), ("attributes", W.DWORD)]
    class Privileges(C.Structure):
        _fields_ = [("count", W.DWORD), ("entries", Privilege * 1)]
    privileges = query(3)
    count = C.cast(privileges, C.POINTER(W.DWORD))[0]
    offset = Privileges.entries.offset
    if count > 1024 or offset + count * C.sizeof(Privilege) > C.sizeof(privileges):
        raise BoundaryError("WINDOWS_AGENT_TOKEN_UNAVAILABLE")
    entries = C.cast(C.addressof(privileges) + offset, C.POINTER(Privilege))
    api.LookupPrivilegeNameW.argtypes = [W.LPCWSTR, C.POINTER(Luid), W.LPWSTR, C.POINTER(W.DWORD)]
    api.LookupPrivilegeNameW.restype = W.BOOL
    safe = {"SeChangeNotifyPrivilege", "SeIncreaseWorkingSetPrivilege", "SeTimeZonePrivilege",
            "SeShutdownPrivilege", "SeUndockPrivilege"}
    for index in range(count):
        size, name = W.DWORD(256), C.create_unicode_buffer(256)
        if not api.LookupPrivilegeNameW(None, C.byref(entries[index].luid), name, C.byref(size)):
            raise BoundaryError("WINDOWS_AGENT_PRIVILEGE_NAME_UNAVAILABLE")
        if name.value not in safe:
            raise BoundaryError("STANDARD_AGENT_TOKEN_REQUIRED")
