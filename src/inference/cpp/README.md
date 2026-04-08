# C++ XGBoost Inference Wrapper

Optional C++ acceleration for XGBoost inference in the fraud detection
pipeline. When available it provides lower per-prediction latency than
pure Python, but the system falls back to Python transparently if the
extension is not compiled.

## Prerequisites

- g++ with C++17 support
- Python 3.10+ with `pybind11` and `xgboost` installed
- The XGBoost shared library (`libxgboost.so`) -- installed automatically
  by `pip install xgboost`

## Building

From `src/inference/cpp/`:

```bash
# Using make (recommended)
make

# Or using the shell scripts
./build_python_extension.sh
```

This produces `simple_xgboost_cpp.<suffix>.so` in the current directory.
`fast_inference.py` adds this directory to `sys.path` at runtime.

## Exporting the Model

The C++ wrapper uses XGBoost's native JSON format, not the Python pickle.
To convert:

```bash
python src/inference/export_model.py \
    --input models/ieee_fraud_model_production.pkl \
    --output models/ieee_fraud_model_production_cpp.json
```

`fast_inference.py` looks for `<model>_cpp.json` alongside the pickle
file automatically.

## File Overview

| File                          | Purpose                              |
|-------------------------------|--------------------------------------|
| `simple_xgboost_wrapper.hpp`  | C++ wrapper header (XGBoost C API)   |
| `simple_xgboost_wrapper.cpp`  | C++ wrapper implementation           |
| `simple_python_bindings.cpp`  | pybind11 bindings                    |
| `xgboost_headers/c_api.h`    | Vendored XGBoost C API header        |
| `Makefile`                    | Build system                         |
| `build_simple.sh`             | Wrapper object build script          |
| `build_python_extension.sh`   | Full extension build script          |

## Architecture

```
fast_inference.py  -->  simple_xgboost_cpp (pybind11)
                           |
                    SimpleXGBoostWrapper (C++)
                           |
                    XGBoost C API (libxgboost.so)
```

If the C++ extension is not available or fails to load, `fast_inference.py`
falls back to the standard Python `model.predict_proba()` call.
