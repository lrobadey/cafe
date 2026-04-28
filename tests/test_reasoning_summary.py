import unittest
from types import SimpleNamespace

from reasoning_summary import extract_reasoning_summary_text


class ReasoningSummaryTests(unittest.TestCase):
    def test_extracts_summary_text_from_dict_response(self):
        response = {
            "output": [
                {
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "Thinking about the order."},
                        {"type": "summary_text", "text": "Choosing the fastest path."},
                    ],
                },
                {"type": "message", "content": []},
            ]
        }

        self.assertEqual(
            extract_reasoning_summary_text(response),
            "Thinking about the order.\n\nChoosing the fastest path.",
        )

    def test_extracts_summary_text_from_object_response(self):
        response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="reasoning",
                    summary=[SimpleNamespace(type="summary_text", text="Queue is empty, so idle.")],
                )
            ]
        )

        self.assertEqual(extract_reasoning_summary_text(response), "Queue is empty, so idle.")

    def test_returns_empty_string_when_no_summary_exists(self):
        response = {"output": [{"type": "message", "content": []}]}

        self.assertEqual(extract_reasoning_summary_text(response), "")


if __name__ == "__main__":
    unittest.main()
