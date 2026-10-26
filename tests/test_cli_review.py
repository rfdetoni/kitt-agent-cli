"""Review subcommand exposes explicit, non-default write confirmation."""
from kitt.cli.main import build_parser


def test_review_cli_operations_require_explicit_action():
    parser = build_parser()
    parsed = parser.parse_args(["review", "diff", "review_" + "a" * 32])
    assert parsed.review_action == "diff"
    assert parsed.yes is False
    write = parser.parse_args(["review", "apply", "review_" + "a" * 32, "--yes"])
    assert write.review_action == "apply"
    assert write.yes is True
    pending = parser.parse_args(["review", "list", "--conversation-id", "a"])
    assert pending.review_action == "list"
    assert pending.conversation_id == "a"
