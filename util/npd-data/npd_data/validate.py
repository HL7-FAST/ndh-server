"""HAPI validator CLI wrapper.

Requires Java 17+ and network access to tx.fhir.org. The IG package is
supplied as a downloaded package.tgz path.
"""

import collections
import logging
import re
import subprocess

log = logging.getLogger(__name__)

_ERROR_LINE = re.compile(r", (?:Error|Fatal) - (.+)$")


def summarize_validation(report):
    """Parse an eslint-compact report into (total_errors, [(template, count)]).

    Each error message is reduced to a template by replacing its variable
    parts (quoted values, URLs, numbers) so similar errors group together,
    e.g. many "Unknown code 'X' in the CodeSystem 'Y'" collapse into one row.
    """
    counts = collections.Counter()
    for line in report.splitlines():
        match = _ERROR_LINE.search(line)
        if match:
            counts[_categorize(match.group(1))] += 1
    return sum(counts.values()), counts.most_common()


def _categorize(message):
    m = re.sub(r"'[^']*'", "'_'", message)
    m = re.sub(r'"[^"]*"', '"_"', m)
    m = re.sub(r"https?://[^\s)]+", "_url_", m)
    m = re.sub(r"\d+", "N", m)
    return re.sub(r"\s+", " ", m).strip()


def run_validator(
    targets, ig_package, validator_jar=None, java="java", html_output=None, compact_output=None
):
    """Return (success, output).

    Writes an HTML report to html_output and an eslint-compact text report to
    compact_output when given.
    """
    targets = [str(target) for target in targets]
    command = [
        java,
        "-jar",
        str(validator_jar),
        "-version",
        "4.0.1",
        "-ig",
        str(ig_package),
        # Sample data uses example domains.
        "-allow-example-urls",
        "true",
    ]
    if html_output:
        command += ["-html-output", str(html_output)]
    if compact_output:
        command += ["-output", str(compact_output), "-output-style", "eslint-compact"]
    command += targets
    log.info("validating %d paths", len(targets))
    completed = subprocess.run(command, capture_output=True, text=True)
    return completed.returncode == 0, completed.stdout + completed.stderr
