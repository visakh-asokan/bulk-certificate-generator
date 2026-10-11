"""Row-by-row validation of recipients.

Each recipient gets its own verdict. A bad row never causes the request to be
rejected as a whole; it is reported back and the valid rows carry on.
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from pydantic import ValidationError

from app.schemas import RecipientIn


@dataclass
class RecipientResult:
    row: int  # 1-based position in the request
    name: Optional[str]
    email: Optional[str]
    errors: List[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors


def _as_text(value: Any) -> Optional[str]:
    """Keep whatever the client sent (shortened) so the error report is useful."""
    return None if value is None else str(value)[:255]


def _error_messages(exc: ValidationError) -> List[str]:
    messages = []
    for error in exc.errors():
        field_name = ".".join(str(part) for part in error["loc"]) or "recipient"
        message = error["msg"].replace("Value error, ", "")
        messages.append(f"{field_name}: {message}")
    return messages

def validate_recipients(raw_recipients: List[Any]) -> List[RecipientResult]:
    """Return one result per input row, in the same order."""
    results: List[RecipientResult] = []
    first_row_with_email: Dict[str, int] = {}

    for row, raw in enumerate(raw_recipients, start=1):
        if not isinstance(raw, dict):
            results.append(
                RecipientResult(row, None, None, ["recipient must be an object with 'name' and 'email'"])
            )
            continue

        try:
            recipient = RecipientIn.model_validate(raw)
        except ValidationError as exc:
            results.append(
                RecipientResult(row, _as_text(raw.get("name")), _as_text(raw.get("email")), _error_messages(exc))
            )
            continue

        # The same email twice in one request is almost always a copy-paste
        # mistake in the spreadsheet, so only the first occurrence is accepted.
        email_key = recipient.email.lower()
        if email_key in first_row_with_email:
            results.append(
                RecipientResult(
                    row,
                    recipient.name,
                    recipient.email,
                    [f"email: duplicate of row {first_row_with_email[email_key]} in this request"],
                )
            )
            continue

        first_row_with_email[email_key] = row
        results.append(RecipientResult(row, recipient.name, recipient.email))

    return results