"""macOS Keychain generic-password API; secret values never enter process argv."""
import ctypes
import sys


def _framework():
    if sys.platform != 'darwin':
        raise RuntimeError('macOS secure store required')
    framework = ctypes.CDLL('/System/Library/Frameworks/Security.framework/Security')
    framework.SecKeychainFindGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    framework.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    framework.SecKeychainAddGenericPassword.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_void_p]
    framework.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    framework.SecKeychainItemFreeContent.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    framework.SecKeychainItemFreeContent.restype = ctypes.c_int32
    return framework


def read_secret(service, account):
    framework = _framework()
    service, account = service.encode(), account.encode()
    length, data = ctypes.c_uint32(), ctypes.c_void_p()
    status = framework.SecKeychainFindGenericPassword(None, len(service), service, len(account), account, ctypes.byref(length), ctypes.byref(data), None)
    if status == -25300:  # errSecItemNotFound
        return None
    if status:
        raise RuntimeError('Keychain read requires secure user action; OSStatus ' + str(status))
    try:
        return ctypes.string_at(data, length.value)
    finally:
        framework.SecKeychainItemFreeContent(None, data)


def add_secret(service, account, secret):
    framework = _framework()
    service, account = service.encode(), account.encode()
    status = framework.SecKeychainAddGenericPassword(None, len(service), service, len(account), account, len(secret), secret, None)
    if status:
        raise RuntimeError('Keychain storage requires secure user action; OSStatus ' + str(status))
