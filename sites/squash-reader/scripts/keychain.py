"""Store the local sync credential in the macOS login Keychain, never in source."""
import ctypes as c
import getpass
import sys
import os

SERVICE = 'pk-squash-public-sync'
ACCOUNT = os.environ.get('PK_SYNC_HOST', 'localhost')
CF = c.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
SEC = c.CDLL('/System/Library/Frameworks/Security.framework/Security')
CF.CFStringCreateWithCString.argtypes = [c.c_void_p,c.c_char_p,c.c_uint32]
CF.CFStringCreateWithCString.restype = c.c_void_p
CF.CFDataCreate.argtypes = [c.c_void_p,c.c_char_p,c.c_long]
CF.CFDataCreate.restype = c.c_void_p
CF.CFDictionaryCreate.argtypes = [c.c_void_p,c.POINTER(c.c_void_p),c.POINTER(c.c_void_p),c.c_long,c.c_void_p,c.c_void_p]
CF.CFDictionaryCreate.restype = c.c_void_p
CF.CFDataGetLength.argtypes=[c.c_void_p]
CF.CFDataGetLength.restype=c.c_long
CF.CFDataGetBytePtr.argtypes=[c.c_void_p]
CF.CFDataGetBytePtr.restype=c.c_void_p
CF.CFRelease.argtypes=[c.c_void_p]
SEC.SecItemAdd.argtypes=[c.c_void_p,c.c_void_p]
SEC.SecItemCopyMatching.argtypes=[c.c_void_p,c.POINTER(c.c_void_p)]
SEC.SecItemUpdate.argtypes=[c.c_void_p,c.c_void_p]


def constant(name):
    return c.c_void_p.in_dll(SEC,name).value


def string(value):
    return CF.CFStringCreateWithCString(None,value.encode(),0x08000100)


def dictionary(items):
    keys=(c.c_void_p*len(items))(*(constant(key) for key,_ in items))
    values=(c.c_void_p*len(items))(*(value for _,value in items))
    return CF.CFDictionaryCreate(None,keys,values,len(items),c.addressof(c.c_byte.in_dll(CF,'kCFTypeDictionaryKeyCallBacks')),c.addressof(c.c_byte.in_dll(CF,'kCFTypeDictionaryValueCallBacks')))


def query_items():
    return [('kSecClass',constant('kSecClassGenericPassword')),('kSecAttrService',string(SERVICE)),('kSecAttrAccount',string(ACCOUNT))]


def get_secret():
    result=c.c_void_p()
    query=dictionary(query_items()+[('kSecReturnData',c.c_void_p.in_dll(CF,'kCFBooleanTrue').value),('kSecMatchLimit',constant('kSecMatchLimitOne'))])
    try:
        status=SEC.SecItemCopyMatching(query,c.byref(result))
        if status != 0:
            raise RuntimeError(f'Keychain credential unavailable ({status})')
        return c.string_at(CF.CFDataGetBytePtr(result),CF.CFDataGetLength(result)).decode()
    finally:
        CF.CFRelease(query)
        if result.value: CF.CFRelease(result)


def set_secret(secret):
    raw=secret.encode()
    value=CF.CFDataCreate(None,raw,len(raw))
    items=query_items()
    query=dictionary(items)
    attributes=dictionary([('kSecValueData',value)])
    combined=dictionary(items+[('kSecValueData',value)])
    try:
        status=SEC.SecItemAdd(combined,None)
        if status == -25299:
            status=SEC.SecItemUpdate(query,attributes)
        if status != 0: raise RuntimeError(f'Keychain store failed ({status})')
    finally:
        for item in (query,attributes,combined,value): CF.CFRelease(item)
        for _,item in items[1:]: CF.CFRelease(item)


if __name__ == '__main__':
    if sys.argv[1:] != ['store']:
        raise SystemExit('Usage: keychain.py store')
    secret=getpass.getpass('Sync credential (hidden): ')
    set_secret(secret)
    print('Sync credential saved to macOS Keychain')
