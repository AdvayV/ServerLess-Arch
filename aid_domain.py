"""Immutable component identities and illustrative financial-aid checks.

All records and rules are synthetic. Passing these checks is an input to staff
review, never an award decision or a claim about an institution's policy.
"""

from dataclasses import asdict, dataclass

from simulation_utils import require_integer


CHECKS = ("academic", "income", "evidence")
CHECKER_VERSION = "demo-checker-v1"
DEFAULT_RULES = {"academic": 800, "income": 300_000, "evidence": True}
DEFAULT_COSTS = {"academic": 40, "income": 100, "evidence": 60}


def validate_value(check: str, value: object) -> None:
    if check not in CHECKS:
        raise ValueError(f"Unknown check: {check}")
    if check == "evidence":
        if type(value) is not bool:
            raise ValueError("Evidence must be a boolean")
    else:
        require_integer(value, check)
        if check == "academic" and value > 1000:
            raise ValueError("Academic value must be CGPA in hundredths, from 0 to 1000")


@dataclass(frozen=True, order=True)
class ComponentKey:
    student: str
    check: str
    record_version: int
    rule_version: int
    application: str = "synthetic-aid-review"
    checker_version: str = CHECKER_VERSION

    def public(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    observed: int | bool
    requirement: int | bool
    explanation: str

    def public(self) -> dict:
        return asdict(self)


def evaluate_check(check: str, observed: int | bool, requirement: int | bool) -> CheckResult:
    """Compute a value from the immutable inputs captured when a job is made."""
    if check == "academic":
        passed = observed >= requirement
        explanation = f"CGPA {observed / 100:.2f}; demo minimum {requirement / 100:.2f}."
    elif check == "income":
        passed = observed <= requirement
        explanation = f"Annual household income INR {observed:,}; demo maximum INR {requirement:,}."
    elif check == "evidence":
        passed = observed == requirement
        explanation = f"Evidence verified: {'yes' if observed else 'no'}; required: {'yes' if requirement else 'no'}."
    else:
        raise ValueError(f"Unknown check: {check}")
    return CheckResult(passed, observed, requirement, explanation)


def reference_evaluate(record: dict, rules: dict) -> dict:
    """Uncached oracle, independent of the component evaluator and job system."""
    answers = {
        "academic": record["academic"] >= rules["academic"],
        "income": record["income"] <= rules["income"],
        "evidence": record["evidence"] == rules["evidence"],
    }
    return {"checks": answers, "all_checks_passed": all(answers.values())}
