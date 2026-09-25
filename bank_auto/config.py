# Bank Automation Tool — OFX-Based Personal Banking CLI
# Supports PNC, Huntington, SecurTrust
# All credentials stored encrypted locally via OS keyring

"""
Core configuration for supported banks.
OFX server URLs, org IDs, and FID values sourced from ofxhome.com.
"""

BANK_PROFILES = {
    "pnc": {
        "name": "PNC Bank",
        "ofx_url": "https://www.oasis.cfree.com/test.ofxgp",
        "org": "4901",
        "fid": "4901",
        "broker_id": "",
        "app_id": "QWIN",
        "app_ver": "2700",
        "ofx_version": 103,
        "supports_ach": False,
        "supports_billpay": True,
        "supports_wire": False,
    },
    "huntington": {
        "name": "Huntington National Bank",
        "ofx_url": "https://onlinebanking.huntington.com/scripts/serverext.dll",
        "org": "Huntington",
        "fid": "5307",
        "broker_id": "",
        "app_id": "QWIN",
        "app_ver": "2700",
        "ofx_version": 103,
        "supports_ach": True,
        "supports_wire": True,
        "supports_billpay": True,
    },
    "securtrust": {
        "name": "SecurTrust Federal Credit Union",
        "ofx_url": "https://ofx.securtrust.org",
        "org": "SecurTrust FCU",
        "fid": "2101",
        "broker_id": "",
        "app_id": "QWIN",
        "app_ver": "2700",
        "ofx_version": 103,
        "supports_ach": True,
        "supports_wire": False,
        "supports_billpay": True,
    },
}

# Keyring service name for credential storage
KEYRING_SERVICE = "bank_auto_tool"

# Local encrypted config path
CONFIG_DIR_NAME = ".bank_auto"
