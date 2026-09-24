import re
from collections import deque

LEVELS = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']

# Matches the format set in core/logging.py:
#   2026-09-23 21:25:13,926 - services.discovery - INFO - message
_ENTRY_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3} - .*? - '
    r'(DEBUG|INFO|WARNING|ERROR|CRITICAL) - '
)


def read_log_tail(path, max_lines=500, min_level=None):
    """Return the last max_lines lines of a log file, oldest first.

    With min_level, only entries at that level or above are kept. Lines that
    don't start a new entry (tracebacks, multi-line messages) belong to the
    entry above them and are kept or dropped with it.

    The file is streamed, so memory is bounded by max_lines rather than the
    file size. A missing file yields an empty list.
    """
    threshold = LEVELS.index(min_level) if min_level in LEVELS else 0
    tail = deque(maxlen=max_lines)
    keep = threshold == 0  # continuation lines before any entry: keep only if unfiltered

    try:
        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            for line in f:
                match = _ENTRY_RE.match(line)
                if match:
                    keep = LEVELS.index(match.group(1)) >= threshold
                if keep:
                    tail.append(line.rstrip('\n'))
    except FileNotFoundError:
        return []

    return list(tail)
