"""Tests for ct_configrecorder_override Lambda function."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from ct_configrecorder_override import should_process_account


def test_exclusion_mode_excludes_listed_account():
    assert should_process_account('111111111111', 'EXCLUSION', ['111111111111'], []) is False


def test_exclusion_mode_includes_unlisted_account():
    assert should_process_account('999999999999', 'EXCLUSION', ['111111111111'], []) is True


def test_inclusion_mode_includes_listed_account():
    assert should_process_account('111111111111', 'INCLUSION', [], ['111111111111']) is True


def test_inclusion_mode_excludes_unlisted_account():
    assert should_process_account('999999999999', 'INCLUSION', [], ['111111111111']) is False
