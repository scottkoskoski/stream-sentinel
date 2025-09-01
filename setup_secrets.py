#!/usr/bin/env python3
"""
Stream-Sentinel Secrets Setup Script

This script helps you quickly set up secure secrets management for Stream-Sentinel.
It generates secure passwords and creates your .env file with all required configuration.

Usage:
    python setup_secrets.py

The script will:
1. Check if .env already exists (and ask before overwriting)
2. Generate secure passwords for all services
3. Create a properly configured .env file
4. Validate the configuration
5. Provide next steps
"""

import os
import secrets
import string
from pathlib import Path
from typing import Dict, Any


def generate_secure_password(length: int = 32) -> str:
    """Generate a cryptographically secure password."""
    # Use letters, digits, and safe special characters
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def generate_all_passwords() -> Dict[str, str]:
    """Generate all required passwords for Stream-Sentinel."""
    return {
        # Environment
        'STREAM_SENTINEL_ENV': 'development',
        'APP_VERSION': '1.0.0',
        'LOG_LEVEL': 'INFO',
        
        # Database passwords (32+ characters)
        'POSTGRES_PASSWORD': generate_secure_password(32),
        'REDIS_PASSWORD': generate_secure_password(32),
        'CLICKHOUSE_PASSWORD': generate_secure_password(32),
        
        # Service authentication
        'KAFKA_UI_USERNAME': 'admin',
        'KAFKA_UI_PASSWORD': generate_secure_password(32),
        'SCHEMA_REGISTRY_USERNAME': 'schema_registry_user',
        'SCHEMA_REGISTRY_PASSWORD': generate_secure_password(32),
        
        # Kafka authentication  
        'KAFKA_USERNAME': 'stream_sentinel_user',
        'KAFKA_PASSWORD': generate_secure_password(32),
        
        # SSL/TLS passwords (16+ characters for certificates)
        'SSL_TRUSTSTORE_PASSWORD': generate_secure_password(16),
        'SSL_KEYSTORE_PASSWORD': generate_secure_password(16),
        'SSL_KEY_PASSWORD': generate_secure_password(16),
        
        # Application secrets (64+ characters for JWT)
        'AUTH_SECRET_KEY': generate_secure_password(64),
        
        # Security settings
        'SSL_ENABLED': 'false',
        'AUTH_ENABLED': 'false',
        
        # Development settings
        'DEBUG_MODE': 'false',
        'DEVELOPMENT_MODE': 'true',
    }


def create_env_file(passwords: Dict[str, str], env_path: Path) -> None:
    """Create a .env file with the generated passwords."""
    
    env_content = """# Stream-Sentinel Environment Configuration
# Generated automatically - DO NOT commit this file to version control
# 
# This file contains sensitive passwords and secrets.
# Keep it secure and never share it publicly.

# =============================================================================
# ENVIRONMENT CONFIGURATION
# =============================================================================

STREAM_SENTINEL_ENV={STREAM_SENTINEL_ENV}
APP_VERSION={APP_VERSION}
LOG_LEVEL={LOG_LEVEL}

# =============================================================================
# DATABASE PASSWORDS
# =============================================================================

POSTGRES_PASSWORD={POSTGRES_PASSWORD}
REDIS_PASSWORD={REDIS_PASSWORD}
CLICKHOUSE_PASSWORD={CLICKHOUSE_PASSWORD}

# =============================================================================
# SERVICE AUTHENTICATION  
# =============================================================================

KAFKA_UI_USERNAME={KAFKA_UI_USERNAME}
KAFKA_UI_PASSWORD={KAFKA_UI_PASSWORD}
SCHEMA_REGISTRY_USERNAME={SCHEMA_REGISTRY_USERNAME}
SCHEMA_REGISTRY_PASSWORD={SCHEMA_REGISTRY_PASSWORD}

# =============================================================================
# KAFKA CONFIGURATION
# =============================================================================

KAFKA_USERNAME={KAFKA_USERNAME}
KAFKA_PASSWORD={KAFKA_PASSWORD}
KAFKA_BOOTSTRAP_SERVERS=localhost:9092

# =============================================================================
# SSL/TLS CONFIGURATION
# =============================================================================

SSL_ENABLED={SSL_ENABLED}
SSL_TRUSTSTORE_PASSWORD={SSL_TRUSTSTORE_PASSWORD}
SSL_KEYSTORE_PASSWORD={SSL_KEYSTORE_PASSWORD}
SSL_KEY_PASSWORD={SSL_KEY_PASSWORD}

# =============================================================================
# APPLICATION SECURITY
# =============================================================================

AUTH_ENABLED={AUTH_ENABLED}
AUTH_SECRET_KEY={AUTH_SECRET_KEY}

# =============================================================================
# DEVELOPMENT SETTINGS
# =============================================================================

DEBUG_MODE={DEBUG_MODE}
DEVELOPMENT_MODE={DEVELOPMENT_MODE}

# =============================================================================
# FRAUD DETECTION CONFIGURATION
# =============================================================================

FRAUD_THRESHOLD=0.7
FRAUD_MODEL_CPP_ACCELERATION=true

# =============================================================================
# MONITORING SETTINGS
# =============================================================================

PROMETHEUS_ENABLED=true
MONITORING_ENABLED=true

# =============================================================================
# PERFORMANCE TUNING
# =============================================================================

KAFKA_CONSUMER_THREADS=4
WORKER_THREADS=8

# =============================================================================
# SECURITY NOTES
# =============================================================================
#
# 1. This file contains sensitive passwords - never commit it to git
# 2. For production, use external secrets management (Vault, K8s secrets)
# 3. Rotate passwords regularly
# 4. Enable SSL/TLS for production deployments
# 5. Use strong firewall rules and network segmentation
#
# =============================================================================

""".format(**passwords)
    
    env_path.write_text(env_content)


