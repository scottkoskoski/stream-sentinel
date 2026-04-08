# Makefile for Stream-Sentinel C++ XGBoost inference wrapper
#
# Builds the pybind11 Python extension (simple_xgboost_cpp) that provides
# native XGBoost inference for optional performance acceleration.
#
# Prerequisites:
#   - g++ with C++17 support
#   - Python 3 with pybind11 and xgboost installed
#
# Targets:
#   make           -- build the Python extension
#   make wrapper   -- build only the C++ object file
#   make clean     -- remove build artifacts
#
# All paths are derived from the active Python environment.

CXX       := g++
CXXFLAGS  := -std=c++17 -fPIC -O3 -Wall -Wextra -Wno-unused-parameter

PYTHON    := python3
PY_INCLUDES := $(shell $(PYTHON) -m pybind11 --includes)
PY_EXT_SUFFIX := $(shell $(PYTHON) -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))")
XGBOOST_LIB := $(shell $(PYTHON) -c "import xgboost; from pathlib import Path; print(Path(xgboost.__file__).parent / 'lib' / 'libxgboost.so')")

BUILD_DIR := build_simple
OBJ       := $(BUILD_DIR)/simple_xgboost_wrapper.o
TARGET    := simple_xgboost_cpp$(PY_EXT_SUFFIX)

.PHONY: all wrapper clean

all: $(TARGET)

wrapper: $(OBJ)

$(BUILD_DIR):
	mkdir -p $(BUILD_DIR)

$(OBJ): simple_xgboost_wrapper.cpp simple_xgboost_wrapper.hpp | $(BUILD_DIR)
	$(CXX) $(CXXFLAGS) -I xgboost_headers -c $< -o $@

$(TARGET): $(OBJ) simple_python_bindings.cpp simple_xgboost_wrapper.hpp
	$(CXX) $(CXXFLAGS) -shared $(PY_INCLUDES) \
		-I xgboost_headers \
		$(OBJ) simple_python_bindings.cpp \
		$(XGBOOST_LIB) \
		-o $@

clean:
	rm -rf $(BUILD_DIR) $(TARGET)
