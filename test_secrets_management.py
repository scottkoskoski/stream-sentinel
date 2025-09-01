#!/usr/bin/env python3
"""
Secrets Management Validation Script

This script validates that the secrets management implementation works correctly
by testing environment variable loading, configuration validation, and security checks.

Usage:
    python test_secrets_management.py

The script will:
1. Test .env file loading
2. Validate configuration systems
3. Check for security issues
4. Provide recommendations
"""

import os
import sys
from pathlib import Path
from typing import Dict, Any, List, Tuple
import tempfile
import subprocess

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / 'src'))

def test_env_file_loading():
    """Test that .env files are properly loaded by configuration modules."""
    print("🔍 Testing .env file loading...")
    
    # Create a temporary .env file for testing
    test_env_content = """
# Test environment configuration
STREAM_SENTINEL_ENV=development
POSTGRES_PASSWORD=test-postgres-password-12345678901234567890
REDIS_PASSWORD=test-redis-password-12345678901234567890
CLICKHOUSE_PASSWORD=test-clickhouse-password-12345678901234567890
KAFKA_BOOTSTRAP_SERVERS=test-kafka:9092
"""
    
    env_path = Path(__file__).parent / '.env'
    backup_env = None
    
    # Backup existing .env if it exists
    if env_path.exists():
        backup_env = env_path.read_text()
        print("  📄 Backing up existing .env file")
    
    try:
        # Write test .env file
        env_path.write_text(test_env_content.strip())
        print("  ✏️  Created test .env file")
        
        # Test Kafka configuration loading
        try:
            from kafka.config import get_kafka_config
            kafka_config = get_kafka_config()
            
            if os.getenv('STREAM_SENTINEL_ENV') == 'development':
                print("  ✅ Kafka config loaded .env file successfully")
            else:
                print("  ❌ Kafka config did not load .env file")
                return False
        except Exception as e:
            print(f"  ❌ Kafka config loading failed: {e}")
            return False
        
        # Test persistence configuration loading  
        try:
            from persistence.config import get_database_configs
            postgres_config, clickhouse_config = get_database_configs()
            
            if (postgres_config.password.startswith('test-postgres-password') and 
                clickhouse_config.password.startswith('test-clickhouse-password')):
                print("  ✅ Database configs loaded .env file successfully")
            else:
                print("  ❌ Database configs did not load .env file properly")
                return False
        except Exception as e:
            print(f"  ❌ Database config loading failed: {e}")
            return False
        
        print("  🎉 .env file loading test PASSED")
        return True
        
    finally:
        # Restore original .env file
        if backup_env:
            env_path.write_text(backup_env)
            print("  🔄 Restored original .env file")
        elif env_path.exists():
            env_path.unlink()
            print("  🗑️  Removed test .env file")


def test_default_password_detection():
    """Test for presence of default passwords that should be changed."""
    print("\n🔍 Testing for default passwords...")
    
    # Focus on actual hardcoded passwords that indicate security problems
    dangerous_patterns = [
        'password=stream_sentinel_password',
        'password=password123',
        'password=admin123', 
        'password=root123',
        'password=default',
        'PASSWORD=password123',
        'PASSWORD=admin123',
        'PASSWORD=default'
    ]
    
    files_to_check = [
        'docker/docker-compose.yml',
        'docker/docker-compose.secure.yml',
        '.env'
    ]
    
    issues_found = []
    
    for file_path in files_to_check:
        full_path = Path(__file__).parent / file_path
        if not full_path.exists():
            continue
            
        try:
            content = full_path.read_text()
            for pattern in dangerous_patterns:
                if pattern in content:
                    issues_found.append(f"  ❌ Found dangerous password pattern '{pattern}' in {file_path}")
        except Exception as e:
            print(f"  ⚠️  Could not check {file_path}: {e}")
    
    if issues_found:
        print("  🚨 Security issues found:")
        for issue in issues_found:
            print(issue)
        return False
    else:
        print("  ✅ No default passwords found in configuration files")
        return True


def test_password_strength():
    """Test that environment variables meet password strength requirements."""
    print("\n🔍 Testing password strength requirements...")
    
    password_vars = [
        'POSTGRES_PASSWORD',
        'REDIS_PASSWORD', 
        'CLICKHOUSE_PASSWORD',
        'KAFKA_PASSWORD',
        'SCHEMA_REGISTRY_PASSWORD'
    ]
    
    min_length = 32
    issues = []
    
    for var in password_vars:
        value = os.getenv(var)
        if value:
            if len(value) < min_length:
                issues.append(f"  ❌ {var} is too short ({len(value)} chars, minimum {min_length})")
            elif value.startswith('CHANGEME'):
                issues.append(f"  ❌ {var} still has default CHANGEME value")
            else:
                print(f"  ✅ {var} meets strength requirements")
    
    if issues:
        print("  🚨 Password strength issues:")
        for issue in issues:
            print(issue)
        return False
    else:
        print("  ✅ All configured passwords meet strength requirements")
        return True


