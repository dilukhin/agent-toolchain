"""Bounded Windows named-pipe transport; no approval or process execution API.

Peer facts come from kernel tokens. Server identity pins must originate in a
protected composition root. This module cannot establish bootstrap/file trust.
One request/reply/ack per connection, two workers total, no FlushFileBuffers.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import math
import os
import re
import threading
import time

import windows_owner_boundary as boundary

LIMIT = 65536
CLIENT_ACCESS = 0x12019B  # no FILE_CREATE_PIPE_INSTANCE
INVALID = C.c_void_p(-1).value
ACK = b"\x06"


class PipeError(RuntimeError):
    pass


def _require(ok, code):
    if not ok:
        raise PipeError(code)


def _api(dll, name, restype, *args):
    function = getattr(dll, name)
    function.restype, function.argtypes = restype, list(args)
    return function


class Overlapped(C.Structure):
    _fields_ = [("internal", C.c_size_t), ("internal_high", C.c_size_t),
                ("offset", W.DWORD), ("offset_high", W.DWORD), ("event", W.HANDLE)]


class SecurityAttributes(C.Structure):
    _fields_ = [("length", W.DWORD), ("descriptor", C.c_void_p), ("inherit", W.BOOL)]


class Trustee(C.Structure):
    _fields_ = [("multiple", C.c_void_p), ("operation", W.DWORD), ("form", W.DWORD),
                ("kind", W.DWORD), ("name", C.c_void_p)]


class ExplicitAccess(C.Structure):
    _fields_ = [("permissions", W.DWORD), ("mode", W.DWORD), ("inherit", W.DWORD), ("trustee", Trustee)]


class Kernel:
    def __init__(self):
        _require(os.name == "nt", "WINDOWS_REQUIRED")
        k, a = C.WinDLL("kernel32", use_last_error=True), C.WinDLL("advapi32", use_last_error=True)
        self.close = _api(k, "CloseHandle", W.BOOL, W.HANDLE)
        self.event = _api(k, "CreateEventW", W.HANDLE, C.c_void_p, W.BOOL, W.BOOL, W.LPCWSTR)
        self.wait = _api(k, "WaitForSingleObject", W.DWORD, W.HANDLE, W.DWORD)
        self.cancel = _api(k, "CancelIoEx", W.BOOL, W.HANDLE, C.POINTER(Overlapped))
        self.result = _api(k, "GetOverlappedResult", W.BOOL, W.HANDLE, C.POINTER(Overlapped), C.POINTER(W.DWORD), W.BOOL)
        self.read = _api(k, "ReadFile", W.BOOL, W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.POINTER(Overlapped))
        self.write = _api(k, "WriteFile", W.BOOL, W.HANDLE, C.c_void_p, W.DWORD, C.POINTER(W.DWORD), C.POINTER(Overlapped))
        self.connect = _api(k, "ConnectNamedPipe", W.BOOL, W.HANDLE, C.POINTER(Overlapped))
        self.disconnect = _api(k, "DisconnectNamedPipe", W.BOOL, W.HANDLE)
        self.create = _api(k, "CreateNamedPipeW", W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, W.DWORD,
                           W.DWORD, W.DWORD, W.DWORD, C.POINTER(SecurityAttributes))
        self.open = _api(k, "CreateFileW", W.HANDLE, W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE)
        self.mode = _api(k, "SetNamedPipeHandleState", W.BOOL, W.HANDLE, C.POINTER(W.DWORD), C.c_void_p, C.c_void_p)
        self.server_pid = _api(k, "GetNamedPipeServerProcessId", W.BOOL, W.HANDLE, C.POINTER(W.ULONG))
        self.open_process = _api(k, "OpenProcess", W.HANDLE, W.DWORD, W.BOOL, W.DWORD)
        self.open_process_token = _api(a, "OpenProcessToken", W.BOOL, W.HANDLE, W.DWORD, C.POINTER(W.HANDLE))
        self.impersonate = _api(a, "ImpersonateNamedPipeClient", W.BOOL, W.HANDLE)
        self.revert = _api(a, "RevertToSelf", W.BOOL)
        self.thread = _api(k, "GetCurrentThread", W.HANDLE)
        self.open_thread_token = _api(a, "OpenThreadToken", W.BOOL, W.HANDLE, W.DWORD, W.BOOL, C.POINTER(W.HANDLE))
        self.descriptor = _api(a, "ConvertStringSecurityDescriptorToSecurityDescriptorW", W.BOOL,
                               W.LPCWSTR, W.DWORD, C.POINTER(C.c_void_p), C.POINTER(W.DWORD))
        self.free = _api(k, "LocalFree", C.c_void_p, C.c_void_p)
        self.current_process = _api(k, "GetCurrentProcess", W.HANDLE)
        self.string_sid = _api(a, "ConvertStringSidToSidW", W.BOOL, W.LPCWSTR, C.POINTER(C.c_void_p))
        self.get_security = _api(a, "GetSecurityInfo", W.DWORD, W.HANDLE, C.c_int, W.DWORD,
                                 C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p), C.c_void_p, C.POINTER(C.c_void_p))
        self.set_security = _api(a, "SetSecurityInfo", W.DWORD, W.HANDLE, C.c_int, W.DWORD,
                                 C.c_void_p, C.c_void_p, C.c_void_p, C.c_void_p)
        self.merge_acl = _api(a, "SetEntriesInAclW", W.DWORD, W.ULONG, C.POINTER(ExplicitAccess),
                              C.c_void_p, C.POINTER(C.c_void_p))

    def allow_identity_query(self, agent_sid):
        """Restrict one SID to QUERY on THIS process/token; no debug privilege.

        The grants live with these kernel objects. Existing ACEs are preserved.
        No token duplicate/impersonate/adjust or process VM/write/control access.
        """
        sid, token = C.c_void_p(), W.HANDLE()
        _require(self.string_sid(agent_sid, C.byref(sid)), "PIPE_AGENT_SID_INVALID")
        try:
            process = self.current_process()
            _require(self.open_process_token(process, 0x60008, C.byref(token)), "PIPE_SERVER_TOKEN_DACL_UNAVAILABLE")
            for handle, access, all_access in ((process, 0x1000, 0x1FFFFF), (token, 8, 0xF01FF)):
                descriptor, old_acl, new_acl = C.c_void_p(), C.c_void_p(), C.c_void_p()
                try:
                    _require(self.get_security(handle, 6, 4, None, None, C.byref(old_acl), None,
                                               C.byref(descriptor)) == 0 and old_acl.value,
                             "PIPE_SERVER_DACL_UNAVAILABLE")
                    # Default group ACEs may already allow TOKEN_DUPLICATE. An allow
                    # of QUERY alone cannot narrow them: deny all other mapped rights
                    # for this exact agent SID, then grant only identity query.
                    entries = (ExplicitAccess * 2)(
                        ExplicitAccess(all_access & ~access, 3, 0, Trustee(None, 0, 0, 1, sid)),
                        ExplicitAccess(access, 1, 0, Trustee(None, 0, 0, 1, sid)))
                    _require(self.merge_acl(2, entries, old_acl, C.byref(new_acl)) == 0,
                             "PIPE_SERVER_QUERY_GRANT_FAILED")
                    _require(self.set_security(handle, 6, 4, None, None, new_acl, None) == 0,
                             "PIPE_SERVER_QUERY_GRANT_FAILED")
                finally:
                    if new_acl:
                        self.free(new_acl)
                    if descriptor:
                        self.free(descriptor)
        finally:
            if token:
                self.close(token)
            self.free(sid)

    def transfer(self, handle, kind, *, deadline, stop, raw=None, limit=LIMIT):
        event = self.event(None, True, False, None)
        _require(event, "PIPE_EVENT_FAILED")
        overlap, count = Overlapped(), W.DWORD()
        overlap.event = event
        buffer = C.create_string_buffer(raw if raw is not None else limit)
        pending = False
        try:
            _require(time.monotonic() < deadline and not stop.is_set(), "PIPE_TIMEOUT")
            if kind == "connect":
                ok = self.connect(handle, C.byref(overlap))
            elif kind == "read":
                ok = self.read(handle, buffer, limit, C.byref(count), C.byref(overlap))
            elif kind == "write":
                _require(type(raw) is bytes and 0 < len(raw) <= LIMIT, "PIPE_OUTPUT_LIMIT")
                ok = self.write(handle, buffer, len(raw), C.byref(count), C.byref(overlap))
            else:
                raise PipeError("PIPE_OPERATION_INVALID")
            error = 0 if ok else C.get_last_error()
            if kind == "connect" and error == 535:  # already connected
                return b""
            if not ok and error != 997:  # ERROR_IO_PENDING
                raise PipeError("PIPE_INPUT_LIMIT" if error == 234 else "PIPE_IO_FAILED")
            pending = not ok
            while pending:
                remaining = deadline - time.monotonic()
                _require(remaining > 0 and not stop.is_set(), "PIPE_TIMEOUT")
                status = self.wait(event, max(1, min(50, math.ceil(remaining * 1000))))
                _require(status in (0, 258), "PIPE_WAIT_FAILED")
                if status == 0:
                    # Even an error completes the pending IO and releases its buffer.
                    pending = False
                    ok = self.result(handle, C.byref(overlap), C.byref(count), False)
                    error = 0 if ok else C.get_last_error()
                    _require(ok, "PIPE_INPUT_LIMIT" if error == 234 else "PIPE_IO_FAILED")
            if kind == "read":
                _require(0 < count.value <= limit, "PIPE_INPUT_INVALID")
                return buffer.raw[:count.value]
            if kind == "write":
                _require(count.value == len(raw), "PIPE_SHORT_WRITE")
            return b""
        finally:
            if pending:
                self.cancel(handle, C.byref(overlap))
                # Never free OVERLAPPED/buffer while Windows can still reference it.
                # Failure to drain is a process-fatal OS IO failure, never an approval.
                if self.wait(event, 1000) != 0:
                    os._exit(70)
                self.result(handle, C.byref(overlap), C.byref(count), False)
            self.close(event)

    def principal(self, handle, *, standard_agent=False):
        # Call only after successfully reading the client's message.
        _require(self.impersonate(handle), "PIPE_CLIENT_TOKEN_FAILED")
        token = W.HANDLE()
        try:
            _require(self.open_thread_token(self.thread(), 8, True, C.byref(token)), "PIPE_CLIENT_TOKEN_FAILED")
        finally:
            if not self.revert():
                os._exit(70)
        try:
            # Inspect the retained authenticated token as the server, never run
            # local security-provider queries while impersonating an identification token.
            if standard_agent:
                boundary.require_standard_agent_token(token)
            return boundary.from_token(token)
        finally:
            if token:
                self.close(token)

    def authenticate_server(self, handle, expected_owner, expected_pid):
        boundary.validate(expected_owner)
        pid = W.ULONG()
        _require(self.server_pid(handle, C.byref(pid)), "PIPE_SERVER_PID_FAILED")
        _require(expected_pid is None or pid.value == expected_pid, "PIPE_SERVER_PID_MISMATCH")
        process = self.open_process(0x1000, False, pid.value)
        _require(process, "PIPE_SERVER_PROCESS_UNAVAILABLE")
        token = W.HANDLE()
        try:
            # Keep the process handle alive while reading TokenUser/TokenSessionId.
            _require(self.open_process_token(process, 8, C.byref(token)), "PIPE_SERVER_TOKEN_FAILED")
            _require(boundary.from_token(token) == expected_owner, "PIPE_SERVER_PRINCIPAL_MISMATCH")
            again = W.ULONG()
            _require(self.server_pid(handle, C.byref(again)) and again.value == pid.value,
                     "PIPE_SERVER_PROCESS_CHANGED")
        finally:
            if token:
                self.close(token)
            self.close(process)


def _address(value):
    _require(type(value) is str and re.fullmatch(r"\\\\\.\\pipe\\[A-Za-z0-9_-]{1,120}", value) is not None,
             "PIPE_LOCAL_ADDRESS_REQUIRED")
    return value


def call(address, raw, *, expected_owner, expected_pid=None, timeout=5):
    _address(address)
    _require(type(raw) is bytes and 0 < len(raw) <= LIMIT, "PIPE_INPUT_LIMIT")
    _require(type(timeout) in (int, float) and math.isfinite(timeout) and 0 < timeout <= 40, "PIPE_TIMEOUT_INVALID")
    k, stop, deadline = Kernel(), threading.Event(), time.monotonic() + timeout
    while True:
        # OVERLAPPED + SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION.
        handle = k.open(address, CLIENT_ACCESS, 0, None, 3, 0x40110000, None)
        if handle != INVALID:
            break
        error = C.get_last_error()
        _require(error == 231 and time.monotonic() < deadline, "PIPE_CONNECT_FAILED")
        stop.wait(min(0.01, max(0, deadline - time.monotonic())))
    try:
        k.authenticate_server(handle, expected_owner, expected_pid)
        mode = W.DWORD(2)  # message read mode; server also uses message type
        _require(k.mode(handle, C.byref(mode), None, None), "PIPE_MESSAGE_MODE_FAILED")
        k.transfer(handle, "write", deadline=deadline, stop=stop, raw=raw)
        result = k.transfer(handle, "read", deadline=deadline, stop=stop)
        k.transfer(handle, "write", deadline=deadline, stop=stop, raw=ACK)
        return result
    finally:
        k.close(handle)


class Transport:
    """Two bounded channels for a trusted, bounded in-process handler.

    handler(channel, kernel_principal, bytes) returns bytes, never a callback or
    executor supplied by a client. It must itself terminate within handler_limit.
    Session separation and exact operation authorization belong to the host.
    """
    def __init__(self, prefix, agent_sid, handler, *, io_timeout=2, handler_limit=35):
        _require(type(prefix) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", prefix) is not None,
                 "PIPE_PREFIX_INVALID")
        self.owner = boundary.current()
        boundary.validate(boundary.Principal(agent_sid, 0))
        _require(agent_sid != self.owner.sid and callable(handler), "PIPE_AUTHORITY_NOT_SEPARATE")
        _require(type(io_timeout) in (int, float) and math.isfinite(io_timeout) and 0 < io_timeout <= 5,
                 "PIPE_TIMEOUT_INVALID")
        _require(type(handler_limit) in (int, float) and math.isfinite(handler_limit) and 0 < handler_limit <= 35,
                 "PIPE_HANDLER_LIMIT_INVALID")
        self.k, self.stop = Kernel(), threading.Event()
        self.k.allow_identity_query(agent_sid)
        self.handler, self.agent_sid = handler, agent_sid
        self.io_timeout, self.handler_limit = io_timeout, handler_limit
        self.addresses = {name: _address(rf"\\.\pipe\{prefix}-{name}") for name in ("agent", "decision")}
        self.handles, self.threads, self.failed = [], [], threading.Event()
        self.last_refusal = {}  # at most two fixed codes, never raw requests/exception diagnostics
        try:
            for channel, address in self.addresses.items():
                acl = f"D:P(A;;GA;;;SY)(A;;GA;;;{self.owner.sid})"
                if channel == "agent":
                    acl += f"(A;;0x{CLIENT_ACCESS:x};;;{agent_sid})"
                descriptor = C.c_void_p()
                _require(self.k.descriptor(acl, 1, C.byref(descriptor), None), "PIPE_DACL_INVALID")
                try:
                    attrs = SecurityAttributes(C.sizeof(SecurityAttributes), descriptor, False)
                    # FIRST_PIPE_INSTANCE, OVERLAPPED; message type/read mode, reject remote clients.
                    handle = self.k.create(address, 3 | 0x80000 | 0x40000000, 4 | 2 | 8,
                                           1, LIMIT, LIMIT, 0, C.byref(attrs))
                    _require(handle != INVALID, "PIPE_CREATE_FAILED")
                    self.handles.append(handle)
                finally:
                    self.k.free(descriptor)
                self.threads.append(threading.Thread(target=self._serve, args=(handle, channel), daemon=True))
            for thread in self.threads:
                thread.start()
        except BaseException:
            self.close()
            raise

    def _serve(self, handle, channel):
        try:
            while not self.stop.is_set():
                try:
                    self.k.transfer(handle, "connect", deadline=time.monotonic() + self.io_timeout, stop=self.stop)
                    deadline = time.monotonic() + self.io_timeout
                    raw = self.k.transfer(handle, "read", deadline=deadline, stop=self.stop)
                    principal = self.k.principal(handle, standard_agent=channel == "agent")
                    _require(principal.sid == (self.agent_sid if channel == "agent" else self.owner.sid),
                             "PIPE_WRONG_CLIENT")
                    result = self.handler(channel, principal, raw)
                    deadline = time.monotonic() + self.io_timeout
                    self.k.transfer(handle, "write", deadline=deadline, stop=self.stop, raw=result)
                    # Explicit ACK replaces unbounded FlushFileBuffers.
                    _require(self.k.transfer(handle, "read", deadline=deadline, stop=self.stop, limit=1) == ACK,
                             "PIPE_REPLY_NOT_ACKNOWLEDGED")
                except (PipeError, boundary.BoundaryError) as error:
                    self.last_refusal[channel] = str(error)
                finally:
                    self.k.disconnect(handle)
        except BaseException:
            self.failed.set()
            self.stop.set()

    def close(self):
        self.stop.set()
        deadline = time.monotonic() + self.handler_limit + 2
        for thread in self.threads:
            if thread.ident is not None:
                thread.join(timeout=max(0, deadline - time.monotonic()))
        if any(t.is_alive() for t in self.threads):
            # Do not close/recycle handles still referenced by a trusted handler.
            raise PipeError("PIPE_HANDLER_SHUTDOWN_INCOMPLETE")
        for handle in self.handles:
            self.k.close(handle)
        self.handles.clear()

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        self.close()
