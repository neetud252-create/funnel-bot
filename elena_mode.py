"""Shared switches for optional Elena automation; manual support is the default."""
import os


def enabled():
    return os.getenv('ELENA_ENABLED', '0').lower() in ('1', 'true', 'yes', 'on')


def activation_only():
    # Default to activation only, including when no deployment variable is set.
    return os.getenv('ELENA_ACTIVATION_ONLY', '1').lower() in ('1', 'true', 'yes', 'on')