def main():
    """Main setup function."""
    print("Stream-Sentinel Secrets Setup")
    print("=" * 40)
    
    project_root = Path(__file__).parent
    env_path = project_root / '.env'
    env_example_path = project_root / '.env.example'
    
    # Check if .env already exists
    if env_path.exists():
        print(f"WARNING: .env file already exists at {env_path}")
        response = input("Do you want to overwrite it? (y/N): ").lower().strip()
        if response not in ['y', 'yes']:
            print("Setup cancelled. Existing .env file preserved.")
            return
    
    # Check if .env.example exists
    if not env_example_path.exists():
        print(f"WARNING: .env.example not found at {env_example_path}")
        print("This might indicate you're not in the Stream-Sentinel project directory.")
        response = input("Continue anyway? (y/N): ").lower().strip()
        if response not in ['y', 'yes']:
            print("Setup cancelled.")
            return
    
    print("Generating secure passwords...")
    passwords = generate_all_passwords()
    
    print("Creating .env file...")
    create_env_file(passwords, env_path)
    
    print("Setting secure file permissions...")
    os.chmod(env_path, 0o600)  # Read/write for owner only
    
    print("SUCCESS: .env file created successfully!")
    print()
    
    # Display important passwords
    print("Generated passwords:")
    print("-" * 30)
    important_passwords = [
        'POSTGRES_PASSWORD',
        'REDIS_PASSWORD', 
        'CLICKHOUSE_PASSWORD',
        'KAFKA_UI_PASSWORD'
    ]
    
    for key in important_passwords:
        value = passwords[key]
        masked_value = value[:8] + "..." + value[-8:]
        print(f"  {key}: {masked_value}")
    
    print("-" * 30)
    print("Full credentials saved to .env file")
    print()
    
    # Validation
    print("Validating configuration...")
    try:
        # Test that we can run docker-compose config
        import subprocess
        result = subprocess.run(
            ['docker-compose', '--env-file', str(env_path), 'config', '--quiet'],
            cwd=project_root / 'docker',
            capture_output=True
        )
        
        if result.returncode == 0:
            print("SUCCESS: Docker Compose configuration is valid")
        else:
            print("WARNING: Docker Compose validation warning (this is usually OK)")
            
    except FileNotFoundError:
        print("INFO: Docker Compose not found - skipping validation")
    
    print()
    print("Next Steps:")
    print("1. Review your .env file (but don't share it!)") 
    print("2. Start the services:")
    print("   cd docker && docker-compose --env-file ../.env up -d")
    print("3. Verify services are running:")
    print("   docker-compose ps")
    print("4. Test the system:")
    print("   python test_secrets_management.py")
    print()
    print("Security Reminders:")
    print("* Never commit .env to version control")
    print("* Use different passwords for production")
    print("* Enable SSL/TLS for production deployments")
    print("* Rotate passwords regularly")
    print("* Monitor for security events")
    print()
    print("For more information, see SECRETS_MANAGEMENT.md")


if __name__ == '__main__':
    main()