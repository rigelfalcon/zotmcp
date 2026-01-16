"""Utility functions for ZotMCP."""

import os
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def get_zotero_base_attachment_path() -> Optional[str]:
    """Auto-detect Zotero's linked attachment base directory from prefs.js."""
    # Common Zotero profile locations
    if os.name == 'nt':  # Windows
        profile_base = os.path.expandvars(r'%APPDATA%\Zotero\Zotero\Profiles')
    else:  # macOS/Linux
        profile_base = os.path.expanduser('~/Library/Application Support/Zotero/Profiles')
        if not os.path.exists(profile_base):
            profile_base = os.path.expanduser('~/.zotero/zotero')
    
    if not os.path.exists(profile_base):
        return None
    
    # Find prefs.js in profile directory
    for root, dirs, files in os.walk(profile_base):
        if 'prefs.js' in files:
            prefs_path = os.path.join(root, 'prefs.js')
            try:
                with open(prefs_path, 'r', encoding='utf-8', errors='ignore') as f:
                    for line in f:
                        # Look for baseAttachmentPath setting
                        match = re.search(
                            r'user_pref\("extensions\.zotero\.baseAttachmentPath",\s*"([^"]+)"\)',
                            line
                        )
                        if match:
                            path = match.group(1).replace('\\', os.sep)
                            if os.path.exists(path):
                                logger.info(f"Auto-detected linked attachment base: {path}")
                                return path
            except Exception as e:
                logger.warning(f"Failed to read prefs.js: {e}")
    
    return None
