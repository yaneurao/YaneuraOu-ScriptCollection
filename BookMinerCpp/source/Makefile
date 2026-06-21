APP_NAME := BookMinerCpp
TARGET := ../$(APP_NAME).exe
BUILD_DIR := build
YANEURAOU_ROOT := ../../../YaneuraOu/source
YANEURAOU_LIB := $(BUILD_DIR)/yaneuraou/yaneuraou_core.lib
TARGET_CPU ?= OTHER

CXX ?= clang++
AR ?= ar
ARFLAGS = rcs
UNAME_S := $(shell uname -s)
WINDOWS_HOST := $(filter MINGW% MSYS% CYGWIN%,$(UNAME_S))

CXXFLAGS ?= -std=c++20 -O2 -Wall -Wextra -Wpedantic -ffunction-sections -fdata-sections
CPPFLAGS ?=
CPPFLAGS += -isystem $(YANEURAOU_ROOT) -DUSE_PIECE_VALUE -DTARGET_CPU=\"$(TARGET_CPU)\" -DUNICODE
EXTRA_CPPFLAGS ?=
CPPFLAGS += $(EXTRA_CPPFLAGS)
ifeq ($(TARGET_CPU),ZEN1)
CPPFLAGS += -DUSE_AVX2 -mbmi -mno-bmi2 -mavx2 -march=znver1
else ifeq ($(TARGET_CPU),ZEN2)
CPPFLAGS += -DUSE_AVX2 -mbmi -mno-bmi2 -mavx2 -march=znver2
else ifeq ($(TARGET_CPU),ZEN3)
CPPFLAGS += -DUSE_AVX2 -DUSE_BMI2 -mbmi -mbmi2 -mavx2 -march=znver3
else ifeq ($(TARGET_CPU),AVX512VNNI)
CPPFLAGS += -DUSE_AVX512 -DUSE_BMI2 -DUSE_VNNI -march=cascadelake
else ifeq ($(TARGET_CPU),AVXVNNI)
CPPFLAGS += -DUSE_AVX2 -DUSE_BMI2 -DUSE_VNNI -DUSE_AVXVNNI -march=alderlake
else ifeq ($(TARGET_CPU),AVX512)
CPPFLAGS += -DUSE_AVX512 -DUSE_BMI2 -march=skylake-avx512
else ifeq ($(TARGET_CPU),AVX2)
CPPFLAGS += -DUSE_AVX2 -DUSE_BMI2 -mbmi -mbmi2 -mavx2 -march=corei7-avx
else ifeq ($(TARGET_CPU),SSE42)
CPPFLAGS += -DUSE_SSE42 -msse4.2 -march=corei7
else ifeq ($(TARGET_CPU),SSE41)
CPPFLAGS += -DUSE_SSE41 -msse4.1 -march=core2
else ifeq ($(TARGET_CPU),SSSE3)
CPPFLAGS += -DUSE_SSSE3 -msse3 -march=core2
else ifeq ($(TARGET_CPU),SSE2)
CPPFLAGS += -DUSE_SSE2 -msse2 -march=core2
else ifeq ($(TARGET_CPU),NO_SSE)
CPPFLAGS += -DNO_SSE -m32 -march=pentium3
else ifeq ($(TARGET_CPU),WINARM64)
CPPFLAGS += -DIS_64BIT -DUSE_NEON=8
else ifeq ($(TARGET_CPU),ARMV8)
CPPFLAGS += -DIS_64BIT -DUSE_NEON=8
else ifeq ($(TARGET_CPU),ARMV8_DOTPROD)
CPPFLAGS += -DIS_64BIT -DUSE_NEON=8 -DUSE_NEON_DOTPROD -march=armv8.2-a+dotprod
else ifeq ($(TARGET_CPU),APPLEAVX2)
CPPFLAGS += -DIS_64BIT -DUSE_AVX2 -DUSE_BMI2 -target x86_64-apple-macos11 -mbmi -mbmi2 -mavx2 -mpopcnt
else ifeq ($(TARGET_CPU),APPLESSE42)
CPPFLAGS += -DIS_64BIT -DUSE_SSE42 -target x86_64-apple-macos11
else ifeq ($(TARGET_CPU),APPLEM1)
CPPFLAGS += -DIS_64BIT -DUSE_NEON -target arm64-apple-macos11
else
CPPFLAGS += -DNO_SSE
endif
YANEURAOU_WARNINGS := \
	-Wno-unused-parameter \
	-Wno-sign-compare \
	-Wno-unused-function \
	-Wno-unused-variable \
	-Wno-deprecated-enum-enum-conversion \
	-Wno-misleading-indentation
LDFLAGS ?=
LDFLAGS += -Wl,--gc-sections
ifneq ($(WINDOWS_HOST),)
LDFLAGS += -static -static-libgcc -static-libstdc++
endif
LDLIBS ?= -lpthread

SOURCES := \
	main.cpp \
	book_store.cpp \
	config.cpp \
	usi_engine.cpp

YANEURAOU_ADAPTER_SOURCES := \
	sfen_position.cpp \
	yaneuraou_usi_stubs.cpp

YANEURAOU_SOURCES := \
	types.cpp \
	bitboard.cpp \
	movegen.cpp \
	position.cpp \
	extra/bitop.cpp \
	extra/long_effect.cpp \
	extra/sfen_packer.cpp \
	eval/evaluate.cpp \
	eval/evaluate_bona_piece.cpp

OBJECTS := $(addprefix $(BUILD_DIR)/,$(SOURCES:.cpp=.o))
YANEURAOU_ADAPTER_OBJECTS := $(addprefix $(BUILD_DIR)/yaneuraou_adapter/,$(YANEURAOU_ADAPTER_SOURCES:.cpp=.o))
YANEURAOU_OBJECTS := $(addprefix $(BUILD_DIR)/yaneuraou/,$(YANEURAOU_SOURCES:.cpp=.o))
YANEURAOU_LIB_OBJECTS := $(YANEURAOU_ADAPTER_OBJECTS) $(YANEURAOU_OBJECTS)
DEPS := $(OBJECTS:.o=.d) $(YANEURAOU_LIB_OBJECTS:.o=.d)

.PHONY: all clean run

all: $(TARGET)

$(TARGET): $(OBJECTS) $(YANEURAOU_LIB)
	@mkdir -p $(dir $@)
	$(CXX) $(OBJECTS) $(YANEURAOU_LIB) -o $@ $(LDFLAGS) $(LDLIBS)

$(YANEURAOU_LIB): $(YANEURAOU_LIB_OBJECTS)
	@mkdir -p $(dir $@)
	$(AR) $(ARFLAGS) $@ $^

$(BUILD_DIR)/%.o: %.cpp
	@mkdir -p $(BUILD_DIR)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -MMD -MP -c $< -o $@

$(BUILD_DIR)/yaneuraou_adapter/%.o: %.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -MMD -MP -c $< -o $@

$(BUILD_DIR)/yaneuraou/%.o: $(YANEURAOU_ROOT)/%.cpp
	@mkdir -p $(dir $@)
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) $(YANEURAOU_WARNINGS) -MMD -MP -c $< -o $@

run: $(TARGET)
	cd .. && ./$(APP_NAME).exe

clean:
	rm -rf $(BUILD_DIR) $(TARGET)

ifneq ($(MAKECMDGOALS),clean)
-include $(DEPS)
endif
