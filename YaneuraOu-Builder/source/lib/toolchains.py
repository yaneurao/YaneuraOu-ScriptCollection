from __future__ import annotations


WINARM_CROSS_COMPILER = "/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang++"
WINARM_CROSS_CC = "/opt/aarch64-w64-mingw32/bin/aarch64-w64-mingw32-clang"
WINARM_CROSS_BIN_DIR = "/opt/aarch64-w64-mingw32/bin"
WINARM_CLANGARM64_INCLUDE_DIR = "/clangarm64/include"
WINARM_CLANGARM64_LIB_DIR = "/clangarm64/lib"
WINARM_EHANDLER_STUB_C = "mingw_aarch64_ehandler_stub.c"
WINARM_EHANDLER_STUB_O = "mingw_aarch64_ehandler_stub.o"

WIN32_CROSS_COMPILER = "$WIN32_CLANG_WRAPPER"
WIN32_SYSROOT = "/mingw32"
WIN32_TARGET = "i686-w64-windows-gnu"
WIN32_MINGW32_BIN_DIR = "/mingw32/bin"
WIN32_MINGW32_INCLUDE_DIR = "/mingw32/include"
WIN32_MINGW32_LIB_DIR = "/mingw32/lib"
WIN32_MINGW32_TARGET_LIB_DIR = "/mingw32/i686-w64-mingw32/lib"
