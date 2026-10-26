"""Shared switch for Elena's activation-only operation."""
import os


def activation_only():
    # Default to activation only, including when no deployment variable is set.
    return os.getenv('ELENA_ACTIVATION_ONLY', '1').lower() in ('1', 'true', 'yes', 'on')
