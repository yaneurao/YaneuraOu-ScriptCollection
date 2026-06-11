APP_NAME := BookMinerCpp
TARGET := ../$(APP_NAME).exe
BUILD_DIR := build
YANEURAOU_ROOT := ../../../YaneuraOu/source
YANEURAOU_LIB := $(BUILD_DIR)/yaneuraou/yaneuraou_core.lib

CXX ?= clang++
AR ?= ar
ARFLAGS = rcs

CXXFLAGS ?= -std=c++20 -O2 -Wall -Wextra -Wpedantic -ffunction-sections -fdata-sections
CPPFLAGS ?=
CPPFLAGS += -isystem $(YANEURAOU_ROOT) -DUSE_PIECE_VALUE -DUSE_SFEN_PACKER -DNO_SSE -DTARGET_CPU=\"OTHER\" -DUNICODE
YANEURAOU_WARNINGS := \
	-Wno-unused-parameter \
	-Wno-sign-compare \
	-Wno-unused-function \
	-Wno-unused-variable \
	-Wno-deprecated-enum-enum-conversion \
	-Wno-misleading-indentation
LDFLAGS ?=
LDFLAGS += -Wl,--gc-sections
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

-include $(DEPS)
