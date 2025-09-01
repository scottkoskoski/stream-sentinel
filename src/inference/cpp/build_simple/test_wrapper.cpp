#include "../simple_xgboost_wrapper.hpp"
#include <iostream>
#include <vector>

int main() {
    std::cout << "Testing Simple XGBoost Wrapper..." << std::endl;
    
    stream_sentinel::SimpleXGBoostWrapper wrapper;
    
    // Test model loading
    std::string model_path = "/home/scottyk/Documents/stream-sentinel/models/ieee_fraud_model_cpp.json";
    
    if (wrapper.load_model(model_path)) {
        std::cout << "Model loaded successfully" << std::endl;
        
        // Test prediction with dummy features (200 features)
        std::vector<float> test_features(200, 1.0f);
        
        double prediction = wrapper.predict(test_features);
        
        if (prediction >= 0.0) {
            std::cout << "Prediction successful: " << prediction << std::endl;
        } else {
            std::cout << "Prediction failed: " << wrapper.get_last_error() << std::endl;
            return 1;
        }
    } else {
        std::cout << "Model loading failed: " << wrapper.get_last_error() << std::endl;
        return 1;
    }
    
    std::cout << "Simple XGBoost wrapper test PASSED!" << std::endl;
    return 0;
}
