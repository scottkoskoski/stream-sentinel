/**
 * @file simple_python_bindings.cpp
 * @brief Simple Python bindings for minimal XGBoost wrapper
 * 
 * Provides basic pybind11 interface for SimpleXGBoostWrapper.
 * Designed for drop-in replacement of Python XGBoost inference.
 */

#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include <pybind11/numpy.h>
#include "simple_xgboost_wrapper.hpp"

namespace py = pybind11;
using namespace stream_sentinel;

PYBIND11_MODULE(simple_xgboost_cpp, m) {
    m.doc() = "Simple C++ XGBoost wrapper for fraud detection performance optimization";
    
    py::class_<SimpleXGBoostWrapper>(m, "SimpleXGBoostWrapper")
        .def(py::init<>())
        .def("load_model", &SimpleXGBoostWrapper::load_model,
             "Load XGBoost model from file",
             py::arg("model_path"))
        .def("predict", &SimpleXGBoostWrapper::predict,
             "Predict fraud probability for feature vector",
             py::arg("features"))
        .def("get_last_error", &SimpleXGBoostWrapper::get_last_error,
             "Get last error message")
        .def("is_loaded", &SimpleXGBoostWrapper::is_loaded,
             "Check if model is loaded");
    
    // Utility function for easy testing
    m.def("create_wrapper", []() {
        return std::make_unique<SimpleXGBoostWrapper>();
    }, "Create a new SimpleXGBoostWrapper instance");
}