def test_docker_compose_functionality():
    """Test that Docker Compose can parse the configuration with environment variables."""
    print("\n🔍 Testing Docker Compose configuration...")
    
    compose_file = Path(__file__).parent / 'docker' / 'docker-compose.yml'
    if not compose_file.exists():
        print("  ❌ docker-compose.yml not found")
        return False
    
    try:
        # Test Docker Compose config parsing
        result = subprocess.run([
            'docker-compose', 
            '-f', str(compose_file),
            'config', 
            '--quiet'
        ], 
        capture_output=True, 
        text=True, 
        cwd=compose_file.parent
        )
        
        if result.returncode == 0:
            print("  ✅ Docker Compose configuration is valid")
            return True
        else:
            print(f"  ❌ Docker Compose configuration error: {result.stderr}")
            return False
            
    except FileNotFoundError:
        print("  ⚠️  Docker Compose not found, skipping validation")
        return True
    except Exception as e:
        print(f"  ❌ Docker Compose test failed: {e}")
        return False


def test_gitignore_protection():
    """Test that sensitive files are properly protected by .gitignore."""
    print("\n🔍 Testing .gitignore protection...")
    
    gitignore_path = Path(__file__).parent / '.gitignore'
    if not gitignore_path.exists():
        print("  ❌ .gitignore file not found")
        return False
    
    gitignore_content = gitignore_path.read_text()
    
    required_patterns = [
        '.env',
        '.env.local',
        '.env.production',
        '*.key',
        '*.pem'
    ]
    
    missing_patterns = []
    for pattern in required_patterns:
        if pattern not in gitignore_content:
            missing_patterns.append(pattern)
    
    if missing_patterns:
        print(f"  ❌ Missing .gitignore patterns: {missing_patterns}")
        return False
    else:
        print("  ✅ All sensitive file patterns are in .gitignore")
        return True


def generate_secure_passwords():
    """Generate sample secure passwords for the .env file."""
    print("\n🔐 Generating sample secure passwords...")
    
    try:
        import secrets
        import string
        
        def generate_password(length=32):
            alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
            return ''.join(secrets.choice(alphabet) for _ in range(length))
        
        passwords = {
            'POSTGRES_PASSWORD': generate_password(32),
            'REDIS_PASSWORD': generate_password(32),
            'CLICKHOUSE_PASSWORD': generate_password(32),
            'KAFKA_UI_PASSWORD': generate_password(32),
            'SCHEMA_REGISTRY_PASSWORD': generate_password(32),
            'SSL_TRUSTSTORE_PASSWORD': generate_password(16),
            'SSL_KEYSTORE_PASSWORD': generate_password(16),
            'AUTH_SECRET_KEY': generate_password(64)
        }
        
        print("  🎲 Sample secure passwords generated:")
        print("  " + "="*60)
        for key, value in passwords.items():
            print(f"  {key}={value}")
        print("  " + "="*60)
        print("  ⚠️  Save these to your .env file and keep them secure!")
        
        return True
        
    except Exception as e:
        print(f"  ❌ Password generation failed: {e}")
        return False


def print_security_recommendations():
    """Print security recommendations for production deployment."""
    print("\n🛡️  Security Recommendations:")
    print("="*70)
    
    recommendations = [
        "1. Change ALL default passwords before production deployment",
        "2. Use a dedicated secrets management system (Vault, K8s secrets, etc.)",
        "3. Enable SSL/TLS encryption for all service communication", 
        "4. Implement proper network segmentation and firewall rules",
        "5. Enable audit logging for all services",
        "6. Set up monitoring for security events and anomalies",
        "7. Implement regular password rotation policies",
        "8. Use multi-factor authentication for administrative access",
        "9. Regular security assessments and penetration testing",
        "10. Keep all services and dependencies updated",
        "11. Implement proper backup and disaster recovery procedures",
        "12. Document incident response procedures"
    ]
    
    for rec in recommendations:
        print(f"   {rec}")
    
    print("="*70)


def main():
    """Run all secrets management tests."""
    print("🔒 Stream-Sentinel Secrets Management Validation")
    print("="*50)
    
    tests = [
        ("Environment File Loading", test_env_file_loading),
        ("Default Password Detection", test_default_password_detection), 
        ("Password Strength", test_password_strength),
        ("Docker Compose Configuration", test_docker_compose_functionality),
        ("Git Ignore Protection", test_gitignore_protection)
    ]
    
    results = []
    
    for test_name, test_func in tests:
        try:
            result = test_func()
            results.append((test_name, result))
        except Exception as e:
            print(f"  💥 Test '{test_name}' crashed: {e}")
            results.append((test_name, False))
    
    # Generate sample passwords
    generate_secure_passwords()
    
    # Print results summary
    print("\n📊 Test Results Summary:")
    print("="*30)
    
    passed = 0
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"  {test_name:<30}: {status}")
        if result:
            passed += 1
    
    print("="*30)
    print(f"  Total: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 All secrets management tests PASSED!")
    else:
        print(f"\n⚠️  {total - passed} test(s) FAILED - please review the issues above")
    
    # Always print security recommendations
    print_security_recommendations()
    
    print("\n📋 Next Steps:")
    print("1. Copy .env.example to .env")
    print("2. Replace all CHANGEME passwords with secure values")  
    print("3. Test your configuration with: docker-compose config")
    print("4. Start services with: docker-compose --env-file .env up -d")
    
    return passed == total


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)